"""Preregistered factor contrasts on fresh complete drafts and common management.

All decisions and both management plans are persisted before outcome generation.
Old v0.3.2 experiments are immutable. No hyperparameter search or promotion occurs.
"""
from __future__ import annotations
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
import scipy
from decision_engine.validation import canonical_hash
from .worlds import public_world,keyed_seed
from .uncertainty import ScenarioConfig,with_uncertainty
from .reference import generate_reference_field
from .drafts import DraftContext,play_draft
from .robust_scoring import CDFEvaluator,AdditiveManagementEvaluator
from .management import ForecastManager
from .independent import hidden_tape,sum_plan,score_matchup

VARIANTS=('E0','EU','ER','EUR','EA')
ALL_POLICIES=('B1','B2','B3')+VARIANTS
MANAGERS=('ADDITIVE','CATEGORY_EXPECTED')
BASELINES=('B1','B2','B3')
FACTORS={'E0':{'uncertainty':False,'large_reference':False,'internal_management':'CATEGORY_EXPECTED'},
 'EU':{'uncertainty':True,'large_reference':False,'internal_management':'CATEGORY_EXPECTED'},
 'ER':{'uncertainty':False,'large_reference':True,'internal_management':'CATEGORY_EXPECTED'},
 'EUR':{'uncertainty':True,'large_reference':True,'internal_management':'CATEGORY_EXPECTED'},
 'EA':{'uncertainty':True,'large_reference':True,'internal_management':'ADDITIVE'}}


