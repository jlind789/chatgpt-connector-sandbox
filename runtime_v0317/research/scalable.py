"""Opt-in bounded lineup search; original exact engine and default are unchanged.

Roster feasibility and lazy draft choices are exact in the declared subset.
Lineups use a finite menu of global weighted matchings and coordinate ascent
on the ORIGINAL expected-category objective, NOT a proof of global optimality.
"""
from __future__ import annotations
import math
import copy
import json
import numpy as np
from scipy.optimize import linear_sum_assignment
from decision_engine.assignment import RosterSolver
from decision_engine.fast_assignment import MatchingRosterSolver
from decision_engine.engine import ResearchRunner, empty_result, uniform
from decision_engine.validation import EngineError, validate, canonical_hash
from decision_engine.scoring import IntrinsicModel
from research.robust_scoring import CDFEvaluator

PROFILE='BOUNDED_MATCHING_LINEUP_V035'


def check_scope(data):
    L=data['league']
    if any(s['slot_class'] not in ('ACTIVE','BENCH') or not s['counts_toward_roster_total'] or s['required_status_tags_any_of'] for s in L['roster_slots']):
        raise EngineError('SCALING_STORAGE_UNSUPPORTED','Opt-in scale profile supports ACTIVE/BENCH storage only; no IR/reserve rule is discarded.',status='UNSUPPORTED_CAPABILITY')
    if data['manager_profile']['position_policies']:
        raise EngineError('SCALING_POSITION_POLICY_UNSUPPORTED','Position policies require the retained exact solver; not silently removed.',status='UNSUPPORTED_CAPABILITY')
    if any(r['type']!='TOTAL_ROSTER_COUNT' or not {'ACTIVE','BENCH'}<=set(r['scope_slot_classes']) for r in L['league_constraints']):
        raise EngineError('SCALING_SIDE_CONSTRAINT_UNSUPPORTED','Scaled feasibility currently supports total-roster constraints across ACTIVE/BENCH only.',status='UNSUPPORTED_CAPABILITY')
    if L['lineup_rules']['active_limits'] or L['lineup_rules']['minimum_participation']:
        raise EngineError('SCALING_PARTICIPATION_UNSUPPORTED','This lineup approximation does not implement global start/game limits or participation minima.',status='UNSUPPORTED_CAPABILITY')


