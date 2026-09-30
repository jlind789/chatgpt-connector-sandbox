"""Headless roster-only draft-path runner. Market timing is deliberately absent."""
from __future__ import annotations
import copy
from collections import Counter
import hashlib
import json
import math
import statistics
from . import __version__
from .validation import validate, EngineError, canonical_hash
from .assignment import RosterSolver
from .scoring import IntrinsicModel, TeamEvaluator


def uniform(seed,rollout,pick_id,player_id):
    payload=json.dumps([seed,rollout,pick_id,player_id],separators=(',',':')).encode()
    value=int.from_bytes(hashlib.sha256(payload).digest()[:8],'big')
    return (value+0.5)/(2**64)


def availability(data):
    owned={e['player_id']:t['team_id'] for t in data['roster_state']['teams'] for e in t['entries']}
    excluded=set(data['draft_state']['manually_excluded_player_ids']);user=data['user_team_id']
    return {p['player_id']:('NON_ROSTERABLE' if not p['rosterable'] else 'OWNED_BY_USER' if owned.get(p['player_id'])==user else 'OWNED_BY_OPPONENT' if p['player_id'] in owned else 'EXCLUDED' if p['player_id'] in excluded else 'AVAILABLE') for p in sorted(data['league_players'],key=lambda p:p['player_id'])}


def mathematical_hash(data):
    """Canonical calculation fingerprint; full-input hash separately retains import provenance."""
    d=copy.deepcopy(data)
    d.pop('source_metadata');d.pop('snapshot_id');d.pop('created_at');d.pop('teams')
    own=next(t['entries'] for t in d['roster_state']['teams'] if t['team_id']==d['user_team_id'])
    opponents=sorted(e['player_id'] for t in d['roster_state']['teams'] if t['team_id']!=d['user_team_id'] for e in t['entries'])
    d['roster_state']={'user_entries':own,'pooled_opponent_player_ids':opponents};d['user_team_id']='USER'
    def clean(x):
        if isinstance(x,dict):
            return {k:clean(v) for k,v in sorted(x.items()) if k not in {'display_name','as_of','imported_at','source','source_platform','platform','reported_slot_definition_id'}}
        if isinstance(x,list):
            if x and isinstance(x[0],dict) and 'actor_id' in x[0]:return [clean(v) for v in x] # pick order is semantic
            items=[clean(v) for v in x]
            return sorted(items,key=lambda v:json.dumps(v,sort_keys=True))
        return x
    return canonical_hash(clean(d))

