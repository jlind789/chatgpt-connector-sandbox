"""Opt-in full-roster rules profile. Exact integer legality; bounded lineup quality.

Stored IR/reserve players cannot produce active statistics. Storage is fixed
through this forecast horizon. This is not a recovery/activation transaction model.
"""
from __future__ import annotations
import copy
import json
import numpy as np
from decision_engine.engine import ResearchRunner, empty_result
from decision_engine.validation import EngineError,validate
from decision_engine.scoring import IntrinsicModel
from research.robust_scoring import CDFEvaluator
from research.scalable import BoundedLineupEvaluator,ScalableRunner
from research.constrained_assignment import ConstrainedRosterSolver

PROFILE='CONSTRAINED_ROSTER_BOUNDED_LINEUP_V036'


def check_rule_scope(data):
    # Exact current wire semantics only. No interpretation of a named platform's defaults.
    keys={s['stat_key']:s for s in data['league']['stat_definitions']}
    for limit in data['league']['lineup_rules']['active_limits']:
        if keys[limit['stat_key']].get('minimum',float('-inf'))<0:
            raise EngineError('SIGNED_CAP_STAT_UNSUPPORTED','Bounded capped lineup search requires nonnegative cap statistics.',status='UNSUPPORTED_CAPABILITY')


class RuleAwareLineupEvaluator(BoundedLineupEvaluator):
    optimizer_status='EXACT_STORAGE_SUBSETS_BOUNDED_LINEUP_NO_GLOBAL_UTILITY_BOUND'
    def __init__(self,data,solver,sweeps=2,maximum_storage_patterns=128):
        if type(sweeps) is not int or not 0<=sweeps<=10:raise ValueError('sweeps must be an integer in [0,10]')
        if type(maximum_storage_patterns) is not int or maximum_storage_patterns<1:raise ValueError('storage pattern budget must be positive')
        check_rule_scope(data);CDFEvaluator.__init__(self,data,solver)
        self.sweeps=sweeps;self.maximum_storage_patterns=maximum_storage_patterns
        self.menu_cache={};self.group_arrays={};self.search_stats={'matching_calls':0,'plans_scored':0,'cap_rejections':0}
        self.gids=list(self.groups);self.pids=sorted(solver.lp);self.pindex={p:i for i,p in enumerate(self.pids)}
        self.statindex={k:i for i,k in enumerate(self.stats)};self.probs=np.array([s['probability'] for s in self.scenarios])
        self.exposure_index=self.statindex[self.exposure_key]
        for g in self.gids:
            self.group_arrays[g]=np.array([[[sum(s['player_period_stats'][p][t].get(k,0.) for t in self.groups[g]) for k in self.stats] for p in self.pids] for s in self.scenarios],dtype=float)
        self.profiles=np.zeros((1+len(self.categories),len(self.stats)));iv=IntrinsicModel(data)
        for j,c in enumerate(self.categories,1):
            anchor=iv.anchors[c['category_id']];sd=anchor['standard_deviation'];calc=c['calculation']
            if not sd:continue
            factor=(1 if c['direction']=='HIGHER_IS_BETTER' else -1)*self.weights[c['category_id']]/sd
            if calc['type']=='COUNT':self.profiles[j,self.statindex[calc['stat_key']]]=factor
            elif anchor['pooled_reference_rate'] is not None:
                self.profiles[j,self.statindex[calc['numerator_stat_key']]]=factor
                self.profiles[j,self.statindex[calc['denominator_stat_key']]]=-factor*anchor['pooled_reference_rate']
        self.profiles[0]=self.profiles[1:].sum(axis=0)
    def menu(self,owned,g):
        base=super().menu(owned,g)
        if not self.rules['active_limits']:return base
        # Full matchings can all violate a small game cap. Add legal subsets;
        # otherwise bounded search could incorrectly have only the empty plan.
        cache=getattr(self,'capped_menu_cache',None)
        if cache is None:self.capped_menu_cache={};cache=self.capped_menu_cache
        key=(tuple(sorted(owned)),g)
        if key in cache:return cache[key]
        E=np.einsum('s,spk->pk',self.probs,self.group_arrays[g])
        unique={tuple(a.player_id for a in plan):plan for plan,_ in base[1]}
        for plan,_ in base[1]:
            for profile in self.profiles:
                values=E@profile
                order=sorted(plan,key=lambda a:(-values[self.pindex[a.player_id]],a.player_id))
                for k in range(1,len(order)+1):
                    subset=tuple(sorted(order[:k],key=lambda a:a.player_id));pk=tuple(a.player_id for a in subset)
                    if pk not in unique or self.solver.key(subset)<self.solver.key(unique[pk]):unique[pk]=subset
            for a in plan:
                if (a.player_id,) not in unique:unique[(a.player_id,)]=(a,)
        result=(base[0],[(unique[k],self._array_for(g,unique[k])) for k in sorted(unique)])
        cache[key]=result;return result
    def cap_valid(self,total):
        return all(np.all(total[:,self.statindex[r['stat_key']]]<=r['maximum']) for r in self.rules['active_limits'])
    def evaluate(self,owned,terminal=True):
        key=(tuple(sorted(owned)),terminal)
        if key in self.cache:return self.cache[key]
        constructions=self.solver.storage_options(owned,terminal,self.maximum_storage_patterns)
        best=None;bestkey=None
        for construction in constructions:
            playable=tuple(sorted(a.player_id for a in construction if a.slot_class in ('ACTIVE','BENCH')))
            penalty,effects=self.solver.soft_penalty(construction)
            menus={g:self.menu(playable,g) for g in self.gids}
            seed={g:menus[g][0] for g in self.gids}
            for initial in (seed,{g:() for g in self.gids}):
                plan=dict(initial);pieces={g:self._array_for(g,plan[g]) for g in self.gids};total=sum(pieces.values())
                if not self.cap_valid(total):self.search_stats['cap_rejections']+=1;continue
                utility=self._utility(total)
                for _ in range(self.sweeps):
                    changed=False
                    for g in self.gids:
                        winner=plan[g];winnerarr=pieces[g];winutility=utility
                        winkey=(-utility,-float(self.probs@total[:,self.exposure_index]),self.solver.key(winner))
                        for a,arr in menus[g][1]:
                            proposal=sum((arr if h==g else pieces[h]) for h in self.gids)
                            if not self.cap_valid(proposal):self.search_stats['cap_rejections']+=1;continue
                            u=self._utility(proposal);games=float(self.probs@proposal[:,self.exposure_index])
                            test=(-u,-games,self.solver.key(a))
                            if test<winkey:winkey=test;winner=a;winnerarr=arr;winutility=u
                        if self.solver.key(winner)!=self.solver.key(plan[g]):changed=True
                        plan[g]=winner;pieces[g]=winnerarr;utility=winutility;total=sum(pieces.values())
                    if not changed:break
                # Independent scalar recheck before committing the plan.
                if any(self.stats_for_plan(plan,s)[r['stat_key']]>r['maximum'] for s in self.scenarios for r in self.rules['active_limits']):
                    raise EngineError('LINEUP_CAP_WITNESS_INVALID','Scalar check rejected the bounded plan.')
                result=self.evaluate_fixed_plan(plan)
                score=result['strategy_utility']-penalty
                k=(-score,-result['expected_usable_stats'][self.exposure_key],self.solver.key(construction),tuple((g,self.solver.key(plan[g])) for g in self.gids))
                if bestkey is None or k<bestkey:
                    bestkey=k
                    best={**result,'decision_utility':score,'soft_policy_penalty':penalty,'policy_effects':effects,
                          'construction_assignment':[a.output() for a in construction],
                          'lineup_plan':{g:[a.output() for a in plan[g]] for g in self.gids},
                          'optimizer_status':self.optimizer_status,'forecast_information_policy':'STATIC_PRE_DRAFT','_plan':plan}
        self.cache[key]=best;return best


