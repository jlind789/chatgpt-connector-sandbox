"""v0.3.4: fixed-candidate replication and explicitly separate diagnostics.

No changes to the shipped decision formula. All draft/management commitments
precede hidden outcomes. The experimental scorer does not call the engine scorer.
"""
from __future__ import annotations
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import scipy
from decision_engine.fixtures import build_fixture
from decision_engine.validation import canonical_hash, validate
from .worlds import public_world, keyed_seed
from .reference import generate_reference_field
from .robustness import build_variants, contrast, source_hash
from .drafts import DraftContext, play_draft
from .baselines import choose
from .robust_scoring import CDFEvaluator, AdditiveManagementEvaluator
from .management import ForecastManager
from .independent import hidden_tape, sum_plan, score_matchup, lineup_plan

BASELINES = ('B1', 'B2', 'B3')
REPLICATION_POLICIES = BASELINES + ('E0', 'EUR', 'EA')
SUPPORTED_POLICIES = set(REPLICATION_POLICIES) | {'ED'}
MANAGERS = ('ADDITIVE', 'CATEGORY_EXPECTED')
PUBLIC_FAMILIES = ('BALANCED','CALENDAR_CONGESTION','ROLE_REVERSAL','GOALIE_RATES')
AUDIT_FAMILIES = PUBLIC_FAMILIES + ('UNOBSERVED_ROLE_SHIFT',)


def validate_protocol(p: dict) -> None:
    required = ('experiment_id','purpose','strata','seed_labels','engine_rollouts',
                'small_reference_draws','large_reference_draws','uncertainty',
                'audit_draws','policies','management_policies','primary_comparison')
    for name in required:
        if name not in p:
            raise ValueError('Missing protocol field: ' + name)
    if not p['experiment_id'] or not p['purpose']:
        raise ValueError('Experiment identity and purpose must be explicit.')
    policies = p['policies']
    if (not policies or len(set(policies)) != len(policies)
        or not set(policies) <= SUPPORTED_POLICIES or 'B3' not in policies):
        raise ValueError('Unique supported policies and the B3 comparator are required.')
    if (not p['management_policies'] or len(set(p['management_policies'])) != len(p['management_policies'])
        or not set(p['management_policies']) <= set(MANAGERS)):
        raise ValueError('Unknown, duplicate, or empty management policies.')
    if not p['strata'] or len({s['stratum_id'] for s in p['strata']}) != len(p['strata']):
        raise ValueError('Unique, nonempty strata are required.')
    for s in p['strata']:
        if s['public_family'] not in PUBLIC_FAMILIES or s['audit_family'] not in AUDIT_FAMILIES:
            raise ValueError('Unsupported public or audit generator.')
        if type(s['teams']) is not int or s['teams'] not in (2,3):
            raise ValueError('This protocol supports tiny 2/3-team worlds only.')
        if s.get('depth_profile') not in (None, 'EXPANDED_POOL_SHALLOW', 'EXPANDED_POOL_DEEP'):
            raise ValueError('Unknown roster-depth profile.')
        if s.get('depth_profile') and (s['teams'] != 2 or s['public_family'] != 'BALANCED'):
            raise ValueError('Depth diagnostic is explicitly limited to balanced two-team worlds.')
    labels = p['seed_labels']
    if not labels or len(set(labels)) != len(labels):
        raise ValueError('Seed labels must be nonempty and unique.')
    if any(type(n) is not int or n < 34000 for n in labels):
        raise ValueError('Use declared new >=34000 labels, not earlier experiment labels.')
    for name in ('engine_rollouts','small_reference_draws','large_reference_draws','audit_draws'):
        if type(p[name]) is not int or p[name] < 1:
            raise ValueError('Positive integer required: ' + name)
    if p['small_reference_draws'] >= p['large_reference_draws']:
        raise ValueError('Large reference sample must exceed the small sample.')
    from .uncertainty import ScenarioConfig
    ScenarioConfig(**p['uncertainty']).validate()
    if p['primary_comparison']['positive'] not in policies or p['primary_comparison']['negative'] not in policies:
        raise ValueError('Comparison policies must be present.')
    if p['primary_comparison']['manager'] not in p['management_policies']:
        raise ValueError('Primary manager must be evaluated.')