class CompactRosterSolver(MatchingRosterSolver):
    """Rectangular matching; compulsory rows/columns verified after solving.

    Costs maximize the number of selected mandatory players + dummy empties.
    A witness is accepted only if EVERY mandatory item is present. No fractional
    matching is rounded; no position constraint or cardinality is relaxed.
    """
    def __init__(self,data):
        check_scope(data)
        super().__init__(data)
        self.option_cache={}; self.all_cache={}; self.fill_cache={}
    def options(self,pid,locked=True):
        key=(pid,locked)
        if key not in self.option_cache:
            self.option_cache[key]=super().options(pid,locked)
        return self.option_cache[key]
    def matching_witness(self,owned,available,remaining,terminal=True,manager=True):
        owned=tuple(sorted(owned));pool=tuple(sorted(set(available)-set(owned)))
        k=len(owned)+remaining;sids=sorted(self.slots);s=len(sids)
        if len(set(owned))!=len(owned) or remaining<0 or remaining>len(pool) or k>s:return None
        ids=tuple(sorted(set(owned)|set(pool)));n=len(ids);empties=s-k
        if s==0:return () if k==0 else None
        cost=np.full((s,n+empties),np.inf); ownedset=set(owned)
        options={p:{a.slot_instance_id:a for a in reversed(self.options(p,locked=manager))} for p in ids}
        for i,sid in enumerate(sids):
            for j,p in enumerate(ids):
                if sid in options[p]:cost[i,j]=-float(p in ownedset)
            if not terminal or not self.slots[sid]['required_at_completion']:
                cost[i,n:]=-1.0
        try:rows,cols=linear_sum_assignment(cost)
        except ValueError:return None
        if len(rows)!=s or sum(j>=n for j in cols)!=empties:return None
        result=tuple(sorted((options[ids[j]][sids[i]] for i,j in zip(rows,cols) if j<n),key=lambda a:a.player_id))
        if len(result)!=k or not ownedset<={a.player_id for a in result}:return None
        if not self.league_valid(result,terminal) or (manager and not self.manager_valid(result,terminal)):return None
        return result
    def all(self,players,terminal=False,manager=True,budget=None):
        key=(tuple(sorted(players)),terminal,manager)
        if key not in self.all_cache:
            witness=self.matching_witness(key[0],[],0,terminal,manager)
            self.all_cache[key]=[] if witness is None else [witness]
        return self.all_cache[key]
    def initialize(self,players):
        a=self.matching_witness(players,[],0,False,True)
        if a is None:raise EngineError('ILLEGAL_LEAGUE_ROSTER','No legal current assignment including locks.')
        self.inherited={};return a
    def active_fill(self,players,manager=True):
        key=(tuple(sorted(players)),manager)
        if key not in self.fill_cache:self.fill_cache[key]=super().active_fill(players,manager)
        return self.fill_cache[key]
    def anonymous_rosters(self,pooled_players,agents):
        agents=sorted(agents,key=lambda a:a['agent_id']);ids=sorted(pooled_players)
        if not agents:return {} if not ids else super().anonymous_rosters(ids,agents)
        slots=[(a['agent_id'],sid) for a in agents for sid in sorted(self.slots)]
        dummies=[a['agent_id'] for a in agents for _ in range(len(self.slots)-a['initial_roster_count'])]
        if len(ids)+len(dummies)!=len(slots) or any(a['initial_roster_count']>len(self.slots) for a in agents):
            raise EngineError('ANONYMOUS_DEMAND_INFEASIBLE','Invalid anonymous ownership counts.')
        cost=np.full((len(slots),len(slots)),np.inf)
        opts={p:{a.slot_instance_id:a for a in reversed(self.options(p,False))} for p in ids}
        for i,(agent,sid) in enumerate(slots):
            for j,p in enumerate(ids):
                if sid in opts[p]:cost[i,j]=0.
            for j,owner in enumerate(dummies):
                if agent==owner:cost[i,len(ids)+j]=0.
        try:rr,cc=linear_sum_assignment(cost)
        except ValueError:raise EngineError('ANONYMOUS_DEMAND_INFEASIBLE','Pooled ownership cannot be legally repartitioned.')
        assignments={a['agent_id']:[] for a in agents}
        for i,j in zip(rr,cc):
            if j<len(ids):assignments[slots[i][0]].append(opts[ids[j]][slots[i][1]])
        for a in agents:
            items=assignments[a['agent_id']]
            if len(items)!=a['initial_roster_count'] or not self.league_valid(tuple(items),False):
                raise EngineError('ANONYMOUS_DEMAND_INFEASIBLE','Pooled ownership violates current total-roster rules.')
        return {key:tuple(sorted(x.player_id for x in value)) for key,value in assignments.items()}