class RuleAwareRunner(ScalableRunner):
    def __init__(self,data,sweeps=2,maximum_storage_patterns=128):
        d=validate(data);check_rule_scope(d)
        ResearchRunner.__init__(self,d,solver_class=ConstrainedRosterSolver)
        self.evaluator=RuleAwareLineupEvaluator(self.data,self.solver,sweeps,maximum_storage_patterns)
    def choose(self,policy,owned,pool,picks_after,manager=True):
        if policy not in ('B1','B3'):return ResearchRunner.choose(self,policy,owned,pool,picks_after,manager)
        order=sorted(pool,key=lambda p:(-self.intrinsic.value(p),p))
        if policy=='B1':return next((p for p in order if self.solver.candidate_reason(owned,p,pool,picks_after,manager) is None),None)
        # Do NOT inherit the +1 active-fill bound under arbitrary positional rules.
        # This cardinality bound is valid even if constraints change all assignments.
        upper=min(len(self.evaluator.active_slots),len(owned)+1)
        best=None;bestfill=-1
        for p in order:
            fill=self.solver.active_fill(tuple(owned)+(p,),manager)
            if fill<=bestfill:continue
            if self.solver.candidate_reason(owned,p,pool,picks_after,manager) is not None:continue
            best=p;bestfill=fill
            if fill==upper:return p
        return best
    def run(self):
        result=ResearchRunner.run(self)
        result['provenance'].update({'execution_profile':PROFILE,'capability_profile':PROFILE,
            'input_contract_profile':self.data['capability_profile'],
            'construction_solver':'BINARY_MILP_POST_VALIDATED_WITNESSES','integer_solver_mip_relative_gap':0.,
            'storage_information_policy':'STATIC_STATUS_STATIC_HORIZON_STORAGE',
            'solver_statistics':dict(self.solver.stats),
            'lineup_search':{'sweeps':self.evaluator.sweeps,'maximum_storage_patterns':self.evaluator.maximum_storage_patterns,
                'global_optimality_proven':False,'objective_changed':False,**self.evaluator.search_stats},
            'participation_semantics':'DECLARED_STATIC_SCENARIOS_NOT_A_LIVE_GAME_COUNTER'})
        for row in result['ranked_candidates']:
            assignment=row['explanation']['representative_construction_assignment']
            if assignment is None:continue
            byp={p['player_id']:p for p in assignment};owned=sorted(byp)
            # Reconstruct witness to expose every applied count and hard rule.
            witness=tuple(next(a for a in self.solver.options(p) if a.output()==byp[p]) for p in owned)
            row['explanation']['representative_rule_counts']={r['constraint_id']:self.solver.count(witness,r) for r in self.solver.rules}
            row['explanation']['representative_manager_counts']={p['policy_id']:self.solver.count(witness,p) for p in self.solver.policies}
            row['explanation']['representative_hard_policy_violation_vector']=self.solver.hard_vector(witness,True)
            row['explanation']['static_reserve_player_ids']=[a.player_id for a in witness if a.slot_class not in ('ACTIVE','BENCH')]
        result['messages'].append({'severity':'WARNING','code':'BOUNDED_LINEUP_STATIC_STORAGE',
          'message':'Roster feasibility is integer-constrained and verified; nonlinear lineup quality has no global bound. Reserve storage/status is fixed for this forecast horizon; live activations are not modeled.'})
        return result


