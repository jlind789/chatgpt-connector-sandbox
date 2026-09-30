"""Fixed intrinsic normalizer and exhaustive, forecast-only lineup/team evaluator."""
from __future__ import annotations
import itertools
import math
from decimal import Decimal, ROUND_HALF_EVEN
from .validation import Budget, applies

class IntrinsicModel:
    def __init__(self,data):
        self.data=data;self.rows={};self.anchors={};self.warnings=[]
        lp={p['player_id']:p for p in data['league_players']};positions={p['position_id']:p for p in data['league']['positions']}
        projections={p['player_id']:p['stat_values'] for p in data['projection_set']['player_projections']}
        cohort=sorted(data['reference_model']['normalization_player_ids'])
        cats=sorted(data['league']['categories'],key=lambda c:c['category_id'])
        for c in cats:
            ids=[p for p in cohort if applies(c,p,lp,positions)];calc=c['calculation'];rate=None
            if calc['type']=='RATIO':
                den=math.fsum(projections[p][calc['denominator_stat_key']] for p in ids)
                rate=math.fsum(projections[p][calc['numerator_stat_key']] for p in ids)/den if den else None
            def feature(p):
                if calc['type']=='COUNT':return projections[p][calc['stat_key']]
                return 0.0 if rate is None else projections[p][calc['numerator_stat_key']]-rate*projections[p][calc['denominator_stat_key']]
            features=[feature(p) for p in ids];mean=math.fsum(features)/len(features);sd=math.sqrt(math.fsum((x-mean)**2 for x in features)/len(features))
            self.anchors[c['category_id']]={'cohort_player_ids':ids,'mean':mean,'standard_deviation':sd,'pooled_reference_rate':rate}
            if sd==0:self.warnings.append({'severity':'WARNING','code':'CONSTANT_CATEGORY_NORMALIZER','message':f'{c["category_id"]} has zero differentiating intrinsic scale; official scoring is retained.'})
            direction=1 if c['direction']=='HIGHER_IS_BETTER' else -1
            for p in sorted(projections):
                row=self.rows.setdefault(p,{'intrinsic_value':0.0,'category_components':[]})
                applicable=applies(c,p,lp,positions);z=direction*(feature(p)-mean)/sd if applicable and sd else 0.0
                raw=None
                if applicable:
                    raw=projections[p][calc['stat_key']] if calc['type']=='COUNT' else (calc['multiplier']*projections[p][calc['numerator_stat_key']]/projections[p][calc['denominator_stat_key']] if projections[p][calc['denominator_stat_key']] else None)
                row['category_components'].append({'category_id':c['category_id'],'applicable':applicable,'raw_projection':raw,'normalized_contribution':z,'weighted_contribution':c['weight']*z})
        for row in self.rows.values():row['intrinsic_value']=math.fsum(x['weighted_contribution'] for x in row['category_components'])
    def value(self,p):return self.rows[p]['intrinsic_value']


def category_value(c,stats):
    calculation=c['calculation']
    if calculation['type']=='COUNT':return stats.get(calculation['stat_key'],0.0)
    denominator=stats.get(calculation['denominator_stat_key'],0.0)
    return None if denominator==0 else calculation['multiplier']*stats.get(calculation['numerator_stat_key'],0.0)/denominator


def rounded(x,precision):
    return Decimal(str(x)).quantize(Decimal(1).scaleb(-precision),rounding=ROUND_HALF_EVEN)

