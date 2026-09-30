"""Native split-horizon season draft research profile.

Season projections drive Intrinsic Value. A separate weighted lineup-exposure
horizon drives roster utility. H2H category credit is computed within each
representative matchup group, then averaged by declared group weights.
"""
from __future__ import annotations
from bisect import bisect_left, bisect_right
import json, math
import numpy as np
from scipy.optimize import linear_sum_assignment
from decision_engine.engine import ResearchRunner, empty_result
from decision_engine.validation import validate, EngineError
from decision_engine.scoring import category_value, rounded
from research.rule_aware import RuleAwareLineupEvaluator, check_rule_scope
from research.efficient_runner import EfficientRunner
from research.hoh_replay_runner import ReplayEfficientRosterSolver

PROFILE_REPRESENTATIVE='HORIZON_SPLIT_REPRESENTATIVE_SEASON_V1'
PROFILE_EXACT='HORIZON_SPLIT_EXACT_CALENDAR_V1'

class WeightedEvaluationGroupLineupEvaluator(RuleAwareLineupEvaluator):
    """Bounded daily lineup search scored as weighted H2H matchup groups."""
    optimizer_status='WEIGHTED_MATCHUP_GROUPS_EXACT_STORAGE_BOUNDED_LINEUP_NO_GLOBAL_UTILITY_BOUND'

    def __init__(self,data,solver,sweeps=1,maximum_storage_patterns=64):
        super().__init__(data,solver,sweeps,maximum_storage_patterns)
        if data['schema_version']!='decision-engine-input/0.3.1':
            raise EngineError('SPLIT_HORIZON_SCHEMA_REQUIRED','Weighted matchup groups require decision-engine-input/0.3.1.',status='UNSUPPORTED_CAPABILITY')
        if self.rules['active_limits'] or self.rules['minimum_participation']:
            raise EngineError('SPLIT_HORIZON_PARTICIPATION_UNSUPPORTED','Weighted matchup-group research does not yet define cross-matchup caps/minima.',status='UNSUPPORTED_CAPABILITY')
        self.eval_groups={x['evaluation_group_id']:x for x in data['exposure_model']['evaluation_groups']}
        self.eval_weights={k:float(v['weight']) for k,v in self.eval_groups.items()}
        self.eval_weight_total=math.fsum(self.eval_weights.values())
        period_eval={p['period_id']:p['evaluation_group_id'] for p in data['exposure_model']['periods']}
        self.lock_to_eval={}
        for lock,periods in self.groups.items():
            ids={period_eval[p] for p in periods}
            if len(ids)!=1:raise EngineError('LOCK_GROUP_CROSSES_EVALUATION_GROUP','One lock group cannot span scoring matchups.')
            self.lock_to_eval[lock]=next(iter(ids))
        self.eval_locks={gid:[] for gid in self.eval_groups}
        for lock,gid in self.lock_to_eval.items():self.eval_locks[gid].append(lock)
        if any(not v for v in self.eval_locks.values()):raise EngineError('EMPTY_EVALUATION_GROUP','Every scoring matchup group needs at least one exposure period.')
        self.group_cdf={};self.group_credit_cache={}
        for gid in sorted(self.eval_groups):
            for c in self.categories:
                cells={};undefined=0.
                for f in self.field:
                    stats=f['evaluation_group_stat_values'][gid]
                    x=category_value(c,stats)
                    if x is None:undefined+=f['weight']
                    else:cells.setdefault(rounded(x,c['precision']),[]).append(f['weight'])
                keys=sorted(cells);weights=[math.fsum(cells[x]) for x in keys]
                prefix=[math.fsum(weights[:i]) for i in range(len(weights)+1)]
                self.group_cdf[(gid,c['category_id'])]=(keys,prefix,undefined)

    def menu(self,owned,g):
        # Exact-calendar optimization: a daily lineup depends only on rostered
        # players whose NHL teams actually play that date.  Cache by that active
        # subset rather than by calendar date.  When every active player fits,
        # the full active set is the only production-relevant choice, so avoid
        # building the wider nonlinear support menu.  This changes no scoring
        # objective and falls back to the same finite weighted-matching menu when
        # congestion creates a real start/sit decision.
        if self.data['capability_profile']!=PROFILE_EXACT or self.rules['active_limits']:
            return super().menu(owned,g)
        E=np.einsum('s,spk->pk',self.probs,self.group_arrays[g])
        active=tuple(sorted(p for p in owned if E[self.pindex[p],self.exposure_index]>0))
        # Include the active players' expected stat vectors so the cache remains
        # correct if a future exact-calendar profile uses opponent/date-varying
        # per-game forecasts instead of the current constant per-game projection.
        signature=tuple((p,tuple(float(x) for x in E[self.pindex[p]])) for p in active)
        key=('EXACT_ACTIVE_SUBSET',signature)
        if key in self.menu_cache:return self.menu_cache[key]
        if not active:
            z=np.zeros((len(self.scenarios),len(self.stats)));result=((),[((),z)]);self.menu_cache[key]=result;return result
        sids=sorted(self.active_slots)
        opts={p:{a.slot_instance_id:a for a in reversed(self.solver.options(p,False)) if a.slot_instance_id in self.active_slots} for p in active}
        # First attempt: all active players fit simultaneously.
        if len(active)<=len(sids):
            cost=np.full((len(sids),len(active)+len(sids)),np.inf);cost[:,len(active):]=0.
            for i,sid in enumerate(sids):
                for j,p in enumerate(active):
                    if sid in opts[p]:cost[i,j]=-1.
            try:rr,cc=linear_sum_assignment(cost);self.search_stats['matching_calls']+=1
            except ValueError:rr=cc=[]
            assignment=tuple(sorted((opts[active[j]][sids[i]] for i,j in zip(rr,cc) if j<len(active)),key=lambda a:a.player_id))
            if len(assignment)==len(active):
                arr=self._array_for(g,assignment);result=(assignment,[(assignment,arr)]);self.menu_cache[key]=result;return result
        # Congested day: same bounded menu as the predecessor, but only the
        # actually active players participate in the matching problem.
        unique={():()};first=None;proposals=[(profile,None) for profile in self.profiles]
        proposals.extend((self.profiles[0],p) for p in active if opts[p])
        for profile,forced in proposals:
            values=E@profile;cost=np.full((len(sids),len(active)+len(sids)),np.inf);cost[:,len(active):]=0.
            for i,sid in enumerate(sids):
                for j,p in enumerate(active):
                    if sid in opts[p]:cost[i,j]=-values[self.pindex[p]]
            if forced is not None:
                column=active.index(forced);finite=cost[np.isfinite(cost)]
                bound=2*(len(sids)+1)*(1+float(np.max(np.abs(finite))))
                cost[:,column]-=bound
            rr,cc=linear_sum_assignment(cost);self.search_stats['matching_calls']+=1
            assignment=tuple(sorted((opts[active[j]][sids[i]] for i,j in zip(rr,cc) if j<len(active)),key=lambda a:a.player_id))
            if forced is not None and forced not in {a.player_id for a in assignment}:
                raise EngineError('LINEUP_MENU_SUPPORT_FAILURE','A requested legal player was absent from its support plan.')
            if first is None:first=assignment
            chosen=tuple(a.player_id for a in assignment)
            if chosen not in unique or self.solver.key(assignment)<self.solver.key(unique[chosen]):unique[chosen]=assignment
        choices=[unique[k] for k in sorted(unique)];result=(first,[(a,self._array_for(g,a)) for a in choices]);self.menu_cache[key]=result;return result

    def _score_group_stats(self,stats,gid):
        credits={}
        for c in self.categories:
            x=category_value(c,stats);k=None if x is None else rounded(x,c['precision']);ck=(gid,c['category_id'],k)
            if ck not in self.group_credit_cache:
                keys,prefix,undefined=self.group_cdf[(gid,c['category_id'])]
                if k is None:
                    credit=undefined*self.objective['both_undefined_tie_credit']/self.field_weight
                else:
                    left=bisect_left(keys,k);right=bisect_right(keys,k)
                    tied=prefix[right]-prefix[left]
                    won=prefix[left] if c['direction']=='HIGHER_IS_BETTER' else prefix[-1]-prefix[right]
                    credit=(won+tied*self.objective['category_tie_credit']+undefined)/self.field_weight
                self.group_credit_cache[ck]=credit
            credits[c['category_id']]=self.group_credit_cache[ck]
        league=math.fsum(c['weight']*credits[c['category_id']] for c in self.categories)/math.fsum(c['weight'] for c in self.categories)
        strategy=math.fsum(self.weights[cid]*credits[cid] for cid in sorted(credits))/math.fsum(self.weights.values())
        return league,strategy,credits

    def _eval_arrays(self,pieces):
        out={}
        zero=np.zeros((len(self.scenarios),len(self.stats)))
        for gid,locks in self.eval_locks.items():
            out[gid]=sum((pieces[g] for g in locks),zero.copy())
        return out

    def _score_group_array(self,gid,total):
        scen=[self._score_group_stats(dict(zip(self.stats,row)),gid)[1] for row in total]
        self.search_stats['plans_scored']+=1;self.evaluated_plans+=1
        if self.evaluated_plans>self.maxplans:
            raise EngineError('LINEUP_SEARCH_BUDGET','Bounded lineup evaluation budget exhausted; no partial ranking returned.',status='RESOURCE_LIMIT')
        return float(self.probs@np.asarray(scen))

    def _utility_from_pieces(self,pieces):
        by=self._eval_arrays(pieces)
        return math.fsum(self.eval_weights[gid]*self._score_group_array(gid,total) for gid,total in by.items())/self.eval_weight_total

    def _weighted_games_from_pieces(self,pieces):
        by=self._eval_arrays(pieces)
        return math.fsum(self.eval_weights[gid]*float(self.probs@arr[:,self.exposure_index]) for gid,arr in by.items())

    def evaluate_fixed_plan(self,plan,scenarios=None):
        scenarios=self.scenarios if scenarios is None else sorted(scenarios,key=lambda x:x['scenario_id'])
        group_stats={}
        for s in scenarios:
            srows={}
            for gid,locks in self.eval_locks.items():
                parts={k:[] for k in self.stats}
                for lock in locks:
                    for a in sorted(plan[lock],key=lambda x:x.player_id):
                        for period in self.groups[lock]:
                            vals=s['player_period_stats'][a.player_id][period]
                            for k,v in vals.items():parts[k].append(v)
                srows[gid]={k:math.fsum(v) for k,v in parts.items()}
            group_stats[s['scenario_id']]=srows
        league=0.;strategy=0.;usable={k:0. for k in self.stats};credit_sum={c['category_id']:0. for c in self.categories}
        value_sum={c['category_id']:0. for c in self.categories};defined_sum={c['category_id']:0. for c in self.categories}
        group_results=[]
        for gid in sorted(self.eval_groups):
            gl=0.;gs=0.;gc={c['category_id']:0. for c in self.categories}
            for s in scenarios:
                stats=group_stats[s['scenario_id']][gid];l,u,cr=self._score_group_stats(stats,gid);prob=s['probability']
                gl+=prob*l;gs+=prob*u
                for k in self.stats:usable[k]+=self.eval_weights[gid]*prob*stats[k]
                for c in self.categories:
                    cid=c['category_id'];gc[cid]+=prob*cr[cid]
                    val=category_value(c,stats)
                    if val is not None:
                        value_sum[cid]+=self.eval_weights[gid]*prob*val;defined_sum[cid]+=self.eval_weights[gid]*prob
            w=self.eval_weights[gid]/self.eval_weight_total;league+=w*gl;strategy+=w*gs
            for cid,v in gc.items():credit_sum[cid]+=w*v
            group_results.append({'evaluation_group_id':gid,'weight':self.eval_weights[gid],'league_utility':gl,'strategy_utility':gs,'category_result_credit':gc})
        return {'league_utility':league,'strategy_utility':strategy,'expected_usable_stats':usable,
                'category_results':[{'category_id':c['category_id'],'expected_result_credit':credit_sum[c['category_id']],
                  'defined_probability':defined_sum[c['category_id']]/self.eval_weight_total,
                  'expected_value_when_defined':value_sum[c['category_id']]/defined_sum[c['category_id']] if defined_sum[c['category_id']] else None} for c in self.categories],
                'evaluation_group_results':group_results}

    def evaluate(self,owned,terminal=True):
        key=(tuple(sorted(owned)),terminal)
        if key in self.cache:return self.cache[key]
        constructions=self.solver.storage_options(owned,terminal,self.maximum_storage_patterns)
        best=None;bestkey=None
        for construction in constructions:
            playable=tuple(sorted(a.player_id for a in construction if a.slot_class in ('ACTIVE','BENCH')))
            penalty,effects=self.solver.soft_penalty(construction)
            menus={g:self.menu(playable,g) for g in self.gids};seed={g:menus[g][0] for g in self.gids}
            for initial in (seed,{g:() for g in self.gids}):
                plan=dict(initial);pieces={g:self._array_for(g,plan[g]) for g in self.gids}
                by=self._eval_arrays(pieces)
                gu={gid:self._score_group_array(gid,arr) for gid,arr in by.items()}
                gg={gid:float(self.probs@arr[:,self.exposure_index]) for gid,arr in by.items()}
                utility=math.fsum(self.eval_weights[gid]*gu[gid] for gid in gu)/self.eval_weight_total
                games=math.fsum(self.eval_weights[gid]*gg[gid] for gid in gg)
                for _ in range(self.sweeps):
                    changed=False
                    for g in self.gids:
                        gid=self.lock_to_eval[g];oldarr=pieces[g];old_group=by[gid]
                        winner=plan[g];winnerarr=oldarr;winutility=utility;wingames=games
                        winkey=(-utility,-games,self.solver.key(winner))
                        for a,arr in menus[g][1]:
                            proposal_group=old_group-oldarr+arr
                            newgu=self._score_group_array(gid,proposal_group)
                            newgg=float(self.probs@proposal_group[:,self.exposure_index])
                            u=utility+(self.eval_weights[gid]/self.eval_weight_total)*(newgu-gu[gid])
                            gms=games+self.eval_weights[gid]*(newgg-gg[gid])
                            test=(-u,-gms,self.solver.key(a))
                            if test<winkey:
                                winkey=test;winner=a;winnerarr=arr;winutility=u;wingames=gms;winner_group=proposal_group;winner_gu=newgu;winner_gg=newgg
                        if self.solver.key(winner)!=self.solver.key(plan[g]):
                            changed=True;plan[g]=winner;pieces[g]=winnerarr;by[gid]=winner_group;gu[gid]=winner_gu;gg[gid]=winner_gg;utility=winutility;games=wingames
                    if not changed:break
                result=self.evaluate_fixed_plan(plan);score=result['strategy_utility']-penalty
                k=(-score,-result['expected_usable_stats'][self.exposure_key],self.solver.key(construction),tuple((g,self.solver.key(plan[g])) for g in self.gids))
                if bestkey is None or k<bestkey:
                    bestkey=k;best={**result,'decision_utility':score,'soft_policy_penalty':penalty,'policy_effects':effects,
                        'construction_assignment':[a.output() for a in construction],
                        'lineup_plan':{g:[a.output() for a in plan[g]] for g in self.gids},
                        'optimizer_status':self.optimizer_status,'forecast_information_policy':'STATIC_PRE_DRAFT','_plan':plan}
        self.cache[key]=best;return best