def expanded_depth_world(base: dict, profile: str) -> dict:
    """Same expanded public pool in both layouts; only roster demand differs.

    Two extra independently generated players per position are added. No player
    outcome or engine pick determines the new roster structure or supply.
    """
    if profile not in ('EXPANDED_POOL_SHALLOW','EXPANDED_POOL_DEEP'):
        raise ValueError('Unknown depth profile.')
    d = base['snapshot']
    if len(base['team_ids']) != 2 or base['family'] != 'BALANCED':
        raise ValueError('Depth generator requires balanced two-team base.')
    period_ids = [x['period_id'] for x in d['exposure_model']['periods']]
    forecast = d['exposure_model']['forecast_scenarios'][0]['player_period_stats']
    players = {p['player_id']:(p['eligibility']['position_ids'],
        {i:copy.deepcopy(forecast[p['player_id']][period]) for i,period in enumerate(period_ids)})
        for p in d['league_players']}
    rng = random.Random(keyed_seed('M034_PUBLIC_DEPTH_SUPPLY',base['world_seed']))
    for role in ('C','RW'):
        for i in (4,5):
            quality = rng.uniform(.55,1.6)
            days = [0,1] if rng.random()<.5 else [rng.randrange(2)]
            schedule = {day:{'games':1.,'goals':.42*quality*rng.uniform(.7,1.3),
                    'assists':.6*quality*rng.uniform(.5,1.5),'shots':3.2*quality*rng.uniform(.8,1.2)}
                    for day in days}
            players[f'{role.lower()}_{i:02d}'] = ([role],schedule)
    deep = profile == 'EXPANDED_POOL_DEEP'
    slots = [('C',1,['C'],'ACTIVE'),('RW',1,['RW'],'ACTIVE')]
    if deep:
        slots.append(('UTIL',1,['C','RW'],'ACTIVE'))
    slots.append(('BN',2 if deep else 1,[],'BENCH'))
    size = sum(s[1] for s in slots)
    names = list(base['team_ids'])
    order = [t for r in range(size) for t in (names if r%2==0 else list(reversed(names)))]
    name = f'm034_depth_{base["world_seed"]}_{"deep" if deep else "shallow"}'
    snapshot = build_fixture(name,slots,players,[],['USER' if t=='user' else 'anon1' for t in order],
        [{'goals':1,'assists':2,'shots':8}],categories=copy.deepcopy(d['league']['categories']),
        opponent_groups=[[]],period_count=2,rollouts=d['methodology_config']['rollouts'])
    snapshot['league']['draft_config']['draft_type']='SNAKE'
    snapshot['methodology_config']=copy.deepcopy(d['methodology_config'])
    snapshot['methodology_config']['max_lineup_plans']=200000
    validate(snapshot)
    out=copy.deepcopy(base)
    out.update(snapshot=snapshot,roster_size=size,draft_order=order,
               public_generator_version='M034_EXPANDED_DEPTH_V1',depth_profile=profile)
    return out


def build_world(stratum: dict, label: int, protocol: dict) -> dict:
    # A declared pair group allows depth layouts to share the public player pool.
    key = stratum.get('public_pair_group', stratum['stratum_id'])
    seed = keyed_seed('PUBLIC_M034_FRESH_V1',key,label) % 2147483647
    world = public_world(stratum['public_family'],seed,stratum['teams'],protocol['engine_rollouts'])
    if stratum.get('depth_profile'):
        world = expanded_depth_world(world,stratum['depth_profile'])
    world['research_stratum']=stratum['stratum_id']
    world['seed_label']=label
    world['public_pair_id']=f'{key}_{label}'
    return world


def mixed_baseline_draft(world: dict, offset: int) -> dict:
    """Heterogeneous reference managers, each with a fixed declared baseline.

    No engine guidance, no field-utility optimization, and no audit information.
    """
    if type(offset) is not int or offset not in (0,1,2):
        raise ValueError('Reference composition offset must be 0, 1, or 2.')
    context=DraftContext(world)
    rosters=copy.deepcopy(world['initial_rosters'])
    taken={p for r in rosters.values() for p in r}
    pool={p['player_id'] for p in world['snapshot']['league_players'] if p['rosterable']}-taken
    policies={t:BASELINES[(i+offset)%3] for i,t in enumerate(world['team_ids'])}
    trace=[]
    for index,team in enumerate(world['draft_order']):
        remaining=world['draft_order'][index+1:].count(team)
        selected=choose(policies[team],rosters[team],pool,remaining,context.solver,context.iv,context.replacement)
        if selected is None:
            raise ValueError('Mixed reference draft has no feasible continuation.')
        if selected not in pool:
            raise AssertionError('Duplicate reference selection.')
        rosters[team].append(selected);pool.remove(selected)
        trace.append({'pick_index':index,'team_id':team,'policy':policies[team],'player_id':selected})
    for roster in rosters.values():
        if not context.solver.best(roster,terminal=True):
            raise AssertionError('Illegal reference roster.')
    return {'status':'COMPLETE','rosters':{t:sorted(r) for t,r in rosters.items()},
            'trace':trace,'team_policies':policies}