class BoundedLineupEvaluator(CDFEvaluator):
    optimizer_status='APPROXIMATE_MATCHING_MENU_COORDINATE_ASCENT_NO_GLOBAL_BOUND'
    def __init__(self,data,solver,sweeps=2):
        if type(sweeps) is not int or not 0<=sweeps<=10:raise ValueError('sweeps must be an integer in [0,10].')
        check_scope(data);super().__init__(data,solver)
        self.sweeps=sweeps;self.menu_cache={};self.group_arrays={};self.search_stats={'matching_calls':0,'plans_scored':0}
        self.gids=list(self.groups);self.pids=sorted(solver.lp);self.pindex={p:i for i,p in enumerate(self.pids)}
        self.statindex={k:i for i,k in enumerate(self.stats)};self.probs=np.array([s['probability'] for s in self.scenarios])
        self.exposure_index=self.statindex[self.exposure_key]
        for g in self.gids:
            self.group_arrays[g]=np.array([[[math.fsum(s['player_period_stats'][p][t].get(k,0.) for t in self.groups[g]) for k in self.stats] for p in self.pids] for s in self.scenarios],dtype=float)
        # Projection-only additive menus: shared mixture + one per category.
        # They propose feasible plans; the original nonlinear objective chooses.
        self.profiles=np.zeros((1+len(self.categories),len(self.stats)))
        iv=IntrinsicModel(data)
        for j,c in enumerate(self.categories,1):
            anchor=iv.anchors[c['category_id']];sd=anchor['standard_deviation'];calc=c['calculation']
            if not sd:continue
            direction=1 if c['direction']=='HIGHER_IS_BETTER' else -1
            factor=direction*self.weights[c['category_id']]/sd
            if calc['type']=='COUNT':self.profiles[j,self.statindex[calc['stat_key']]]=factor
            elif anchor['pooled_reference_rate'] is not None:
                self.profiles[j,self.statindex[calc['numerator_stat_key']]]=factor
                self.profiles[j,self.statindex[calc['denominator_stat_key']]]=-factor*anchor['pooled_reference_rate']
        self.profiles[0]=self.profiles[1:].sum(axis=0)
    def _array_for(self,group,assignment):
        indices=[self.pindex[a.player_id] for a in assignment]
        return self.group_arrays[group][:,indices,:].sum(axis=1) if indices else np.zeros((len(self.scenarios),len(self.stats)))
    def menu(self,owned,g):
        key=(tuple(sorted(owned)),g)
        if key in self.menu_cache:return self.menu_cache[key]
        ids=list(key[0]);sids=sorted(self.active_slots)
        E=np.einsum('s,spk->pk',self.probs,self.group_arrays[g])
        opts={p:{a.slot_instance_id:a for a in reversed(self.solver.options(p,False)) if a.slot_instance_id in self.active_slots} for p in ids}
        unique={():()};first=None
        proposals=[(profile,None) for profile in self.profiles]
        # Give every playable roster member a supporting lineup, including players
        # below every linear category frontier but valuable to nonlinear outcomes.
        proposals.extend((self.profiles[0],p) for p in ids
                         if opts[p] and E[self.pindex[p],self.exposure_index]>0)
        for profile,forced in proposals:
            values=E@profile;cost=np.full((len(sids),len(ids)+len(sids)),np.inf);cost[:,len(ids):]=0.
            for i,sid in enumerate(sids):
                for j,p in enumerate(ids):
                    if sid in opts[p] and E[self.pindex[p],self.exposure_index]>0:
                        cost[i,j]=-values[self.pindex[p]]
            if forced is not None:
                column=ids.index(forced)
                # The finite objective range bounds every possible competing plan.
                # A dominating offset forces this player; legality is unchanged.
                bound=2*(len(sids)+1)*(1+float(np.max(np.abs(cost[np.isfinite(cost)]))))
                cost[:,column]-=bound
            rr,cc=linear_sum_assignment(cost);self.search_stats['matching_calls']+=1
            assignment=tuple(sorted((opts[ids[j]][sids[i]] for i,j in zip(rr,cc) if j<len(ids)),key=lambda a:a.player_id))
            if forced is not None and forced not in {a.player_id for a in assignment}:
                raise EngineError('LINEUP_MENU_SUPPORT_FAILURE','A requested legal player was absent from its support plan.')
            if first is None:first=assignment
            chosen=tuple(a.player_id for a in assignment)
            if chosen not in unique or self.solver.key(assignment)<self.solver.key(unique[chosen]):unique[chosen]=assignment
        choices=[unique[k] for k in sorted(unique)]
        # First option remains the shared additive seed; no score-based pruning of players.
        result=(first,[(a,self._array_for(g,a)) for a in choices])
        self.menu_cache[key]=result;return result
    def _utility(self,totals):
        scores=[self.score_stats(dict(zip(self.stats,row)))[1] for row in totals]
        self.search_stats['plans_scored']+=1;self.evaluated_plans+=1
        if self.evaluated_plans>self.maxplans:
            raise EngineError('LINEUP_SEARCH_BUDGET','Bounded lineup evaluation budget exhausted; no partial ranking returned.',status='RESOURCE_LIMIT')
        return float(self.probs@np.array(scores))
    def evaluate(self,owned,terminal=True):
        key=(tuple(sorted(owned)),terminal)
        if key in self.cache:return self.cache[key]
        construction=self.solver.best(owned,terminal,True)
        if construction is None:return None
        menus={g:self.menu(owned,g) for g in self.gids}
        seed={g:menus[g][0] for g in self.gids};best=None;bestkey=None
        for initial in (seed,{g:() for g in self.gids}):
            plan=dict(initial);pieces={g:self._array_for(g,plan[g]) for g in self.gids}
            total=sum(pieces.values());utility=self._utility(total)
            for _ in range(self.sweeps):
                changed=False
                for g in self.gids:
                    winner=plan[g];winnerarr=pieces[g];winutility=utility
                    wingames=float(self.probs@total[:,self.exposure_index])
                    winkey=(-winutility,-wingames,self.solver.key(winner))
                    for a,arr in menus[g][1]:
                        # Stable group-order resummation avoids cancellation-created negative stats.
                        proposal=sum((arr if h==g else pieces[h]) for h in self.gids)
                        u=self._utility(proposal);games=float(self.probs@proposal[:,self.exposure_index])
                        test=(-u,-games,self.solver.key(a))
                        if test<winkey:winkey=test;winner=a;winnerarr=arr;winutility=u
                    if self.solver.key(winner)!=self.solver.key(plan[g]):changed=True
                    plan[g]=winner;pieces[g]=winnerarr;utility=winutility;total=sum(pieces.values())
                if not changed:break
            result=self.evaluate_fixed_plan(plan)
            k=(-result['strategy_utility'],-result['expected_usable_stats'][self.exposure_key],tuple((g,self.solver.key(plan[g])) for g in self.gids))
            if bestkey is None or k<bestkey:bestkey=k;best=(plan,result)
        plan,result=best
        result={**result,'decision_utility':result['strategy_utility'],'soft_policy_penalty':0.,'policy_effects':[],
                'construction_assignment':[a.output() for a in construction],
                'lineup_plan':{g:[a.output() for a in plan[g]] for g in self.gids},
                'optimizer_status':self.optimizer_status,'forecast_information_policy':'STATIC_PRE_DRAFT','_plan':plan}
        self.cache[key]=result;return result