def source_hash(root:Path) -> str:
    hashes={}
    for folder in ('decision_engine','research'):
        for p in sorted((root/folder).rglob('*')):
            if p.is_file() and p.suffix in ('.py','.json'):
                hashes[str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
    return canonical_hash(hashes)


def validate_protocol(p:dict) -> None:
    for name in ('experiment_id','strata','seed_labels','engine_rollouts','small_reference_draws','large_reference_draws','uncertainty','audit_draws','policies','management_policies','primary_comparison','purpose'):
        if name not in p:raise ValueError('Missing protocol field: '+name)
    if tuple(p['policies'])!=ALL_POLICIES or tuple(p['management_policies'])!=MANAGERS:
        raise ValueError('Protocol must include all declared models and common management policies.')
    if not p['strata'] or len({s['stratum_id'] for s in p['strata']})!=len(p['strata']):
        raise ValueError('Strata must be nonempty and uniquely identified.')
    if not p['seed_labels'] or len(set(p['seed_labels']))!=len(p['seed_labels']):raise ValueError('Fresh seed labels must be nonempty and unique.')
    if any(type(x) is not int or x<0 or x in range(101,107) for x in p['seed_labels']):raise ValueError('Invalid/reused diagnostic seed label.')
    for name in ('engine_rollouts','small_reference_draws','large_reference_draws','audit_draws'):
        if type(p[name]) is not int or p[name]<1:raise ValueError('Positive integer required: '+name)
    if p['small_reference_draws']>=p['large_reference_draws']:raise ValueError('Large field must contain more draws per roster.')
    ScenarioConfig(**p['uncertainty']).validate()
    for s in p['strata']:
        if s['public_family'] not in ('BALANCED','CALENDAR_CONGESTION','ROLE_REVERSAL','GOALIE_RATES') or s['audit_family'] not in ('BALANCED','CALENDAR_CONGESTION','ROLE_REVERSAL','GOALIE_RATES','UNOBSERVED_ROLE_SHIFT'):
            raise ValueError('Unsupported generator.')
        if s['teams'] not in (2,3):raise ValueError('Frozen experiment is tiny 2/3-team research only.')


def build_world(stratum:dict,label:int,protocol:dict) -> dict:
    seed=keyed_seed('PUBLIC_M033_FRESH_V1',stratum['stratum_id'],label) % 2147483647
    w=public_world(stratum['public_family'],seed,stratum['teams'],protocol['engine_rollouts'])
    # Auditor labels stay outside the normalized recommendation data.
    w['research_stratum']=stratum['stratum_id'];w['seed_label']=label
    return w


def build_variants(raw:dict,protocol:dict) -> tuple[dict,dict]:
    small=generate_reference_field(raw,protocol['small_reference_draws'])
    large=generate_reference_field(raw,protocol['large_reference_draws'])
    cdict=dict(protocol['uncertainty'])
    cdict['seed']=keyed_seed(cdict['namespace'],cdict['seed'],raw['snapshot']['methodology_config']['seed'])
    config=ScenarioConfig(**cdict)
    uncertain,provenance=with_uncertainty(raw['snapshot'],config)
    models={}
    for name in VARIANTS:
        w=copy.deepcopy(large if FACTORS[name]['large_reference'] else small)
        if FACTORS[name]['uncertainty']:
            w['snapshot']['exposure_model']=copy.deepcopy(uncertain['exposure_model'])
        w['variant_id']=name
        w['variant_factors']=copy.deepcopy(FACTORS[name])
        w['uncertainty_provenance']=copy.deepcopy(provenance) if FACTORS[name]['uncertainty'] else None
        models[name]=w
    return models,{'uncertainty':provenance,'small_reference':small['reference_generation'],
        'large_reference':large['reference_generation'],
        'reference_change':'More forecast samples from the same B1/B2/B3 roster pool; NOT additional roster diversity',
        'models':{k:{'factors':FACTORS[k],'snapshot_sha256':canonical_hash(v['snapshot'])} for k,v in models.items()}}


def audit_tape(data:dict,family:str,seed:int,draw:int) -> dict:
    """Outcome-only extension: undisclosed persistent player role changes.

    No role label, factor or audit namespace is supplied to an agent or planner.
    The role-shift test is not tailored to player identifier parity.
    """
    if family!='UNOBSERVED_ROLE_SHIFT':
        return hidden_tape(data,family,seed,draw,namespace='AUDIT_M033_V1')
    hidden=copy.deepcopy(data)
    for p in sorted(x['player_id'] for x in hidden['players']):
        rng=np.random.default_rng(keyed_seed('AUDIT_M033_ROLE_TRUTH',seed,p))
        factor=float(rng.choice([.55,1.45]))
        for s in hidden['exposure_model']['forecast_scenarios']:
            for stats in s['player_period_stats'][p].values():
                for k in ('goals','shots','assists'):
                    if k in stats:stats[k]*=factor
    # These modified means exist solely behind the audit boundary; never validate
    # or present them as a replacement for the public projection snapshot.
    return hidden_tape(hidden,'BALANCED',seed,draw,namespace='AUDIT_M033_ROLE_OUTCOMES')


def evaluate_controlled_world(raw:dict,stratum:dict,protocol:dict,commit_callback=None) -> dict:
    started=time.perf_counter()
    models,provenance=build_variants(raw,protocol)
    contexts={k:DraftContext(w,AdditiveManagementEvaluator if k=='EA' else CDFEvaluator) for k,w in models.items()}
    # Baseline rosters are drafted with untouched public projections and frozen B2.
    baseline_context=contexts['E0']
    # All strategies get EXACTLY the same public manager model for a given policy.
    # Additive manager uses the original point forecasts, expected-category manager
    # gets the shared U+R public model (not an agent-specific or hidden model).
    managers={'ADDITIVE':ForecastManager(models['E0']['snapshot']),
        'CATEGORY_EXPECTED':ForecastManager(models['EUR']['snapshot'])}
    cases=[];draft_failures=[]
    for focus in raw['team_ids']:
        for name in ALL_POLICIES:
            w=models['E0'] if name in BASELINES else models[name]
            ctx=baseline_context if name in BASELINES else contexts[name]
            draft=play_draft(w,focus,name if name in BASELINES else 'B4',ctx)
            if draft['status']!='COMPLETE':
                draft_failures.append({'focus_team':focus,'policy':name,'status':draft['status'],'error':draft.get('error')})
            for manager_name in MANAGERS:
                case={'focus_team':focus,'policy':name,'management_policy':manager_name,'status':draft['status'],
                    'draft':draft}
                if draft['status']=='COMPLETE':
                    plans={t:managers[manager_name].choose(roster,manager_name) for t,roster in draft['rosters'].items()}
                    case['committed_plans']=plans
                    case['commitment_sha256']=canonical_hash({'rosters':draft['rosters'],'trace':draft['trace'],'plans':plans})
                cases.append(case)
    committed_cases=copy.deepcopy(cases)
    for c in committed_cases:c['draft'].pop('elapsed_seconds',None)
    commitment={'world_id':raw['snapshot']['league']['league_id'],'source_snapshot_sha256':canonical_hash(raw['snapshot']),
        'model_provenance':provenance,'cases':committed_cases,'audit_outcomes_generated':False}
    commitment_sha=canonical_hash(commitment)
    if commit_callback is not None:commit_callback(commitment,commitment_sha)
    for case in cases:
        if case['status']=='COMPLETE':
            case['_scores']=[];case['_category']={c['category_id']:[] for c in raw['snapshot']['league']['categories']}
    audit_seed=keyed_seed('M033_AUDIT_ROOT',stratum['stratum_id'],raw['seed_label'])
    tape_hashes=[]
    for draw in range(protocol['audit_draws']):
        tape=audit_tape(raw['snapshot'],stratum['audit_family'],audit_seed,draw)
        tape_hashes.append(canonical_hash(tape))
        # Same committed roster+lineup can occur across many policies: aggregate once.
        stats_cache={}
        for case in cases:
            if case['status']!='COMPLETE':continue
            totals={}
            for team,plan in case['committed_plans'].items():
                key=canonical_hash(plan)
                if key not in stats_cache:stats_cache[key]=sum_plan(raw['snapshot'],plan,tape)
                totals[team]=stats_cache[key]
            focus=case['focus_team']
            pairs=[score_matchup(raw['snapshot']['league'],totals[focus],totals[t]) for t in raw['team_ids'] if t!=focus]
            case['_scores'].append(math.fsum(r[0] for r in pairs)/len(pairs))
            for cid in case['_category']:
                case['_category'][cid].append(math.fsum(r[1][cid] for r in pairs)/len(pairs))
    for case in cases:
        if case['status']=='COMPLETE':
            case['outcome_share_samples']=case.pop('_scores')
            case['category_share']=statistics.fmean(case['outcome_share_samples'])
            case['category_credits']={k:statistics.fmean(v) for k,v in case.pop('_category').items()}
    return {'world_id':raw['snapshot']['league']['league_id'],'seed_label':raw['seed_label'],
        'stratum_id':stratum['stratum_id'],'public_seed':raw['world_seed'],'teams':len(raw['team_ids']),
        'source_snapshot_sha256':canonical_hash(raw['snapshot']),'protocol_sha256':canonical_hash(protocol),
        'model_provenance':provenance,'pre_outcome_commitment_sha256':commitment_sha,
        'audit_tape_hash_root':canonical_hash(tape_hashes),'audit_draws':protocol['audit_draws'],
        'distinct_complete_drafts_attempted':len(raw['team_ids'])*len(ALL_POLICIES),
        'draft_failures':draft_failures,'scored_management_cases':len(cases),'cases':cases,
        'engine_calls':{k:v.engine_calls for k,v in contexts.items()},
        'lineup_plans_examined':{k:v.evaluator.evaluated_plans for k,v in contexts.items()},
        'independent_manager_plans_examined':{k:v.plans_examined for k,v in managers.items()},
        'timing_enabled':False,'elapsed_seconds':time.perf_counter()-started}


def contrast(rows:list,positive:str,negative:str,seed:int,resamples:int=4000) -> dict:
    """Pair within world, average seats first, stratified resampling of worlds."""
    if not rows:return {'mean_pp':None,'interval_95_pp':None,'world_clusters':0}
    values=[100*(r['scores'][positive]-r['scores'][negative]) for r in rows]
    groups={}
    for r,v in zip(rows,values):groups.setdefault(r['stratum_id'],[]).append(v)
    interval=None
    if all(len(v)>=2 for v in groups.values()):
        rng=np.random.default_rng(seed);sums=np.zeros(resamples)
        for name,v in sorted(groups.items()):
            a=np.asarray(v);idx=rng.integers(0,len(a),size=(resamples,len(a)))
            sums+=np.sum(a[idx],axis=1)
        interval=[float(x) for x in np.quantile(sums/len(rows),[.025,.975])]
    return {'mean_pp':statistics.fmean(values),'median_pp':statistics.median(values),
        'interval_95_pp':interval,'world_clusters':len(rows),'min_pp':min(values),'max_pp':max(values),
        'positive_clusters':sum(v>1e-12 for v in values),'negative_clusters':sum(v< -1e-12 for v in values),
        'tied_clusters':sum(abs(v)<=1e-12 for v in values)}


def summarize_controlled(results:list,protocol:dict) -> dict:
    summaries={};rows_by_manager={}
    for manager in MANAGERS:
        rows=[]
        for world in results:
            groups={}
            for c in world['cases']:
                if c['management_policy']==manager:groups.setdefault(c['focus_team'],{})[c['policy']]=c
            complete=[g for g in groups.values() if set(g)==set(ALL_POLICIES) and all(c['status']=='COMPLETE' for c in g.values())]
            if complete:
                rows.append({'world_id':world['world_id'],'stratum_id':world['stratum_id'],'paired_seats':len(complete),
                    'scores':{p:statistics.fmean(g[p]['category_share'] for g in complete) for p in ALL_POLICIES}})
        rows_by_manager[manager]=rows
        def report(r):
            return {'world_clusters':len(r),'paired_seats':sum(x['paired_seats'] for x in r),
                'mean_category_share':{p:statistics.fmean(x['scores'][p] for x in r) if r else None for p in ALL_POLICIES},
                'versus_B3':{p:contrast(r,p,'B3',keyed_seed('M033_BOOT',manager,p)) for p in VARIANTS},
                'factor_contrasts':{a+'_minus_'+b:contrast(r,a,b,keyed_seed('M033_FACTOR_BOOT',manager,a,b))
                    for a,b in [('EU','E0'),('ER','E0'),('EUR','ER'),('EUR','EU'),('EUR','E0'),('EA','EUR')]}}
        summaries[manager]={'overall':report(rows),'by_stratum':{sid:report([r for r in rows if r['stratum_id']==sid]) for sid in sorted(s['stratum_id'] for s in protocol['strata'])}}
    interaction=[]
    arows={r['world_id']:r for r in rows_by_manager['ADDITIVE']}
    for r in rows_by_manager['CATEGORY_EXPECTED']:
        a=arows.get(r['world_id'])
        if a:
            # Paired change in the EUR-versus-B3 advantage under common management.
            interaction.append({'world_id':r['world_id'],'stratum_id':r['stratum_id'],
                'scores':{'category':r['scores']['EUR']-r['scores']['B3'],
                    'additive':a['scores']['EUR']-a['scores']['B3']}})
    failures=[{'world_id':w['world_id'],**f} for w in results for f in w['draft_failures']]
    primary=summaries['ADDITIVE']['overall']['versus_B3']['EUR']
    return {'status':'FROZEN_SYNTHETIC_FACTOR_DIAGNOSTIC_NOT_PROMOTION',
        'attempted_worlds':len(results),'distinct_drafts_attempted':sum(w['distinct_complete_drafts_attempted'] for w in results),
        'distinct_drafts_completed':sum(w['distinct_complete_drafts_attempted'] for w in results)-len(failures),
        'scored_management_cases':sum(w['scored_management_cases'] for w in results),
        'draft_failures':failures,'protocol_sha256':canonical_hash(protocol),
        'primary_comparison':{'candidate':'EUR','comparator':'B3','common_manager':'ADDITIVE',**primary},
        'by_management':summaries,'paired_league_clusters':rows_by_manager,
        'management_interaction_EUR_vs_B3':contrast(interaction,'category','additive',keyed_seed('M033_MANAGEMENT_INTERACTION')),
        'uncertainty_scope':'Paired, stratified generated-league cluster percentile intervals; conditional on these synthetic generators. Outcomes, seats and management variants are NOT independent clusters. Exploratory contrasts are not multiplicity-adjusted.',
        'promotion_eligible':False,'historical_backtest_run':False,'parameter_tuning_after_audit':False}


def _run_job(job):
    stratum,label,protocol,output,code_hash=job
    raw=build_world(stratum,label,protocol);identifier=f'{stratum["stratum_id"]}_{label}'
    output=Path(output);target=output/(identifier+'.json');commit_path=output/'commitments'/(identifier+'.json')
    expected={'protocol':canonical_hash(protocol),'code':code_hash,'snapshot':canonical_hash(raw['snapshot'])}
    if target.exists():
        saved=json.loads(target.read_text())
        if saved.get('resume_identity')!=expected:raise ValueError('Refusing unsafe resume: '+identifier)
        c=json.loads(commit_path.read_text())
        if canonical_hash(c)!=saved['pre_outcome_commitment_sha256']:raise ValueError('Commitment artifact hash mismatch.')
        return saved
    def persist(commitment,digest):
        commit_path.parent.mkdir(exist_ok=True)
        if commit_path.exists() and canonical_hash(json.loads(commit_path.read_text()))!=digest:
            # Timings in partial runs are nonsemantic but a different commitment
            # requires a new run directory rather than overwriting an old audit.
            raise ValueError('Existing commitment differs; use a new declared run directory.')
        commit_path.write_text(json.dumps(commitment,indent=2,allow_nan=False)+'\n')
    result=evaluate_controlled_world(raw,stratum,protocol,persist)
    result['resume_identity']=expected
    target.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    return result


def run_controlled_protocol(protocol:dict,output:Path,workers:int=1) -> dict:
    validate_protocol(protocol)
    if type(workers) is not int or not 1<=workers<=8:raise ValueError('Workers must be an integer in [1,8].')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parents[1];code_hash=source_hash(root)
    frozen={'protocol':protocol,'protocol_sha256':canonical_hash(protocol),'source_sha256':code_hash,
        'source_archive_sha256':'41768f03c98585eef057a6675e5d3ca1c313df7cafabc0c2ca32e26c30442647'}
    path=output/'frozen_protocol.json'
    if path.exists() and json.loads(path.read_text())!=frozen:raise ValueError('Frozen protocol/code changed; refusing overwrite.')
    path.write_text(json.dumps(frozen,indent=2,allow_nan=False)+'\n')
    started=time.perf_counter();results=[]
    jobs=[(s,label,protocol,str(output),code_hash) for s in protocol['strata'] for label in protocol['seed_labels']]
    def accept(result):
        results.append(result)
        print(result['stratum_id'],result['seed_label'],'drafts',result['distinct_complete_drafts_attempted'],
            'errors',len(result['draft_failures']),'seconds',round(result['elapsed_seconds'],2),flush=True)
    if workers==1:
        for job in jobs:accept(_run_job(job))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for future in as_completed([pool.submit(_run_job,j) for j in jobs]):accept(future.result())
    results.sort(key=lambda r:(r['stratum_id'],r['seed_label']))
    if source_hash(root)!=code_hash:raise ValueError('Source changed during frozen run.')
    summary=summarize_controlled(results,protocol)
    summary.update({'elapsed_seconds':time.perf_counter()-started,
        'sum_world_execution_seconds':math.fsum(w['elapsed_seconds'] for w in results),
        'executed_at_utc':datetime.now(timezone.utc).isoformat(),'source_sha256':code_hash,
        'runtime':{'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__,'platform':platform.platform(),'workers':workers}})
    (output/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    return summary