def mixed_reference_field(world: dict, draws_per_roster: int) -> dict:
    """Change roster composition at exactly the same forecast-row budget.

    Three heterogeneous reference drafts replace three homogeneous drafts.
    Both fields use identical public forecast tapes, draw IDs, and row weights.
    More distinct rosters are measured, never assumed.
    """
    if type(draws_per_roster) is not int or draws_per_roster<1:
        raise ValueError('Positive integer reference draw count required.')
    data=world['snapshot'];field=[];trace=[]
    tapes=[hidden_tape(data,'FORECAST',keyed_seed('REFERENCE-v1',world['family'],world['world_seed'],len(world['team_ids'])),draw,
                       namespace='REFERENCE_FORECAST-v1') for draw in range(draws_per_roster)]
    for offset in (0,1,2):
        draft=mixed_baseline_draft(world,offset)
        for team,roster in sorted(draft['rosters'].items()):
            plan=lineup_plan(data,roster)
            for draw,tape in enumerate(tapes):
                field.append({'reference_id':f'mixed{offset}_{team}_{draw}','weight':1.,'stat_values':sum_plan(data,plan,tape)})
            trace.append({'reference_draft':offset,'team_id':team,'roster':roster,
                          'team_policy':draft['team_policies'][team],'forecast_only_lineup_plan':plan})
    out=copy.deepcopy(world);ref=out['snapshot']['reference_model']
    ref.update(generation_policy='M034_HETEROGENEOUS_BASELINE_COMPOSITION',field=field,
               field_id='mixed_composition_'+canonical_hash(field)[:16])
    ref['reference_model_id']=ref['field_id']
    out['reference_generation']={'version':'M034_HETEROGENEOUS_BASELINE_COMPOSITION',
        'source':'PUBLIC_PRE_DRAFT_PROJECTIONS_ONLY','audit_outcomes_used':False,
        'draws_per_roster':draws_per_roster,'sample_rows':len(field),
        'baseline_completed_rosters':trace,'unique_rosters':len({tuple(t['roster']) for t in trace}),
        'field_sha256':canonical_hash(field),
        'interpretation':'Same row budget and forecast tapes, different homogeneous-versus-heterogeneous baseline composition; diversity may not increase.'}
    validate(out['snapshot'])
    return out


def build_models(raw: dict, protocol: dict) -> tuple[dict,dict]:
    # Preserve v0.3.3's candidate construction and parameters exactly.
    variants,provenance=build_variants(raw,protocol)
    models={k:variants[k] for k in ('E0','EUR','EA')}
    if 'ED' in protocol['policies']:
        alternate=mixed_reference_field(raw,protocol['large_reference_draws'])
        ed=copy.deepcopy(models['EUR'])
        ed['snapshot']['reference_model']=copy.deepcopy(alternate['snapshot']['reference_model'])
        ed['variant_id']='ED'
        ed['reference_generation']=alternate['reference_generation']
        models['ED']=ed
        baseline_rosters=provenance['large_reference']['baseline_completed_rosters']
        provenance['composition_contrast']={'original_rows':len(models['EUR']['snapshot']['reference_model']['field']),
            'alternate_rows':len(ed['snapshot']['reference_model']['field']),
            'original_unique_rosters':len({tuple(t['roster']) for t in baseline_rosters}),
            'alternate_unique_rosters':alternate['reference_generation']['unique_rosters'],
            'alternate_reference':alternate['reference_generation'],
            'projection_unchanged':canonical_hash(ed['snapshot']['projection_set'])==canonical_hash(models['EUR']['snapshot']['projection_set']),
            'uncertainty_unchanged':canonical_hash(ed['snapshot']['exposure_model'])==canonical_hash(models['EUR']['snapshot']['exposure_model'])}
    provenance['evaluated_variants']={k:{'snapshot_sha256':canonical_hash(v['snapshot'])}
        for k,v in models.items() if k in protocol['policies']}
    return models,provenance