def recommend_rules(data,sweeps=2,maximum_storage_patterns=128):
    try:
        r=RuleAwareRunner(data,sweeps,maximum_storage_patterns).run();json.dumps(r,allow_nan=False);return r
    except EngineError as e:
        r=empty_result(data);r['run_status']=e.status;r['messages']=[e.as_dict()];return r


def apply_user_pick_rules(data,player_id):
    runner=RuleAwareRunner(data)
    if not runner.picks or runner.picks[0]['actor_id']!='USER':raise EngineError('USER_NOT_ON_CLOCK','User must be on clock.')
    if player_id not in runner.available:raise EngineError('PLAYER_UNAVAILABLE','Player is not available.')
    remaining=sum(p['actor_id']=='USER' for p in runner.picks)-1
    reason=runner.solver.candidate_reason(runner.owned,player_id,runner.available,remaining)
    if reason:raise EngineError(reason,'Pick violates roster or actual-pool completion requirements.')
    result=copy.deepcopy(runner.data)
    next(t for t in result['roster_state']['teams'] if t['team_id']==result['user_team_id'])['entries'].append({'player_id':player_id,'acquisition_type':'DRAFT_PICK'})
    result['draft_state']['remaining_picks'].pop(0)
    result['draft_state']['status']='IN_PROGRESS' if result['draft_state']['remaining_picks'] else 'COMPLETE'
    result['snapshot_id']+='-after-'+player_id
    return result