class ResearchRunner:
    def __init__(self,raw,solver_class=RosterSolver):
        self.data=validate(raw);d=self.data
        self.available_status=availability(d);self.available=tuple(p for p,s in self.available_status.items() if s=='AVAILABLE')
        self.owned=tuple(p for p,s in self.available_status.items() if s=='OWNED_BY_USER')
        pooled=tuple(p for p,s in self.available_status.items() if s=='OWNED_BY_OPPONENT')
        self.solver=solver_class(d)
        # Verify every reported league roster for legality, without using it for strategy.
        for t in d['roster_state']['teams']:
            if not self.solver.all([e['player_id'] for e in t['entries']],manager=False):raise EngineError('ILLEGAL_LEAGUE_ROSTER','An imported team has no legal current storage assignment.')
        self.current_assignment=self.solver.initialize(self.owned)
        self.anonymous=self.solver.anonymous_rosters(pooled,d['draft_state']['anonymous_demands'])
        self.intrinsic=IntrinsicModel(d);self.evaluator=TeamEvaluator(d,self.solver)
        self.cfg=d['methodology_config'];self.picks=d['draft_state']['remaining_picks']
        self.ranks={p:i+1 for i,p in enumerate(sorted(self.available,key=lambda p:(-self.intrinsic.value(p),p)))}
        self.path_cache={}
        self.failure_value=-1-self.solver.penalty_upper_bound()

    def feasible_candidates(self,owned,pool,remaining_after,manager=True):
        return [p for p in sorted(pool) if self.solver.candidate_reason(owned,p,pool,remaining_after,manager) is None]

    def choose(self,policy,owned,pool,picks_after,manager=True):
        feasible=self.feasible_candidates(owned,pool,picks_after,manager)
        if not feasible:return None
        if policy=='B1':return min(feasible,key=lambda p:(-self.intrinsic.value(p),p))
        if policy=='B3':
            return min(feasible,key=lambda p:(-self.solver.active_fill(tuple(owned)+(p,),manager),-self.intrinsic.value(p),p))
        if policy=='B0':
            ranks=self.data.get('external_ranks',{})
            if not set(feasible)<=set(ranks):return None
            return min(feasible,key=lambda p:(ranks[p],p))
        raise ValueError(f'Unknown continuation policy {policy}')

    def draw_anonymous(self,feasible,rollout,pick):
        temperature=self.cfg['neutral_temperature'];minimum=min(self.ranks[p] for p in feasible)
        # Candidate-invariant keyed Gumbels give without-replacement rank-softmax choices.
        def key(p):
            u=uniform(self.cfg['seed'],rollout,pick['pick_id'],p)
            u=min(max(u,1e-16),1-1e-16)
            g=-math.log(-math.log(u))
            return (-((self.ranks[p]-minimum)/temperature)+g,p)
        return max(feasible,key=key)

    def path(self,first,rollout,continuation):
        owned=list(self.owned)+[first];pool=set(self.available)-{first};anonymous={k:list(v) for k,v in self.anonymous.items()};trace=[{'pick_id':self.picks[0]['pick_id'],'actor_id':'USER','player_id':first}]
        last_user=max(i for i,p in enumerate(self.picks) if p['actor_id']=='USER')
        def failure(reason):return {'decision_utility':self.failure_value,'league_utility':0.0,'strategy_utility':0.0,'soft_policy_penalty':0.0,'failed':True,'failure_reason':reason,'trace':trace,'owned_player_ids':sorted(owned),'category_results':[],'expected_usable_stats':{},'evaluation':None}
        for i,pick in enumerate(self.picks[1:last_user+1],start=1):
            actor=pick['actor_id'];later=sum(p['actor_id']==actor for p in self.picks[i+1:])
            if actor=='USER':
                chosen=self.choose(continuation,owned,pool,later)
                if chosen is None:return failure('USER_NO_FEASIBLE_CONTINUATION')
                owned.append(chosen)
            else:
                feasible=self.feasible_candidates(anonymous[actor],pool,later,manager=False)
                if not feasible:raise EngineError('ANONYMOUS_DEMAND_INFEASIBLE_PATH','The anonymous demand model cannot produce a legal opponent selection. This is a model-coverage failure, not a penalty against the user.',status='UNSUPPORTED_CAPABILITY')
                chosen=self.draw_anonymous(feasible,rollout,pick);anonymous[actor].append(chosen)
            assert chosen in pool
            pool.remove(chosen);trace.append({'pick_id':pick['pick_id'],'actor_id':actor,'player_id':chosen})
        evaluated=self.evaluator.evaluate(owned,terminal=True)
        if evaluated is None:return failure('NO_VALID_TERMINAL_LINEUP_OR_ROSTER')
        return {k:v for k,v in evaluated.items() if k in ('decision_utility','league_utility','strategy_utility','soft_policy_penalty','category_results','expected_usable_stats')}|{'failed':False,'failure_reason':None,'trace':trace,'owned_player_ids':sorted(owned),'evaluation':evaluated}

    def sample(self,candidate,continuation=None):
        continuation=continuation or self.cfg['continuation_policy'];key=(candidate,continuation)
        if key not in self.path_cache:self.path_cache[key]=[self.path(candidate,r,continuation) for r in range(self.cfg['rollouts'])]
        return self.path_cache[key]

    def summarize(self,paths):
        n=len(paths);mean=lambda k:math.fsum(p[k] for p in paths)/n
        successes=[p for p in paths if not p['failed']]
        frequencies=Counter(tuple(p['owned_player_ids']) for p in successes)
        category_ids=[c['category_id'] for c in self.evaluator.categories]
        category_credit={cid:math.fsum(next((r['expected_result_credit'] for r in p['category_results'] if r['category_id']==cid),0.0) for p in paths)/n for cid in category_ids}
        used={s:math.fsum(p['expected_usable_stats'].get(s,0.0) for p in paths)/n for s in self.evaluator.stats}
        return {'neutral_utility':mean('decision_utility'),'league_utility':mean('league_utility'),'strategy_utility':mean('strategy_utility'),'soft_policy_penalty':mean('soft_policy_penalty'),'completion_failure_rate':sum(p['failed'] for p in paths)/n,'category_result_credit':category_credit,'expected_usable_stats':used,'completed_roster_frequencies':[{'player_ids':list(k),'path_count':v} for k,v in sorted(frequencies.items())],'failure_reasons':dict(sorted(Counter(p['failure_reason'] for p in paths if p['failed']).items())),'representative_success':successes[0] if successes else None}

    def run(self):
        d=self.data;out=empty_result(d)
        out['provenance']={'full_input_sha256':canonical_hash(d),'mathematical_input_sha256':mathematical_hash(d),'capability_profile':d['capability_profile'],'methodology_id':self.cfg['methodology_id'],'reference_model_id':d['reference_model']['reference_model_id'],'reference_field_policy':d['reference_model']['generation_policy'],'projection_set_id':d['projection_set']['projection_set_id'],'calendar_id':d['exposure_model']['calendar_id'],'exposure_model_id':d['exposure_model']['exposure_model_id'],'seed':self.cfg['seed'],'rollouts':self.cfg['rollouts'],'random_stream_scheme':'SHA256_KEYED_GUMBEL(seed,rollout,pick_id,player_id)','continuation_policy':self.cfg['continuation_policy'],'timing_implemented':False,'candidate_coverage':'ALL_FEASIBLE','lineup_optimizer_status':getattr(self.evaluator,'optimizer_status','EXACT_ENUMERATION_STATIC_INFORMATION'),'performance_validation':'NOT_ESTABLISHED','historical_backtest_run':False,'normalization_anchors':self.intrinsic.anchors,'availability':self.available_status,'current_construction_assignment':[a.output() for a in self.current_assignment],'grandfathered_manager_violation_vector':self.solver.inherited}
        out['messages']=list(self.intrinsic.warnings)+[{'severity':'INFO','code':'ROSTER_ONLY_NO_MARKET_MODEL','message':'Market timing is not implemented. Q_M equals Q_N and Timing Value is exactly zero.'},{'severity':'WARNING','code':'SYNTHETIC_REFERENCE_FIELD','message':'Results use a supplied fictional reference field, not an empirical population or validated probability model.'}]
        if any(self.solver.inherited.values()):out['messages'].append({'severity':'WARNING','code':'MANAGER_POLICY_ALREADY_VIOLATED','message':'Existing hard-policy violations are recorded componentwise and may not be worsened.'})
        if not self.picks:
            out['run_status']='DRAFT_COMPLETE';return out
        remaining=sum(p['actor_id']=='USER' for p in self.picks)-1
        feasible=[]
        for p in self.available:
            reason=self.solver.candidate_reason(self.owned,p,self.available,remaining)
            if reason:out['excluded_candidates'].append({'player_id':p,'status':'INFEASIBLE','reason_codes':[reason]})
            else:feasible.append(p)
        if not feasible:
            out['run_status']='DRAFT_COMPLETE' if len(self.owned)>=len(self.solver.slots) else 'NO_FEASIBLE_CANDIDATE'
            out['messages'].append({'severity':'INFO','code':'NO_FEASIBLE_SELECTION','message':'No legal next selection exists under the declared remaining-pick and completion constraints.'})
            return out
        reference=min(feasible,key=lambda p:(-self.intrinsic.value(p),p));out['score_reference_player_id']=reference
        summaries={p:self.summarize(self.sample(p)) for p in feasible};ref=summaries[reference]
        anyfailure=any(s['completion_failure_rate'] for s in summaries.values())
        overrides=bool(d['manager_profile']['position_policies'] or d['manager_profile']['category_preferences'])
        units='RISK_ADJUSTED_UTILITY_POINTS' if anyfailure else 'STRATEGY_UTILITY_POINTS' if overrides else 'MODELLED_CATEGORY_SHARE_PERCENTAGE_POINTS'
        for p in feasible:
            s=summaries[p];q=s['neutral_utility'];score=100*(q-ref['neutral_utility']);samples=self.sample(p);refpaths=self.sample(reference)
            differences=[100*(a['decision_utility']-b['decision_utility']) for a,b in zip(samples,refpaths)]
            representative=s.pop('representative_success');evaluation=representative['evaluation'] if representative else None
            reasons=['COMPLETED_ROSTER_UTILITY','ROSTER_ONLY']
            if self.intrinsic.value(p)>self.intrinsic.value(reference):reasons.append('INTRINSIC_VALUE_EDGE')
            if s['soft_policy_penalty']!=ref['soft_policy_penalty']:reasons.append('SOFT_TARGET_EFFECT')
            if s['completion_failure_rate']:reasons.append('COMPLETION_FAILURE_RISK')
            row={'player_id':p,'feasible':True,'intrinsic_value':self.intrinsic.value(p),'intrinsic_category_components':self.intrinsic.rows[p]['category_components'],'neutral_utility':q,'market_utility':q,'marginal_roster_value':score,'timing_value':0.0,'decision_score':score,'score_units':units,'score_reference_player_id':reference,'league_utility':s['league_utility'],'completion_failure_rate':s['completion_failure_rate'],'paired_sampling_standard_error_points':statistics.stdev(differences)/math.sqrt(len(differences)) if len(differences)>1 else None,'path_summary':s,'explanation':{'reason_codes':reasons,'category_credit_deltas_vs_reference':{c:s['category_result_credit'][c]-ref['category_result_credit'][c] for c in sorted(s['category_result_credit'])},'usable_stat_deltas_vs_reference':{k:s['expected_usable_stats'][k]-ref['expected_usable_stats'][k] for k in self.evaluator.stats},'soft_policy_penalty_delta':s['soft_policy_penalty']-ref['soft_policy_penalty'],'diagnostic_attributions_additive':False,'representative_completed_roster':representative['owned_player_ids'] if representative else None,'representative_construction_assignment':evaluation['construction_assignment'] if evaluation else None,'representative_lineup_plan':evaluation['lineup_plan'] if evaluation else None,'representative_policy_effects':evaluation['policy_effects'] if evaluation else None,'representative_draft_trace':representative['trace'] if representative else None,'representative_path_label':'ONE_SUCCESSFUL_PATH_NOT_THE_MEAN' if representative else 'NO_SUCCESSFUL_PATH'},'neutral_path_utility_samples':[x['decision_utility'] for x in samples]}
            out['ranked_candidates'].append(row)
        out['ranked_candidates'].sort(key=lambda r:(-r['decision_score'],-r['marginal_roster_value'],-r['intrinsic_value'],r['player_id']))
        out['recommended_player_id']=out['ranked_candidates'][0]['player_id']
        for baseline in ('B0','B1','B3'):
            chosen=self.choose(baseline,self.owned,self.available,remaining)
            out['baselines'][baseline]={'status':'NOT_AVAILABLE','reason':'No complete archived external-rank coverage.'} if chosen is None else {'status':'EVALUATED','player_id':chosen,'neutral_utility':summaries[chosen]['neutral_utility'],'league_utility':summaries[chosen]['league_utility'],'comparison_type':'FIRST_PICK_WITH_SHARED_'+self.cfg['continuation_policy']+'_CONTINUATION','predictive_evidence':False}
        out['baselines']['B2']={'status':'DEFERRED','reason':'B2 is implemented in research.baselines and evaluated over full drafts. This standalone call lacks the frozen initial replacement anchor required for a B2 first-pick diagnostic.'}
        out['baselines']['B4']={'status':'IDENTICAL_TO_THIS_RUNNER','player_id':out['recommended_player_id'],'reason':'Timing is absent; B4 and the roster-only engine are the same method.'}
        out['provenance']['unique_terminal_rosters_evaluated']=len(self.evaluator.cache)
        out['provenance']['lineup_plans_examined']=self.evaluator.evaluated_plans
        out['run_status']='ROSTER_ONLY'
        return out