def role_adjusted_public(data: dict, seed: int, assignment: int, magnitude: float) -> tuple[dict,dict]:
    """Auditor-only persistent role factors. The original snapshot is immutable."""
    if type(assignment) is not int or assignment<0 or not 0<=magnitude<1:
        raise ValueError('Invalid hidden-role assignment or magnitude.')
    out=copy.deepcopy(data);factors={}
    for player in sorted(p['player_id'] for p in out['players']):
        rng=np.random.default_rng(keyed_seed('M034_AUDIT_ROLE_FACTORS',seed,assignment,player))
        factor=1+magnitude*(1 if rng.integers(0,2) else -1)
        factors[player]=factor
        for scenario in out['exposure_model']['forecast_scenarios']:
            for stats in scenario['player_period_stats'][player].values():
                for name in ('goals','assists','shots'):
                    if name in stats:stats[name]*=factor
    return out,factors


def audit_tape(data: dict, family: str, seed: int, draw: int) -> dict:
    if family=='UNOBSERVED_ROLE_SHIFT':
        hidden,_=role_adjusted_public(data,seed,0,.45)
        return hidden_tape(hidden,'BALANCED',seed,draw,namespace='AUDIT_M034_ROLE_OUTCOMES')
    return hidden_tape(data,family,seed,draw,namespace='AUDIT_M034_OUTCOMES')


def score_committed_cases(data: dict, team_ids: list, cases: list, tape_iterator) -> list[str]:
    """Only score previously committed plans. No selection code runs here."""
    good=[c for c in cases if c['status']=='COMPLETE']
    plans={}
    for c in good:
        c['_scores']=[];c['_categories']={k['category_id']:[] for k in data['league']['categories']}
        c['_plan_keys']={t:canonical_hash(plan) for t,plan in c['committed_plans'].items()}
        for t,key in c['_plan_keys'].items():plans[key]=c['committed_plans'][t]
    hashes=[]
    for tape in tape_iterator:
        hashes.append(canonical_hash(tape))
        totals={key:sum_plan(data,plan,tape) for key,plan in plans.items()}
        pair_cache={}
        for c in good:
            focus=c['focus_team'];results=[]
            for other in team_ids:
                if other==focus:continue
                pair=(c['_plan_keys'][focus],c['_plan_keys'][other])
                if pair not in pair_cache:pair_cache[pair]=score_matchup(data['league'],totals[pair[0]],totals[pair[1]])
                results.append(pair_cache[pair])
            c['_scores'].append(statistics.fmean(r[0] for r in results))
            for key in c['_categories']:c['_categories'][key].append(statistics.fmean(r[1][key] for r in results))
    for c in good:
        c['outcome_share_samples']=c.pop('_scores')
        c['category_share']=statistics.fmean(c['outcome_share_samples'])
        c['category_credits']={k:statistics.fmean(v) for k,v in c.pop('_categories').items()}
        del c['_plan_keys']
    return hashes