class TeamEvaluator:
    def __init__(self,data,solver):
        self.data=data;self.solver=solver;self.cache={};self.plan_cache={}
        self.categories=sorted(data['league']['categories'],key=lambda c:c['category_id'])
        self.scenarios=sorted(data['exposure_model']['forecast_scenarios'],key=lambda s:s['scenario_id'])
        self.field=sorted(data['reference_model']['field'],key=lambda f:f['reference_id'])
        self.field_weight=math.fsum(f['weight'] for f in self.field)
        self.stats=sorted(s['stat_key'] for s in data['league']['stat_definitions'])
        self.exposure_key=data['exposure_model']['exposure_stat_key']
        self.groups={}
        for p in sorted(data['exposure_model']['periods'],key=lambda p:(p['starts_at'],p['period_id'])):
            self.groups.setdefault(p['lock_group_id'],[]).append(p['period_id'])
        self.pref={p['category_id']:p for p in data['manager_profile']['category_preferences']}
        self.rules=data['league']['lineup_rules'];self.objective=data['league']['scoring_objective']
        self.active_slots={k:s for k,s in solver.slots.items() if s['slot_class']=='ACTIVE'}
        self.weights={c['category_id']:c['weight']*self.multiplier(c['category_id']) for c in self.categories}
        self.maxplans=data['methodology_config']['max_lineup_plans']
        self.evaluated_plans=0

    def multiplier(self,cid):
        p=self.pref.get(cid,{'mode':'STANDARD'})
        return 0 if p['mode']=='PUNT' else p.get('multiplier',1)

    def failed_categories(self,stats):
        return set().union(*(set(r['failed_category_ids']) for r in self.rules['minimum_participation'] if stats.get(r['stat_key'],0)<r['minimum'])) if self.rules['minimum_participation'] else set()

    def category_credit(self,c,stats,reference):
        cid=c['category_id']
        if cid in self.failed_categories(stats):return 0.0
        if cid in self.failed_categories(reference):return 1.0
        a,b=category_value(c,stats),category_value(c,reference)
        if a is None and b is None:return self.objective['both_undefined_tie_credit']
        if a is None:return 0.0
        if b is None:return 1.0
        a,b=rounded(a,c['precision']),rounded(b,c['precision'])
        if a==b:return self.objective['category_tie_credit']
        return float((a>b) if c['direction']=='HIGHER_IS_BETTER' else (a<b))

    def score_stats(self,stats):
        credits={c['category_id']:math.fsum(f['weight']*self.category_credit(c,stats,f['stat_values']) for f in self.field)/self.field_weight for c in self.categories}
        league=math.fsum(c['weight']*credits[c['category_id']] for c in self.categories)/math.fsum(c['weight'] for c in self.categories)
        strategy=math.fsum(self.weights[cid]*credits[cid] for cid in sorted(credits))/math.fsum(self.weights.values())
        return league,strategy,credits

    def group_plans(self,playable,group):
        """Enumerate distinct active player sets, retaining a canonical legal global matching."""
        key=(tuple(sorted(playable)),group)
        if key in self.plan_cache:return self.plan_cache[key]
        periods=self.groups[group]
        eligible=[p for p in sorted(playable) if any(s['player_period_stats'][p][t][self.exposure_key]>0 for s in self.scenarios for t in periods)]
        options={p:[a for a in self.solver.options(p,locked=False) if a.slot_instance_id in self.active_slots] for p in eligible}
        budget=Budget(self.data['methodology_config']['max_search_nodes'],'active lineup matching');by_players={}
        def walk(i,used,assignment):
            budget.tick()
            if i==len(eligible):
                chosen=tuple(sorted(a.player_id for a in assignment));akey=self.solver.key(assignment)
                if chosen not in by_players or akey<self.solver.key(by_players[chosen]):by_players[chosen]=tuple(assignment)
                return
            pid=eligible[i];walk(i+1,used,assignment)
            for a in options[pid]:
                if a.slot_instance_id not in used:walk(i+1,used|{a.slot_instance_id},assignment+[a])
        walk(0,set(),[])
        result=[by_players[k] for k in sorted(by_players)]
        self.plan_cache[key]=result
        return result

    def stats_for_plan(self,plan,scenario):
        parts={stat:[] for stat in self.stats}
        for group in self.groups:
            for a in sorted(plan[group],key=lambda x:x.player_id):
                for period in self.groups[group]:
                    values=scenario['player_period_stats'][a.player_id][period]
                    for stat,value in values.items():parts[stat].append(value)
        return {stat:math.fsum(values) for stat,values in parts.items()}

    def evaluate_fixed_plan(self,plan,scenarios=None):
        scenarios=self.scenarios if scenarios is None else scenarios
        rows=[]
        for s in sorted(scenarios,key=lambda x:x['scenario_id']):
            stats=self.stats_for_plan(plan,s);league,strategy,credit=self.score_stats(stats)
            rows.append({'probability':s['probability'],'stats':stats,'league':league,'strategy':strategy,'category_credit':credit})
        result={'league_utility':math.fsum(r['probability']*r['league'] for r in rows),'strategy_utility':math.fsum(r['probability']*r['strategy'] for r in rows),'expected_usable_stats':{stat:math.fsum(r['probability']*r['stats'][stat] for r in rows) for stat in self.stats},'category_results':[]}
        for c in self.categories:
            cid=c['category_id'];values=[(r['probability'],category_value(c,r['stats'])) for r in rows];defined=math.fsum(w for w,v in values if v is not None)
            result['category_results'].append({'category_id':cid,'expected_result_credit':math.fsum(r['probability']*r['category_credit'][cid] for r in rows),'defined_probability':defined,'expected_value_when_defined':math.fsum(w*v for w,v in values if v is not None)/defined if defined else None})
        return result

    def evaluate(self,owned,terminal=True):
        key=(tuple(sorted(owned)),terminal)
        if key in self.cache:return self.cache[key]
        constructions=self.solver.all(owned,terminal,True)
        if not constructions:return None
        # Storage class changes playability; optimize that choice jointly with its policy penalty.
        groups={}
        for assignment in constructions:
            playable=tuple(sorted(a.player_id for a in assignment if a.slot_class in ('ACTIVE','BENCH')))
            penalty,effects=self.solver.soft_penalty(assignment)
            if playable not in groups or (penalty,self.solver.key(assignment))<(groups[playable][0],self.solver.key(groups[playable][1])):groups[playable]=(penalty,assignment,effects)
        budget=Budget(self.maxplans,'full-horizon lineup plans');best=None;best_key=None
        for playable,(penalty,construction,effects) in sorted(groups.items()):
            choices=[self.group_plans(playable,g) for g in self.groups]
            for combination in itertools.product(*choices):
                budget.tick();self.evaluated_plans+=1
                plan=dict(zip(self.groups,combination))
                if any(any(self.stats_for_plan(plan,s)[r['stat_key']]>r['maximum'] for r in self.rules['active_limits']) for s in self.scenarios):continue
                evaluated=self.evaluate_fixed_plan(plan)
                utility=evaluated['strategy_utility']-penalty
                plan_key=tuple((g,self.solver.key(plan[g])) for g in self.groups)
                tiebreak=(-utility,-evaluated['expected_usable_stats'][self.exposure_key],self.solver.key(construction),plan_key)
                if best_key is None or tiebreak<best_key:
                    best_key=tiebreak
                    best={**evaluated,'decision_utility':utility,'soft_policy_penalty':penalty,'policy_effects':effects,'construction_assignment':[a.output() for a in construction],'lineup_plan':{g:[a.output() for a in plan[g]] for g in self.groups},'optimizer_status':'EXACT_ENUMERATION_STATIC_INFORMATION','forecast_information_policy':'STATIC_PRE_DRAFT','_plan':plan}
        self.cache[key]=best
        return best
