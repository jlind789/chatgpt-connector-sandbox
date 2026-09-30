"""Paired complete-draft tournaments; cluster by generated league, not outcome draws."""
from __future__ import annotations
import copy
from datetime import datetime,timezone
import json
import math
from pathlib import Path
import platform
import statistics
import time
import numpy as np
import scipy
from decision_engine.validation import canonical_hash
from .worlds import public_world,keyed_seed
from .reference import generate_reference_field
from .drafts import DraftContext,play_draft
from .independent import lineup_plan,hidden_tape,sum_plan,score_matchup

POLICIES=('B1','B2','B3','B4')


def evaluate_world(world,audit_draws=128,styles=('MIXED',)):
    context=DraftContext(world);data=world['snapshot'];cases=[]
    # All draft choices and lineup plans are frozen before the first audit tape exists.
    for style in styles:
        for focus in world['team_ids']:
            for policy in POLICIES:
                draft=play_draft(world,focus,policy,context,style)
                case={'family':world['family'],'teams':len(world['team_ids']),'world_seed':world['world_seed'],'focus_team':focus,'opponent_style':style,'policy':policy,'draft':draft,'status':draft['status']}
                if draft['status']=='COMPLETE':
                    plans={t:lineup_plan(data,r) for t,r in draft['rosters'].items()}
                    case['committed_lineup_plans']=plans
                    case['plan_commitment_sha256']=canonical_hash({'trace':draft['trace'],'plans':plans})
                    case['_scores']=[];case['_stats']=[];case['_category']={c['category_id']:[] for c in data['league']['categories']}
                cases.append(case)
    commitment_hash=canonical_hash([c.get('plan_commitment_sha256') for c in cases])
    tape_hashes=[]
    for draw in range(audit_draws):
        tape=hidden_tape(data,world['family'],keyed_seed(world['world_seed'],len(world['team_ids'])),draw)
        tape_hashes.append(canonical_hash(tape))
        for case in cases:
            if case['status']!='COMPLETE':continue
            stats={t:sum_plan(data,p,tape) for t,p in case['committed_lineup_plans'].items()}
            focus=case['focus_team'];matchups=[score_matchup(data['league'],stats[focus],stats[t]) for t in world['team_ids'] if t!=focus]
            case['_scores'].append(math.fsum(x[0] for x in matchups)/len(matchups))
            case['_stats'].append(stats[focus])
            for cid in case['_category']:case['_category'][cid].append(math.fsum(x[1][cid] for x in matchups)/len(matchups))
    for case in cases:
        if case['status']!='COMPLETE':continue
        scores=case.pop('_scores');category=case.pop('_category');stats=case.pop('_stats')
        case['category_share']=statistics.fmean(scores)
        case['outcome_draws']=len(scores)
        case['category_credits']={k:statistics.fmean(v) for k,v in category.items()}
        case['mean_realized_usable_stats']={k:statistics.fmean(v[k] for v in stats) for k in stats[0]}
        case['outcome_share_samples']=scores
    return {'world_id':data['league']['league_id'],'world_seed':world['world_seed'],'family':world['family'],'teams':len(world['team_ids']),'snapshot_sha256':canonical_hash(data),'reference_generation':world['reference_generation'],'replacement_anchor':context.replacement.provenance,'all_drafts_and_plans_committed_sha256':commitment_hash,'audit_tape_hash_root':canonical_hash(tape_hashes),'audit_draws':audit_draws,'cases':cases,'engine_calls':context.engine_calls,'unique_terminal_rosters_evaluated':len(context.evaluator.cache),'exact_lineup_plans_examined':context.evaluator.evaluated_plans,'timing_enabled':False}


def bootstrap_interval(values,seed,draws=2000):
    if len(values)<2:return None
    rng=np.random.default_rng(seed);a=np.asarray(values,dtype=float)
    means=np.mean(a[rng.integers(0,len(a),size=(draws,len(a)))],axis=1)
    return [float(x) for x in np.quantile(means,[.025,.975])]


