"""Independent, forecast-only common lineup policies for controlled evaluation.

Selection never sees actual opponent rosters or hidden outcomes. The final audit
score remains in independent.score_matchup; this module chooses plans, not truth.
"""
from __future__ import annotations
from bisect import bisect_left, bisect_right
from decimal import Decimal, ROUND_HALF_EVEN
import itertools
import math
from .independent import lineup_plan, validate_audit_scope, sum_plan

POLICIES=('ADDITIVE','CATEGORY_EXPECTED')

class ForecastManager:
    def __init__(self,data,max_plans=200000):
        validate_audit_scope(data)
        if data['manager_profile']['category_preferences']:
            raise ValueError('Common research manager supports STANDARD categories only.')
        if type(max_plans) is not int or max_plans<1:raise ValueError('Positive plan budget required.')
        self.data=data;self.max_plans=max_plans;self.cache={};self.plans_examined=0
        self.lp={p['player_id']:p for p in data['league_players']}
        self.groups={}
        for p in sorted(data['exposure_model']['periods'],key=lambda x:(x['starts_at'],x['period_id'])):
            self.groups.setdefault(p['lock_group_id'],[]).append(p['period_id'])
        self.slots=[(f'{s["slot_definition_id"]}#{i+1}',s) for s in sorted(data['league']['roster_slots'],key=lambda s:s['slot_definition_id']) if s['slot_class']=='ACTIVE' for i in range(s['capacity'])]
        self.categories=sorted(data['league']['categories'],key=lambda c:c['category_id'])
        self.scenarios=sorted(data['exposure_model']['forecast_scenarios'],key=lambda s:s['scenario_id'])
        self.field=data['reference_model']['field']
        self.weight=math.fsum(r['weight'] for r in self.field)
        self.tables={};self.credit_cache={}
        for c in self.categories:
            by_value={};missing=0.
            for row in self.field:
                val=self._value(c,row['stat_values'])
                if val is None:missing+=row['weight']
                else:by_value.setdefault(val,[]).append(row['weight'])
            thresholds=sorted(by_value);weights=[math.fsum(by_value[v]) for v in thresholds]
            totals=[math.fsum(weights[:i]) for i in range(len(weights)+1)]
            self.tables[c['category_id']]=(thresholds,totals,missing)

    @staticmethod
    def _value(c,stats):
        calc=c['calculation']
        if calc['type']=='COUNT':value=stats[calc['stat_key']]
        else:
            den=stats[calc['denominator_stat_key']]
            if den==0:return None
            value=calc['multiplier']*stats[calc['numerator_stat_key']]/den
        return Decimal(str(value)).quantize(Decimal(1).scaleb(-c['precision']),rounding=ROUND_HALF_EVEN)

    def _score(self,stats):
        out=[];objective=self.data['league']['scoring_objective']
        for c in self.categories:
            value=self._value(c,stats);key=(c['category_id'],value)
            if key not in self.credit_cache:
                thresholds,prefix,missing=self.tables[c['category_id']]
                if value is None:total=missing*objective['both_undefined_tie_credit']
                else:
                    lo=bisect_left(thresholds,value);hi=bisect_right(thresholds,value)
                    wins=prefix[lo] if c['direction']=='HIGHER_IS_BETTER' else prefix[-1]-prefix[hi]
                    total=missing+wins+(prefix[hi]-prefix[lo])*objective['category_tie_credit']
                self.credit_cache[key]=total/self.weight
            out.append(c['weight']*self.credit_cache[key])
        return math.fsum(out)/math.fsum(c['weight'] for c in self.categories)

    def _options(self,roster,group):
        periods=self.groups[group]
        players=[p for p in sorted(roster) if any(s['player_period_stats'][p][t]['games']>0 for s in self.scenarios for t in periods)]
        result={};nodes=0
        def walk(i,used,rows):
            nonlocal nodes
            nodes+=1
            if nodes>self.max_plans*max(1,len(self.slots))*20:
                raise ValueError('Independent forecast-manager matching budget exceeded; no partial plan returned.')
            if i==len(self.slots):
                k=tuple(sorted(used));v=tuple((r['slot_instance_id'],r['player_id']) for r in rows)
                if k not in result or v<result[k][0]:result[k]=(v,list(rows))
                return
            sid,slot=self.slots[i]
            walk(i+1,used,rows)
            for p in players:
                if p in used:continue
                elig=set(self.lp[p]['eligibility']['position_ids'])
                if slot['eligibility']['mode']=='POSITION_SET':elig &= set(slot['eligibility']['position_ids'])
                if elig:walk(i+1,used|{p},rows+[{'player_id':p,'slot_instance_id':sid}])
        walk(0,set(),[])
        return [result[k][1] for k in sorted(result)]

    def choose(self,roster,policy):
        if policy not in POLICIES:raise ValueError('Unknown common lineup policy.')
        ids=tuple(sorted(roster));key=(ids,policy)
        if key in self.cache:return self.cache[key]
        if len(ids)!=len(set(ids)) or not set(ids)<=set(self.lp):raise ValueError('Roster IDs must be known and unique.')
        if policy=='ADDITIVE':
            plan=lineup_plan(self.data,ids)
            self.cache[key]=plan
            return plan
        groups=list(self.groups);options=[self._options(ids,g) for g in groups]
        nplans=math.prod(len(x) for x in options)
        if nplans>self.max_plans:raise ValueError('Independent forecast-manager plan budget exceeded; no partial plan returned.')
        best=None;best_key=None
        for rows in itertools.product(*options):
            plan=dict(zip(groups,rows));scores=[];games=[]
            for s in self.scenarios:
                totals=sum_plan(self.data,plan,s['player_period_stats'])
                scores.append(s['probability']*self._score(totals));games.append(s['probability']*totals['games'])
            # Optimize once over expected scenarios, never separately with hindsight.
            k=(-math.fsum(scores),-math.fsum(games),tuple((g,tuple((r['slot_instance_id'],r['player_id']) for r in plan[g])) for g in groups))
            if best_key is None or k<best_key:best_key=k;best=plan
            self.plans_examined+=1
        self.cache[key]=best
        return best
