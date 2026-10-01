#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, sys, time
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from calibrate_v0319_convergence import setup,load_world,broad_pool,replay_rosters,stage_check,CAL

SOURCE=ROOT/'historical/seat_results_v0318'
OUT=ROOT/'historical/calibration_v0319/resolved_audit_chunks'

def audit(snapshot,original):
    from research.incremental_runner import ExactCalendarIncrementalRunner
    from research.convergence import paired_rows,race_survivors,resolution

    d=json.loads(json.dumps(snapshot))
    d['methodology_config']['max_lineup_plans']=CAL['calibration']['lineup_scoring_safety_ceiling']
    d['methodology_config']['rollouts']=1
    runner=ExactCalendarIncrementalRunner(d,sweeps=1,maximum_storage_patterns=64)
    one=runner.run()
    if one['run_status']!='ROSTER_ONLY':raise RuntimeError(('engine-one-rollout',one['run_status'],one['messages']))
    if one['recommended_player_id']!=original['one_rollout_leader']:
        raise AssertionError(('one-rollout leader mismatch',one['recommended_player_id'],original['one_rollout_leader']))
    if len(one['ranked_candidates'])!=original['full_pool_candidate_count']:
        raise AssertionError(('full-pool count mismatch',len(one['ranked_candidates']),original['full_pool_candidate_count']))

    remaining=sum(p['actor_id']=='USER' for p in d['draft_state']['remaining_picks'])
    active=broad_pool(one,runner,remaining-1)
    prev=one['recommended_player_id'];stable=0;budget=2
    eps=CAL['calibration']['epsilon_utility_points'];minb=CAL['calibration']['minimum_resolution_budget']
    cfg=CAL['calibration']['broad_pool']
    original_by_budget={int(x['budget']):x for x in original['stages']}
    stages=[{'budget':1,'leader':prev,'candidate_count':len(one['ranked_candidates']),'source':'full_pool','resolved_under_rule':False}]
    stage_check(stages[0],original_by_budget[1])
    source_seen=False
    while budget<=128:
        leader,rows=paired_rows(runner,active,budget)
        stable=stable+1 if leader==prev else 0
        separated,worst=resolution(rows,eps)
        is_resolved=bool(budget>=minb and separated and stable>=1)
        stage={'budget':budget,'leader':leader,'candidate_count':len(active),
               'worst_ci95_high_points':worst,'separated':separated,'stable_streak':stable,
               'resolved_under_rule':is_resolved,
               'runner_up':rows[1]['player_id'] if len(rows)>1 else None,
               'runner_up_gap_points':-rows[1]['mean_diff_vs_leader_points'] if len(rows)>1 else None}
        stages.append(stage)
        if budget in original_by_budget:stage_check(stage,original_by_budget[budget])
        if budget==original['final_budget']:
            if not is_resolved:raise AssertionError(('source resolution not reproduced',budget,leader))
            if leader!=original['final_leader']:raise AssertionError(('source final leader mismatch',leader,original['final_leader']))
            source_seen=True
        if budget==128:break
        active=race_survivors(rows,eps,min_keep=cfg['min_keep'],max_keep=cfg['max_keep'],force={leader,prev})
        prev=leader;budget*=2
    if not source_seen:raise AssertionError('Source resolved checkpoint was not reached.')
    next_budget=min(int(original['final_budget'])*2,128)
    nxt=next(x for x in stages if x['budget']==next_budget)
    s128=next(x for x in stages if x['budget']==128)
    source_leader=original['final_leader']
    return {
      'source_resolution_budget':original['final_budget'],
      'source_resolved_leader':source_leader,
      'next_checkpoint_budget':next_budget,
      'next_checkpoint_leader':nxt['leader'],
      'next_checkpoint_resolved':nxt['resolved_under_rule'],
      'source_leader_matches_next':source_leader==nxt['leader'],
      'source_leader_matches_128':source_leader==s128['leader'],
      'resolved_at_128':s128['resolved_under_rule'],
      'leader_at_128':s128['leader'],
      'stages':stages
    }

def main():
    setup()
    ap=argparse.ArgumentParser();ap.add_argument('season',choices=CAL['development_seasons']);ap.add_argument('seat',type=int,choices=range(1,7));args=ap.parse_args()
    source_path=SOURCE/f'{args.season}_seat{args.seat}.json';source_bytes=source_path.read_bytes();record=json.loads(source_bytes)
    world=load_world(args.season);focus=world['team_ids'][args.seat-1]
    trace=record['results']['DECISION_ENGINE']['draft']['trace']
    decisions=[d for d in record['results']['DECISION_ENGINE']['draft']['decisions'] if d['status']=='RESOLVED_WITHIN_ADAPTIVE_RACE']
    rows=[];started=time.perf_counter()
    for n,original in enumerate(decisions,1):
        idx=int(original['pick_index']);rosters=replay_rosters(world,trace,idx)
        from research.drafts import snapshot_for
        snap=snapshot_for(world,rosters,idx,focus);t=time.perf_counter();ev=audit(snap,original)
        row={'season':args.season,'seat':args.seat,'focus_team':focus,'pick_index':idx,'round':int(original['round']),
             'player_id':original['player_id'],'elapsed_seconds':time.perf_counter()-t,**ev}
        rows.append(row)
        print(args.season,'seat',args.seat,'decision',n,'/',len(decisions),'round',row['round'],
              'source_budget',row['source_resolution_budget'],'match_next',row['source_leader_matches_next'],
              'match_128',row['source_leader_matches_128'],'resolved_128',row['resolved_at_128'],flush=True)
    OUT.mkdir(parents=True,exist_ok=True)
    out={'version':'v0.3.19-resolved-stability-audit','season':args.season,'seat':args.seat,
         'source_seat_result_sha256':hashlib.sha256(source_bytes).hexdigest(),'rows':rows,'elapsed_seconds':time.perf_counter()-started}
    path=OUT/f'{args.season}_seat{args.seat}.json';path.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps({'file':str(path),'rows':len(rows),'elapsed_seconds':out['elapsed_seconds']},indent=2))

if __name__=='__main__':main()