def evaluate_world(raw: dict, stratum: dict, protocol: dict, commit_callback=None) -> dict:
    started=time.perf_counter()
    models,provenance=build_models(raw,protocol)
    contexts={k:DraftContext(w,AdditiveManagementEvaluator if k=='EA' else CDFEvaluator)
              for k,w in models.items() if k in protocol['policies'] or k=='E0'}
    managers={'ADDITIVE':ForecastManager(models['E0']['snapshot']),
              'CATEGORY_EXPECTED':ForecastManager(models['EUR']['snapshot'])}
    cases=[];failures=[]
    for focus in raw['team_ids']:
        for name in protocol['policies']:
            model_name='E0' if name in BASELINES else name
            draft=play_draft(models[model_name],focus,name if name in BASELINES else 'B4',contexts[model_name])
            if draft['status']!='COMPLETE':
                failures.append({'focus_team':focus,'policy':name,'status':draft['status'],'error':draft.get('error')})
            for manager_name in protocol['management_policies']:
                c={'focus_team':focus,'policy':name,'management_policy':manager_name,'status':draft['status'],'draft':draft}
                if draft['status']=='COMPLETE':
                    c['committed_plans']={t:managers[manager_name].choose(r,manager_name) for t,r in draft['rosters'].items()}
                    c['commitment_sha256']=canonical_hash({'rosters':draft['rosters'],'trace':draft['trace'],'plans':c['committed_plans']})
                cases.append(c)
    committed=copy.deepcopy(cases)
    for c in committed:c['draft'].pop('elapsed_seconds',None)
    commitment={'world_id':raw['snapshot']['league']['league_id'],'public_pair_id':raw['public_pair_id'],
        'source_snapshot_sha256':canonical_hash(raw['snapshot']),'model_provenance':provenance,
        'cases':committed,'audit_outcomes_generated':False}
    digest=canonical_hash(commitment)
    if commit_callback is not None:commit_callback(commitment,digest)
    seed=keyed_seed('M034_AUDIT_ROOT',stratum.get('audit_pair_group',stratum['stratum_id']),raw['seed_label'])
    # The only point at which hidden observations are first generated.
    hashes=score_committed_cases(raw['snapshot'],raw['team_ids'],cases,
        (audit_tape(raw['snapshot'],stratum['audit_family'],seed,i) for i in range(protocol['audit_draws'])))
    return {'world_id':raw['snapshot']['league']['league_id'],'seed_label':raw['seed_label'],
        'stratum_id':stratum['stratum_id'],'public_pair_id':raw['public_pair_id'],'public_seed':raw['world_seed'],
        'teams':len(raw['team_ids']),'roster_size':raw['roster_size'],'pool_size':len(raw['snapshot']['players']),
        'source_snapshot_sha256':canonical_hash(raw['snapshot']),'protocol_sha256':canonical_hash(protocol),
        'model_provenance':provenance,'pre_outcome_commitment_sha256':digest,
        'audit_tape_hash_root':canonical_hash(hashes),'audit_draws':protocol['audit_draws'],
        'distinct_complete_drafts_attempted':len(raw['team_ids'])*len(protocol['policies']),
        'draft_failures':failures,'scored_management_cases':len(cases),'cases':cases,
        'engine_calls':{k:c.engine_calls for k,c in contexts.items()},
        'timing_enabled':False,'elapsed_seconds':time.perf_counter()-started}


def league_rows(results: list, policies: list, manager: str) -> tuple[list,list]:
    rows=[];excluded=[]
    for w in results:
        groups={}
        for c in w['cases']:
            if c['management_policy']==manager:
                groups.setdefault(c['focus_team'],{})[c['policy']]=c
        complete=[]
        for seat,g in sorted(groups.items()):
            if set(g)==set(policies) and all(c['status']=='COMPLETE' for c in g.values()):complete.append(g)
            else:excluded.append({'world_id':w['world_id'],'seat':seat,'manager':manager})
        if complete:
            rows.append({'world_id':w['world_id'],'stratum_id':w['stratum_id'],
                'public_pair_id':w['public_pair_id'],'seed_label':w['seed_label'],
                'paired_seats':len(complete),'scores':{p:statistics.fmean(g[p]['category_share'] for g in complete) for p in policies}})
    return rows,excluded


def summarize(results: list, protocol: dict) -> dict:
    comparisons=[tuple(x) for x in protocol.get('comparisons',[['EUR','B3'],['EUR','E0'],['EA','EUR']])]
    managers={};all_rows={};excluded=[]
    for manager in protocol['management_policies']:
        rows,bad=league_rows(results,protocol['policies'],manager);excluded+=bad;all_rows[manager]=rows
        def table(selected):
            return {'world_clusters':len(selected),'paired_seats':sum(r['paired_seats'] for r in selected),
                'mean_category_share':{p:statistics.fmean(r['scores'][p] for r in selected) if selected else None for p in protocol['policies']},
                'contrasts':{a+'_minus_'+b:contrast(selected,a,b,keyed_seed('M034_BOOT',manager,a,b),4000)
                    for a,b in comparisons if a in protocol['policies'] and b in protocol['policies']}}
        managers[manager]={'overall':table(rows),'by_stratum':{sid:table([r for r in rows if r['stratum_id']==sid]) for sid in sorted(s['stratum_id'] for s in protocol['strata'])}}
    choice=protocol['primary_comparison']
    primary=managers[choice['manager']]['overall']['contrasts'][choice['positive']+'_minus_'+choice['negative']]
    failures=[{'world_id':w['world_id'],**f} for w in results for f in w['draft_failures']]
    expected=len(protocol['strata'])*len(protocol['seed_labels'])
    if len({(w['stratum_id'],w['seed_label']) for w in results})!=len(results):
        raise ValueError('Duplicate world clusters cannot inflate evidence.')
    return {'status':'SYNTHETIC_REPLICATION_NOT_PRODUCT_PROMOTION',
        'expected_worlds':expected,'attempted_worlds':len(results),'protocol_complete':len(results)==expected,
        'distinct_drafts_attempted':sum(w['distinct_complete_drafts_attempted'] for w in results),
        'distinct_drafts_completed':sum(w['distinct_complete_drafts_attempted'] for w in results)-len(failures),
        'draft_failures':failures,'excluded_paired_seats':excluded,
        'scored_management_cases':sum(w['scored_management_cases'] for w in results),
        'protocol_sha256':canonical_hash(protocol),'primary_comparison':{**choice,**primary},
        'by_management':managers,'paired_league_clusters':all_rows,
        'primary_mean_margin_target_pp':1.0,'subgroup_mean_loss_limit_pp':-1.0,
        'subgroup_mean_loss_flags':[sid for sid,x in managers[choice['manager']]['by_stratum'].items()
            if x['contrasts'].get(choice['positive']+'_minus_'+choice['negative'],{}).get('mean_pp') is not None
            and x['contrasts'][choice['positive']+'_minus_'+choice['negative']]['mean_pp'] < -1.0],
        'historical_validation':False,'promotion_eligible':False,
        'interval_scope':'4000 stratified paired generated-league bootstrap resamples; conditional on declared fictional generators; nonprimary contrasts descriptive, not multiplicity-adjusted.',
        'depth_pairing_note':'Depth diagnostics must use paired public_pair_id contrasts; do not treat paired layouts as independent worlds.'}


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    temporary.replace(path)


