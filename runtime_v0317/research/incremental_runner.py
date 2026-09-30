"""v0.3.15 continuation-path efficiency layer.

Recommendation mathematics are unchanged.  This module reduces repeated work by:
* evaluating B1/B3 legality once per exact roster-equivalence player type;
* evaluating active-fill once per player type for B3;
* precomputing candidate-invariant opponent priority orders for each rollout/pick;
* checking opponent feasibility once per player type while scanning that order.

Player projections, utility, lineup optimization, constraints and random streams remain
separate and unchanged.  Type compression is used only where the existing certified
EfficientRosterSolver proves players interchangeable for legality/assignment.
"""
from __future__ import annotations
import math
from decision_engine.validation import EngineError, validate
from decision_engine.engine import ResearchRunner
from research.hoh_replay_runner import ReplayEfficientRosterSolver
from research.rule_aware import RuleAwareLineupEvaluator, check_rule_scope
from research.efficient_runner import EfficientRunner
import numpy as np

PROFILE='INCREMENTAL_TYPED_CONTINUATION_V0315'

class IncrementalHOHReplayRunner(EfficientRunner):
    def __init__(self,data,sweeps=0,maximum_storage_patterns=32):
        d=validate(data);check_rule_scope(d)
        ResearchRunner.__init__(self,d,solver_class=ReplayEfficientRosterSolver)
        self.evaluator=RuleAwareLineupEvaluator(self.data,self.solver,sweeps,maximum_storage_patterns)
        self.gumbel_cache={};self.random_stats={'draws_computed':0,'draw_cache_hits':0}
        self.priority_cache={}
        self.continuation_stats={
            'choose_calls':0,'choose_type_checks':0,'choose_identity_checks_avoided':0,
            'opponent_pick_calls':0,'opponent_type_checks':0,'opponent_identity_checks_avoided':0,
            'priority_orders_built':0,'priority_order_hits':0,
        }
        # Manager and league legality can have different type layouts.
        self.manager_types=self.solver.layout(True).player_type
        self.league_types=self.solver.layout(False).player_type

    def _ordered_by_intrinsic(self,pool):
        return sorted(pool,key=lambda p:(-self.intrinsic.value(p),p))

    def choose(self,policy,owned,pool,picks_after,manager=True):
        if policy not in ('B1','B3'):
            return ResearchRunner.choose(self,policy,owned,pool,picks_after,manager)
        self.continuation_stats['choose_calls']+=1
        types=self.manager_types if manager else self.league_types
        order=self._ordered_by_intrinsic(pool)
        if policy=='B1':
            seen={}
            for p in order:
                t=types[p]
                if t not in seen:
                    reason=self.solver.candidate_reason(owned,p,pool,picks_after,manager)
                    seen[t]=reason
                    self.continuation_stats['choose_type_checks']+=1
                else:
                    self.continuation_stats['choose_identity_checks_avoided']+=1
                if seen[t] is None:
                    return p
            return None
        # For B3, active fill and feasibility are both identity-invariant inside a
        # certified legality type.  Evaluate only the highest-IV representative of
        # each type; then apply the original (fill, IV, player_id) ordering exactly.
        reps=[];seen=set()
        for p in order:
            t=types[p]
            if t in seen:
                self.continuation_stats['choose_identity_checks_avoided']+=1
                continue
            seen.add(t);reps.append(p)
        best=None;bestfill=-1
        upper=min(len(self.evaluator.active_slots),len(owned)+1)
        for p in reps:
            self.continuation_stats['choose_type_checks']+=1
            fill=self.solver.active_fill(tuple(owned)+(p,),manager)
            if fill<bestfill:continue
            if self.solver.candidate_reason(owned,p,pool,picks_after,manager) is not None:continue
            if fill>bestfill or best is None or (-self.intrinsic.value(p),p)<(-self.intrinsic.value(best),best):
                best=p;bestfill=fill
            # Cannot return on upper immediately unless reps are in IV order (they are),
            # because the first feasible rep at upper fill is the highest-IV player
            # among every player whose type can attain upper fill.
            if fill==upper:return p
        return best

    def _priority_order(self,rollout,pick_id):
        key=(rollout,pick_id)
        if key in self.priority_cache:
            self.continuation_stats['priority_order_hits']+=1
            return self.priority_cache[key]
        # The pool-minimum rank term in the legacy expression is candidate-invariant,
        # so removing it cannot alter ordering.  Use exactly the same keyed Gumbel term.
        def priority(p):
            return (-self.ranks[p]/self.cfg['neutral_temperature']-self._log_gumbel(rollout,pick_id,p),p)
        out=tuple(sorted(self.available,key=priority,reverse=True))
        self.priority_cache[key]=out
        self.continuation_stats['priority_orders_built']+=1
        return out

    def _anonymous_choice(self,actor_roster,pool,later,rollout,pick):
        self.continuation_stats['opponent_pick_calls']+=1
        reason_by_type={}
        present=set(pool)
        for p in self._priority_order(rollout,pick['pick_id']):
            if p not in present:continue
            t=self.league_types[p]
            if t not in reason_by_type:
                reason_by_type[t]=self.solver.candidate_reason(actor_roster,p,pool,later,False)
                self.continuation_stats['opponent_type_checks']+=1
            else:
                self.continuation_stats['opponent_identity_checks_avoided']+=1
            if reason_by_type[t] is None:return p
        return None

    def path(self,first,rollout,continuation):
        owned=list(self.owned)+[first]
        pool=set(self.available)-{first}
        anonymous={k:list(v) for k,v in self.anonymous.items()}
        trace=[{'pick_id':self.picks[0]['pick_id'],'actor_id':'USER','player_id':first}]
        last=max(i for i,p in enumerate(self.picks) if p['actor_id']=='USER')
        # Precompute future-pick counts once for this fixed draft sequence.
        later_counts=[]
        for i,pick in enumerate(self.picks):
            later_counts.append(sum(p['actor_id']==pick['actor_id'] for p in self.picks[i+1:]))
        def failure(reason):
            return {'decision_utility':self.failure_value,'league_utility':0.,'strategy_utility':0.,'soft_policy_penalty':0.,'failed':True,'failure_reason':reason,'trace':trace,'owned_player_ids':sorted(owned),'category_results':[],'expected_usable_stats':{},'evaluation':None}
        for i,pick in enumerate(self.picks[1:last+1],1):
            actor=pick['actor_id'];later=later_counts[i]
            if actor=='USER':
                chosen=self.choose(continuation,owned,pool,later)
                if chosen is None:return failure('USER_NO_FEASIBLE_CONTINUATION')
                owned.append(chosen)
            else:
                chosen=self._anonymous_choice(anonymous[actor],pool,later,rollout,pick)
                if chosen is None:
                    raise EngineError('ANONYMOUS_DEMAND_INFEASIBLE_PATH','Anonymous demand cannot complete a legal selection.',status='UNSUPPORTED_CAPABILITY')
                anonymous[actor].append(chosen)
            pool.remove(chosen)
            trace.append({'pick_id':pick['pick_id'],'actor_id':actor,'player_id':chosen})
        evaluated=self.evaluator.evaluate(owned,terminal=True)
        if evaluated is None:return failure('NO_VALID_TERMINAL_LINEUP_OR_ROSTER')
        return {k:v for k,v in evaluated.items() if k in ('decision_utility','league_utility','strategy_utility','soft_policy_penalty','category_results','expected_usable_stats')}|{'failed':False,'failure_reason':None,'trace':trace,'owned_player_ids':sorted(owned),'evaluation':evaluated}

    def efficiency_provenance(self):
        return {'execution_profile':PROFILE,'continuation_efficiency':dict(self.continuation_stats),
                'solver_statistics':dict(self.solver.stats),'random_cache_statistics':dict(self.random_stats),
                'mathematics_changed':False,'timing_implemented':False}