class ScalableRunner(ResearchRunner):
    def __init__(self,data,sweeps=2):
        data=validate(data);check_scope(data)
        super().__init__(data,solver_class=CompactRosterSolver)
        self.evaluator=BoundedLineupEvaluator(self.data,self.solver,sweeps)
    def choose(self,policy,owned,pool,picks_after,manager=True):
        # Exact lazy ordering, not a shortlist. An extra player can increase
        # fillable active slots by at most one in this declared rule subset.
        order=sorted(pool,key=lambda p:(-self.intrinsic.value(p),p))
        if policy=='B1':
            return next((p for p in order if self.solver.candidate_reason(owned,p,pool,picks_after,manager) is None),None)
        if policy!='B3':return super().choose(policy,owned,pool,picks_after,manager)
        upper=min(sum(s['slot_class']=='ACTIVE' for s in self.solver.slots.values()),self.solver.active_fill(owned,manager)+1)
        best=None;bestfill=-1
        for p in order:
            fill=self.solver.active_fill(tuple(owned)+(p,),manager)
            if fill<=bestfill:continue
            if self.solver.candidate_reason(owned,p,pool,picks_after,manager) is not None:continue
            best=p;bestfill=fill
            if fill==upper:return p
        return best
    def path(self,first,rollout,continuation):
        # Same candidate-invariant rank-softmax random priorities as E0. Checking
        # feasibility in descending priority gives exactly the maximum feasible
        # priority without constructing the entire feasible set at every pick.
        owned=list(self.owned)+[first];pool=set(self.available)-{first}
        anonymous={k:list(v) for k,v in self.anonymous.items()}
        trace=[{'pick_id':self.picks[0]['pick_id'],'actor_id':'USER','player_id':first}]
        last=max(i for i,p in enumerate(self.picks) if p['actor_id']=='USER')
        def failure(reason):
            return {'decision_utility':self.failure_value,'league_utility':0.,'strategy_utility':0.,'soft_policy_penalty':0.,'failed':True,'failure_reason':reason,'trace':trace,'owned_player_ids':sorted(owned),'category_results':[],'expected_usable_stats':{},'evaluation':None}
        for i,pick in enumerate(self.picks[1:last+1],1):
            actor=pick['actor_id'];later=sum(p['actor_id']==actor for p in self.picks[i+1:])
            if actor=='USER':
                chosen=self.choose(continuation,owned,pool,later)
                if chosen is None:return failure('USER_NO_FEASIBLE_CONTINUATION')
                owned.append(chosen)
            else:
                minimum=min((self.ranks[p] for p in pool),default=0)
                def priority(p):
                    u=min(max(uniform(self.cfg['seed'],rollout,pick['pick_id'],p),1e-16),1-1e-16)
                    return (-(self.ranks[p]-minimum)/self.cfg['neutral_temperature']-math.log(-math.log(u)),p)
                ordered=sorted(pool,key=priority,reverse=True)
                chosen=next((p for p in ordered if self.solver.candidate_reason(anonymous[actor],p,pool,later,False) is None),None)
                if chosen is None:raise EngineError('ANONYMOUS_DEMAND_INFEASIBLE_PATH','Anonymous demand cannot complete a legal selection.',status='UNSUPPORTED_CAPABILITY')
                anonymous[actor].append(chosen)
            pool.remove(chosen);trace.append({'pick_id':pick['pick_id'],'actor_id':actor,'player_id':chosen})
        evaluated=self.evaluator.evaluate(owned,terminal=True)
        if evaluated is None:return failure('NO_VALID_TERMINAL_LINEUP_OR_ROSTER')
        return {k:v for k,v in evaluated.items() if k in ('decision_utility','league_utility','strategy_utility','soft_policy_penalty','category_results','expected_usable_stats')}|{'failed':False,'failure_reason':None,'trace':trace,'owned_player_ids':sorted(owned),'evaluation':evaluated}
    def run(self):
        result=super().run()
        result['provenance']['execution_profile']=PROFILE
        result['provenance']['input_contract_profile']=self.data['capability_profile']
        result['provenance']['capability_profile']=PROFILE
        result['provenance']['lineup_search']={'sweeps':self.evaluator.sweeps,'seeds':2,'menu':'CATEGORY_MATCHINGS_PLUS_EVERY_PLAYABLE_PLAYER_SUPPORT_AND_EMPTY','global_optimality_proven':False,'objective_changed':False,**self.evaluator.search_stats}
        result['messages'].append({'severity':'WARNING','code':'APPROXIMATE_LINEUP_SEARCH','message':'All feasible draft candidates evaluated, but lineup search is bounded and has no global optimality certificate. Original exact default unchanged.'})
        return result


def recommend_scaled(data,sweeps=2):
    try:
        result=ScalableRunner(data,sweeps).run();json.dumps(result,allow_nan=False);return result
    except EngineError as e:
        result=empty_result(data);result['run_status']=e.status;result['messages']=[e.as_dict()];return result