def _run_job(job):
    stratum,label,protocol,output,code_hash=job
    out=Path(output);identifier=f'{stratum["stratum_id"]}_{label}'
    target=out/(identifier+'.json');commit=out/'commitments'/(identifier+'.json')
    identity={'protocol_sha256':canonical_hash(protocol),'source_sha256':code_hash,
              'stratum':stratum,'label':label}
    if target.exists():
        saved=json.loads(target.read_text())
        if saved.get('resume_identity')!=identity:raise ValueError('Unsafe resume: '+identifier)
        if not commit.exists() or canonical_hash(json.loads(commit.read_text()))!=saved['pre_outcome_commitment_sha256']:
            raise ValueError('Commitment missing or modified: '+identifier)
        return saved
    raw=build_world(stratum,label,protocol)
    def persist(value,digest):
        if commit.exists() and canonical_hash(json.loads(commit.read_text()))!=digest:
            raise ValueError('Pre-outcome commitment changed: '+identifier)
        _atomic_json(commit,value)
    result=evaluate_world(raw,stratum,protocol,persist)
    result['resume_identity']=identity
    _atomic_json(target,result)
    return result


def run_protocol(protocol: dict, output: Path, workers: int=1) -> dict:
    validate_protocol(protocol)
    if type(workers) is not int or not 1<=workers<=8:raise ValueError('Workers must be in [1,8].')
    root=Path(__file__).resolve().parents[1];code_hash=source_hash(root)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    frozen={'protocol':protocol,'protocol_sha256':canonical_hash(protocol),'source_sha256':code_hash}
    freeze=output/'frozen_protocol.json'
    if freeze.exists() and json.loads(freeze.read_text())!=frozen:
        raise ValueError('Frozen experiment differs; use a distinct declared output.')
    _atomic_json(freeze,frozen)
    jobs=[(s,label,protocol,str(output),code_hash) for s in protocol['strata'] for label in protocol['seed_labels']]
    started=time.perf_counter();results=[]
    def accept(result):
        results.append(result)
        print(result['stratum_id'],result['seed_label'],'drafts',result['distinct_complete_drafts_attempted'],
              'failures',len(result['draft_failures']),'seconds',round(result['elapsed_seconds'],2),flush=True)
    if workers==1:
        for job in jobs:accept(_run_job(job))
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for future in as_completed([executor.submit(_run_job,j) for j in jobs]):accept(future.result())
    if source_hash(root)!=code_hash:raise ValueError('Source changed during the frozen run.')
    results.sort(key=lambda w:(w['stratum_id'],w['seed_label']))
    summary=summarize(results,protocol)
    summary.update(source_sha256=code_hash,executed_at_utc=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=time.perf_counter()-started,
        runtime={'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__,
                 'platform':platform.platform(),'workers':workers})
    _atomic_json(output/'summary.json',summary)
    return summary