from research.horizon_split import WeightedEvaluationGroupLineupEvaluator

class CachedWeightedEvaluationGroupLineupEvaluator(WeightedEvaluationGroupLineupEvaluator):
    """Exact score memoization across repeated weekly stat arrays.

    Search accounting remains unchanged: a memoized scoring request still counts as a
    plan examined and still respects the predecessor's deterministic maxplans guard.
    Only the repeated category-CDF arithmetic is reused.
    """
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.group_array_score_cache={}
        self.group_array_score_hits=0
        self.group_array_score_misses=0
        # Exact-calendar menu acceleration: predecessor recomputed scenario-weighted
        # player rows and serialized signatures on every roster/date call.  They are
        # immutable within one normalized snapshot, so compute them once.
        self._expected_group_rows={}
        self._active_by_group={}
        self._signature_piece_by_group={}
        for g in self.gids:
            E=np.einsum('s,spk->pk',self.probs,self.group_arrays[g])
            self._expected_group_rows[g]=E
            active=tuple(p for p in self.pids if E[self.pindex[p],self.exposure_index]>0)
            self._active_by_group[g]=active
            self._signature_piece_by_group[g]={p:(p,tuple(float(x) for x in E[self.pindex[p]])) for p in active}
    def menu(self,owned,g):
        if self.data['capability_profile']!='HORIZON_SPLIT_EXACT_CALENDAR_V1' or self.rules['active_limits']:
            return super().menu(owned,g)
        owned_set=set(owned)
        active=tuple(p for p in self._active_by_group[g] if p in owned_set)
        signature=tuple(self._signature_piece_by_group[g][p] for p in active)
        key=('EXACT_ACTIVE_SUBSET',signature)
        if key in self.menu_cache:return self.menu_cache[key]
        E=self._expected_group_rows[g]
        if not active:
            z=np.zeros((len(self.scenarios),len(self.stats)));result=((),[((),z)]);self.menu_cache[key]=result;return result
        from scipy.optimize import linear_sum_assignment
        sids=sorted(self.active_slots)
        opts={p:{a.slot_instance_id:a for a in reversed(self.solver.options(p,False)) if a.slot_instance_id in self.active_slots} for p in active}
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

    def _score_group_array(self,gid,total):
        key=(gid,total.shape,total.dtype.str,total.tobytes())
        self.search_stats['plans_scored']+=1;self.evaluated_plans+=1
        if self.evaluated_plans>self.maxplans:
            raise EngineError('LINEUP_SEARCH_BUDGET','Bounded lineup evaluation budget exhausted; no partial ranking returned.',status='RESOURCE_LIMIT')
        if key in self.group_array_score_cache:
            self.group_array_score_hits+=1
            return self.group_array_score_cache[key]
        scen=[self._score_group_stats(dict(zip(self.stats,row)),gid)[1] for row in total]
        value=float(self.probs@np.asarray(scen))
        self.group_array_score_cache[key]=value;self.group_array_score_misses+=1
        return value

class ExactCalendarIncrementalRunner(IncrementalHOHReplayRunner):
    """v0.3.15 path acceleration with the unchanged exact-calendar evaluator."""
    def __init__(self,data,sweeps=1,maximum_storage_patterns=64):
        d=validate(data);check_rule_scope(d)
        ResearchRunner.__init__(self,d,solver_class=ReplayEfficientRosterSolver)
        self.evaluator=CachedWeightedEvaluationGroupLineupEvaluator(self.data,self.solver,sweeps,maximum_storage_patterns)
        self.gumbel_cache={};self.random_stats={'draws_computed':0,'draw_cache_hits':0}
        self.priority_cache={}
        self.continuation_stats={
            'choose_calls':0,'choose_type_checks':0,'choose_identity_checks_avoided':0,
            'opponent_pick_calls':0,'opponent_type_checks':0,'opponent_identity_checks_avoided':0,
            'priority_orders_built':0,'priority_order_hits':0,
        }
        self.manager_types=self.solver.layout(True).player_type
        self.league_types=self.solver.layout(False).player_type