def empty_result(data):
    return {'schema_version':'decision-engine-output/0.3.1','engine_version':__version__,'run_status':'INPUT_ERROR','calibration_status':'MODELLED_SCORE_UNCALIBRATED','input_snapshot_id':data.get('snapshot_id') if isinstance(data,dict) else None,'recommended_player_id':None,'score_reference_player_id':None,'ranked_candidates':[],'excluded_candidates':[],'baselines':{},'messages':[],'provenance':{}}


def recommend(data):
    try:
        result=ResearchRunner(data).run()
        # The serializer is also a last-line finite-number guard.
        json.dumps(result,allow_nan=False)
        return result
    except EngineError as exc:
        result=empty_result(data);result['run_status']=exc.status;result['messages']=[exc.as_dict()]
        return result


def apply_user_pick(data,player_id):
    """Pure snapshot transition; returns a new input and never edits the caller's snapshot."""
    runner=ResearchRunner(data)
    if not runner.picks or runner.picks[0]['actor_id']!='USER':raise EngineError('USER_NOT_ON_CLOCK','A user pick is not next.')
    if player_id not in runner.available:raise EngineError('PLAYER_UNAVAILABLE','The selected player is not available.')
    remaining=sum(p['actor_id']=='USER' for p in runner.picks)-1
    reason=runner.solver.candidate_reason(runner.owned,player_id,runner.available,remaining)
    if reason:raise EngineError(reason,'The selected player violates roster or completion feasibility.')
    result=copy.deepcopy(runner.data)
    for team in result['roster_state']['teams']:
        if team['team_id']==result['user_team_id']:team['entries'].append({'player_id':player_id,'acquisition_type':'DRAFT_PICK'})
    result['draft_state']['remaining_picks'].pop(0)
    result['draft_state']['status']='IN_PROGRESS' if result['draft_state']['remaining_picks'] else 'COMPLETE'
    result['snapshot_id']=result['snapshot_id']+'-after-'+player_id
    return result
