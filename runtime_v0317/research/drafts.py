"""Complete counterfactual drafts with legal, state-adaptive opponent choices."""
from __future__ import annotations
import copy
import math
import time
from decision_engine.engine import ResearchRunner
from decision_engine.fast_assignment import MatchingRosterSolver
from decision_engine.scoring import IntrinsicModel,TeamEvaluator
from decision_engine.validation import EngineError,canonical_hash
from .baselines import ReplacementAnchor,choose
from .worlds import keyed_seed

class MemoEvaluator(TeamEvaluator):
    """Exact memoization only: no shortlist, relaxed rule or changed utility."""
    def __init__(self,*args):
        super().__init__(*args);self.stats_cache={};self.credit_cache={}
    def score_stats(self,stats):
        key=tuple(stats.get(k,0.) for k in self.stats)
        if key not in self.stats_cache:
            credits={}
            participation=sorted({r['stat_key'] for r in self.rules['minimum_participation']})
            for c in self.categories:
                calc=c['calculation']
                relevant=sorted(set(participation+([calc['stat_key']] if calc['type']=='COUNT' else [calc['numerator_stat_key'],calc['denominator_stat_key']])))
                ck=(c['category_id'],tuple(stats.get(k,0.) for k in relevant))
                if ck not in self.credit_cache:
                    self.credit_cache[ck]=math.fsum(f['weight']*self.category_credit(c,stats,f['stat_values']) for f in self.field)/self.field_weight
                credits[c['category_id']]=self.credit_cache[ck]
            league=math.fsum(c['weight']*credits[c['category_id']] for c in self.categories)/math.fsum(c['weight'] for c in self.categories)
            strategy=math.fsum(self.weights[cid]*credits[cid] for cid in sorted(credits))/math.fsum(self.weights.values())
            self.stats_cache[key]=(league,strategy,credits)
        return self.stats_cache[key]


def snapshot_for(world,rosters,pick_index,focus):
    d=copy.deepcopy(world['snapshot']);d['user_team_id']=focus
    others=sorted(t for t in world['team_ids'] if t!=focus)
    agent={t:f'anon{i+1}' for i,t in enumerate(others)}
    d['roster_state']['teams']=[{'team_id':t,'entries':[{'player_id':p,'acquisition_type':'KEEPER' if p in world['initial_rosters'][t] else 'DRAFT_PICK'} for p in sorted(rosters[t])]} for t in world['team_ids']]
    d['draft_state']['remaining_picks']=[{'pick_id':f'turn{j:03d}','actor_id':'USER' if t==focus else agent[t]} for j,t in enumerate(world['draft_order']) if j>=pick_index]
    d['draft_state']['anonymous_demands']=[{'agent_id':agent[t],'initial_roster_count':len(rosters[t])} for t in others]
    d['draft_state']['status']='IN_PROGRESS' if d['draft_state']['remaining_picks'] else 'COMPLETE'
    d['snapshot_id']+=f'-{focus}-turn{pick_index}'
    return d

class DraftContext:
    """Reusable caches are local to one immutable public world and manager profile."""
    def __init__(self,world,evaluator_factory=MemoEvaluator):
        self.world=world;self.data=world['snapshot']
        self.iv=IntrinsicModel(self.data);self.replacement=ReplacementAnchor(self.data,self.iv)
        self.solver=MatchingRosterSolver(self.data);self.solver.initialize([])
        self.evaluator=evaluator_factory(self.data,self.solver)
        self.cache_dependency_sha256=canonical_hash({k:self.data[k] for k in ('league','manager_profile','league_players','projection_set','exposure_model','reference_model','methodology_config')})
        self.shared_assignment={};self.shared_feasibility={};self.recommendation_cache={};self.engine_calls=0

    def engine_pick(self,rosters,index,focus):
        d=snapshot_for(self.world,rosters,index,focus)
        key=canonical_hash(d)
        if key in self.recommendation_cache:return self.recommendation_cache[key]
        runner=ResearchRunner(d,solver_class=MatchingRosterSolver)
        # No model dependency changes within a world. Only ownership/pick state changes.
        dependency=canonical_hash({k:d[k] for k in ('league','manager_profile','league_players','projection_set','exposure_model','reference_model','methodology_config')})
        if dependency!=self.cache_dependency_sha256:raise ValueError('Unsafe cross-model cache reuse.')
        if runner.solver.inherited!=self.solver.inherited or runner.solver.locks!=self.solver.locks:
            raise ValueError('Inherited policy/lock mismatch in research cache.')
        runner.solver.cache=self.shared_assignment;runner.solver.feas_cache=self.shared_feasibility
        runner.evaluator=self.evaluator;runner.intrinsic=self.iv
        result=runner.run();self.engine_calls+=1
        if result['run_status']!='ROSTER_ONLY':raise EngineError('DRAFT_AGENT_'+result['run_status'],'Engine did not return a usable pick.',status=result['run_status'])
        self.recommendation_cache[key]=result
        return result


def play_draft(world,focus,policy,context=None,opponent_style='MIXED'):
    context=context or DraftContext(world)
    solver=context.solver;iv=context.iv;anchor=context.replacement
    rosters=copy.deepcopy(world['initial_rosters']);taken={p for ids in rosters.values() for p in ids}
    pool={p['player_id'] for p in world['snapshot']['league_players'] if p['rosterable']}-taken
    trace=[];started=time.perf_counter()
    for index,team in enumerate(world['draft_order']):
        remaining=world['draft_order'][index+1:].count(team);agent_policy=policy if team==focus else ('B1','B3','B2')[index%3] if opponent_style=='MIXED' else opponent_style
        if agent_policy=='B4':
            try:result=context.engine_pick(rosters,index,team)
            except EngineError as exc:
                return {'status':'AGENT_ERROR','error':exc.as_dict(),'trace':trace,'rosters':rosters,'policy':policy,'focus_team':focus}
            selected=result['recommended_player_id']
            evidence={'input_sha256':result['provenance']['full_input_sha256'],'mathematical_input_sha256':result['provenance']['mathematical_input_sha256'],'decision_score':result['ranked_candidates'][0]['decision_score'],'timing_value':result['ranked_candidates'][0]['timing_value'],'candidate_count':len(result['ranked_candidates'])}
        else:
            selected=choose(agent_policy,rosters[team],pool,remaining,solver,iv,anchor)
            evidence={}
        if selected is None:
            return {'status':'NO_FEASIBLE_PICK','trace':trace,'rosters':rosters,'policy':policy,'focus_team':focus}
        if selected not in pool:raise AssertionError('Draft selected an unavailable player.')
        reason=solver.candidate_reason(rosters[team],selected,pool,remaining)
        if reason:raise AssertionError('Illegal actual draft pick: '+reason)
        pool.remove(selected);rosters[team].append(selected)
        trace.append({'pick_index':index,'team_id':team,'policy':agent_policy,'player_id':selected,**evidence})
    for team,ids in rosters.items():
        if not solver.best(ids,terminal=True):raise AssertionError('Invalid completed roster for '+team)
    return {'status':'COMPLETE','policy':policy,'focus_team':focus,'rosters':{t:sorted(p) for t,p in rosters.items()},'trace':trace,'elapsed_seconds':time.perf_counter()-started,'cache_dependency_sha256':context.cache_dependency_sha256}


def pure_baseline_draft(world,policy):
    # All managers use the stated baseline when generating the frozen field.
    return play_draft(world,world['team_ids'][0],policy,opponent_style=policy)