def summarize(worlds):
    paired=[];errors=[];policy_scores={p:[] for p in POLICIES}
    for world in worlds:
        groups={}
        for c in world['cases']:
            groups.setdefault((c['focus_team'],c['opponent_style']),{})[c['policy']]=c
            if c['status']!='COMPLETE':errors.append({'world_id':world['world_id'],'focus_team':c['focus_team'],'policy':c['policy'],'status':c['status'],'error':c['draft'].get('error')})
        # Completeness required for an entire paired seat, not selectively by comparator.
        complete=[g for g in groups.values() if set(g)==set(POLICIES) and all(c['status']=='COMPLETE' for c in g.values())]
        if not complete:continue
        means={p:statistics.fmean(g[p]['category_share'] for g in complete) for p in POLICIES}
        for p in POLICIES:policy_scores[p].append(means[p])
        paired.append({'world_id':world['world_id'],'family':world['family'],'teams':world['teams'],'paired_seats':len(complete),'scores':means,'deltas_pp':{p:100*(means['B4']-means[p]) for p in POLICIES if p!='B4'}})
    def aggregate(rows):
        if not rows:return {}
        out={'league_clusters':len(rows),'paired_seat_blocks':sum(r['paired_seats'] for r in rows),'mean_category_share':{p:statistics.fmean(r['scores'][p] for r in rows) for p in POLICIES},'paired_engine_minus_baseline_pp':{}}
        for p in ('B1','B2','B3'):
            v=[r['deltas_pp'][p] for r in rows]
            out['paired_engine_minus_baseline_pp'][p]={'mean':statistics.fmean(v),'median':statistics.median(v),'min':min(v),'max':max(v),'league_cluster_bootstrap_95_interval':bootstrap_interval(v,keyed_seed('BOOT-v1',p,len(rows))),'positive_clusters':sum(x>1e-12 for x in v),'zero_clusters':sum(abs(x)<=1e-12 for x in v),'negative_clusters':sum(x< -1e-12 for x in v)}
        return out
    total_cases=sum(len(w['cases']) for w in worlds)
    return {'status':'SYNTHETIC_DEVELOPMENT_DIAGNOSTIC_NOT_PROMOTION','attempted_worlds':len(worlds),'attempted_complete_drafts':total_cases,'completed_drafts':total_cases-len(errors),'agent_or_draft_failures':errors,'overall':aggregate(paired),'by_stratum':{f'{f}/teams={n}':aggregate([r for r in paired if r['family']==f and r['teams']==n]) for f,n in sorted({(w['family'],w['teams']) for w in worlds})},'paired_league_clusters':paired,'confidence_scope':'Conditional synthetic-development intervals; no historical generalization or multiplicity-adjusted superiority claim. Outcome draws and draft seats are not treated as independent league clusters.','promotion_eligible':False,'strongest_baseline_preselected_for_historical_holdout':False}


def run_protocol(protocol,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    frozen=copy.deepcopy(protocol);protocol_hash=canonical_hash(frozen)
    frozen['sha256']=protocol_hash
    path=output/'frozen_protocol.json'
    if path.exists() and json.loads(path.read_text())!=frozen:raise ValueError('Refusing to overwrite a different frozen protocol.')
    path.write_text(json.dumps(frozen,indent=2)+'\n')
    started=time.perf_counter();results=[]
    for stratum in protocol['strata']:
        for seed in protocol['seeds']:
            w=public_world(stratum['family'],seed,stratum['teams'],protocol['engine_rollouts'])
            identifier=w['snapshot']['league']['league_id']
            artifact=output/(identifier+'.json')
            if artifact.exists():
                saved=json.loads(artifact.read_text())
                if saved['reference_generation']['generated_from_snapshot_sha256']!=canonical_hash(w['snapshot']) or saved['audit_draws']!=protocol['audit_draws'] or saved['reference_generation']['draws_per_roster']!=protocol['reference_forecast_draws_per_roster']:
                    raise ValueError('Existing world result does not match frozen inputs: '+identifier)
                results.append(saved)
                print(identifier,'RESUMED_VERIFIED',flush=True)
                continue
            w=generate_reference_field(w,protocol['reference_forecast_draws_per_roster'])
            world_started=time.perf_counter()
            result=evaluate_world(w,protocol['audit_draws'])
            result['elapsed_seconds']=time.perf_counter()-world_started
            results.append(result)
            (output/(identifier+'.json')).write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
            print(identifier,'drafts=',len(result['cases']),'errors=',sum(c['status']!='COMPLETE' for c in result['cases']),'seconds=',round(result['elapsed_seconds'],3),flush=True)
    summary=summarize(results)
    summary.update({'sum_world_execution_seconds':math.fsum(r['elapsed_seconds'] for r in results),'protocol_sha256':protocol_hash,'executed_at_utc':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':time.perf_counter()-started,'runtime':{'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__,'platform':platform.platform()}})
    (output/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    return summary