class SplitHorizonReplayRunner(EfficientRunner):
    def __init__(self,data,sweeps=1,maximum_storage_patterns=64):
        d=validate(data);check_rule_scope(d)
        ResearchRunner.__init__(self,d,solver_class=ReplayEfficientRosterSolver)
        self.evaluator=WeightedEvaluationGroupLineupEvaluator(self.data,self.solver,sweeps,maximum_storage_patterns)
        self.gumbel_cache={};self.random_stats={'draws_computed':0,'draw_cache_hits':0}
    def run(self):
        r=super().run()
        profile=self.data['capability_profile']
        r['provenance'].update({'split_horizon_profile':profile,'intrinsic_projection_horizon_id':self.data['projection_set']['horizon_id'],
            'lineup_exposure_horizon_id':self.data['exposure_model']['horizon_id'],
            'scoring_evaluation_period_count':self.data['league']['scoring_objective']['evaluation_period_count'],
            'evaluation_groups':self.data['exposure_model']['evaluation_groups'],
            'projection_reconciliation_mode':self.data['exposure_model']['projection_reconciliation_mode']})
        if profile==PROFILE_REPRESENTATIVE:
            r['messages'].append({'severity':'WARNING','code':'REPRESENTATIVE_SEASON_SCHEDULE',
                'message':'Roster utility uses a structurally constrained representative 28-matchup season, not the exact published day-by-day 2026-27 schedule.'})
        elif profile==PROFILE_EXACT:
            r['messages'].append({'severity':'INFO','code':'EXACT_PUBLISHED_SCHEDULE_DATES',
                'message':'Roster utility uses NHL game dates parsed from the frozen official 2026-27 by-club schedule PDF and the declared 27-week HoH fantasy regular-season calendar.'})
        return r

def recommend_split_horizon(data,sweeps=1,maximum_storage_patterns=64):
    try:
        r=SplitHorizonReplayRunner(data,sweeps,maximum_storage_patterns).run();json.dumps(r,allow_nan=False);return r
    except EngineError as e:
        r=empty_result(data);r['run_status']=e.status;r['messages']=[e.as_dict()];return r
