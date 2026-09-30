#!/usr/bin/env python3
from __future__ import annotations
import argparse, gzip, hashlib, json, sys, time, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'.runtime_v0317'
RUNTIME_ZIP=ROOT/'Fantasy-Decision-Engine-v0.3.17-Development-Backtest-Runtime.zip'
GEN=ROOT/'historical/generated_v0318'
SOURCE=ROOT/'historical/seat_results_v0318'
OUT=ROOT/'historical/calibration_v0319/chunks'
CAL=json.loads((ROOT/'historical/protocol-v0.3.19-convergence-calibration.json').read_text())
PARENT=json.loads((ROOT/'historical/protocol-v0.3.18-development-freeze.json').read_text())
CHUNKS={1:(1,4),2:(5,8),3:(9,12),4:(13,16),5:(17,19)}

def setup():
    if not (RUNTIME/'decision_engine').exists():
        RUNTIME.mkdir(exist_ok=True)
        with zipfile.ZipFile(RUNTIME_ZIP) as z:z.extractall(RUNTIME)
    sys.path.insert(0,str(RUNTIME))

def load_world(season):
    with gzip.open(GEN/season/'world.json.gz','rt',encoding='utf-8') as f:return json.load(f)

def broad_pool(one,runner,later):
    ranked=one['ranked_candidates']
    score=[x['player_id'] for x in ranked]
    intr=[x['player_id'] for x in sorted(ranked,key=lambda x:(-x['intrinsic_value'],x['player_id']))]
    cfg=CAL['calibration']['broad_pool']
    pool=set(score[:cfg['top_decision_score']])|set(intr[:cfg['top_intrinsic']])|{one['recommended_player_id']}
    if cfg['include_B1']:
        p=runner.choose('B1',runner.owned,runner.available,later)
        if p:pool.add(p)
    if cfg['include_B3']:
        p=runner.choose('B3',runner.owned,runner.available,later)
        if p:pool.add(p)
    for rank in cfg['sentinel_ranks']:
        if rank<=len(score):pool.add(score[rank-1])
    return sorted(pool)

def replay_rosters(world,trace,pick_index):
    rosters={t:list(world['initial_rosters'][t]) for t in world['team_ids']}
    seen=0
    for row in trace:
        idx=int(row['pick_index'])
        if idx>=pick_index:break
        if idx!=seen:raise AssertionError(('noncontiguous trace',seen,idx))
        if world['draft_order'][idx]!=row['team_id']:raise AssertionError(('team mismatch',idx,world['draft_order'][idx],row['team_id']))
        rosters[row['team_id']].append(row['player_id']);seen+=1
    if seen!=pick_index:raise AssertionError(('trace prefix length',pick_index,seen))
    return rosters

def stage_check(stage,original):
    if stage['leader']!=original['leader']:
        raise AssertionError(('leader reproduction mismatch',stage['budget'],stage['leader'],original['leader']))
    if stage['candidate_count']!=original['candidate_count']:
        raise AssertionError(('candidate-count reproduction mismatch',stage['budget'],stage['candidate_count'],original['candidate_count']))
    if stage.get('stable_streak')!=original.get('stable_streak'):
        raise AssertionError(('stable-streak reproduction mismatch',stage['budget'],stage.get('stable_streak'),original.get('stable_streak')))
    if stage.get('separated')!=original.get('separated'):
        raise AssertionError(('separation reproduction mismatch',stage['budget'],stage.get('separated'),original.get('separated')))

def calibrate(snapshot,original):
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
    later=remaining-1
    active=broad_pool(one,runner,later)
    prev=one['recommended_player_id'];stable=0;budget=2
    eps=CAL['calibration']['epsilon_utility_points']
    minb=CAL['calibration']['minimum_resolution_budget']
    min_diag=CAL['calibration']['minimum_diagnostic_budget']
    max_diag=CAL['calibration']['maximum_diagnostic_budget']
    cfg=CAL['calibration']['broad_pool']
    original_by_budget={int(x['budget']):x for x in original['stages']}
    stages=[{'budget':1,'leader':prev,'candidate_count':len(one['ranked_candidates']),'source':'full_pool',
             'resolved_under_rule':False}]
    stage_check(stages[0],original_by_budget[1])
    first_resolution_budget=None;first_resolution_leader=None

    while budget<=max_diag:
        leader,rows=paired_rows(runner,active,budget)
        stable=stable+1 if leader==prev else 0
        separated,worst=resolution(rows,eps)
        is_resolved=bool(budget>=minb and separated and stable>=1)
        runner_up=rows[1]['player_id'] if len(rows)>1 else None
        gap=-rows[1]['mean_diff_vs_leader_points'] if len(rows)>1 else None
        stage={'budget':budget,'leader':leader,'candidate_count':len(active),
               'worst_ci95_high_points':worst,'separated':separated,'stable_streak':stable,
               'resolved_under_rule':is_resolved,'runner_up':runner_up,'runner_up_gap_points':gap}
        stages.append(stage)
        if budget in original_by_budget:
            stage_check(stage,original_by_budget[budget])
        if budget==original['final_budget']:
            if leader!=original['final_leader']:raise AssertionError(('original final leader mismatch',leader,original['final_leader']))
            if is_resolved:raise AssertionError(('original unresolved decision reproduced as resolved',budget,leader))
        if is_resolved and first_resolution_budget is None:
            first_resolution_budget=budget;first_resolution_leader=leader

        # Every source decision is carried through at least 128. Beyond 128,
        # continue only while the current checkpoint remains unresolved.
        if budget>=min_diag and is_resolved:break
        if budget==max_diag:break
        active=race_survivors(rows,eps,min_keep=cfg['min_keep'],max_keep=cfg['max_keep'],force={leader,prev})
        prev=leader;budget*=2

    terminal=stages[-1]
    stage128=next((x for x in stages if x['budget']==128),None)
    if stage128 is None:raise AssertionError('Calibration must reach budget 128.')
    return {
      'original_max_budget':original['max_budget'],
      'original_final_budget':original['final_budget'],
      'original_fallback_leader':original['final_leader'],
      'original_runner_up':original.get('final_runner_up'),
      'original_runner_up_gap_points':original.get('final_runner_up_gap_points'),
      'first_resolution_budget':first_resolution_budget,
      'first_resolution_leader':first_resolution_leader,
      'leader_at_128':stage128['leader'],
      'resolved_at_128':stage128['resolved_under_rule'],
      'terminal_budget':terminal['budget'],
      'terminal_leader':terminal['leader'],
      'terminal_runner_up':terminal.get('runner_up'),
      'terminal_runner_up_gap_points':terminal.get('runner_up_gap_points'),
      'terminal_resolved':terminal['resolved_under_rule'],
      'original_fallback_matches_128':original['final_leader']==stage128['leader'],
      'original_fallback_matches_terminal':original['final_leader']==terminal['leader'],
      'first_resolution_matches_128':None if first_resolution_budget is None else first_resolution_leader==stage128['leader'],
      'stages':stages
    }

def main():
    setup()
    ap=argparse.ArgumentParser()
    ap.add_argument('season',choices=CAL['development_seasons'])
    ap.add_argument('seat',type=int,choices=range(1,7))
    ap.add_argument('chunk',type=int,choices=sorted(CHUNKS))
    args=ap.parse_args()
    lo,hi=CHUNKS[args.chunk]
    source_path=SOURCE/f'{args.season}_seat{args.seat}.json'
    source_bytes=source_path.read_bytes()
    record=json.loads(source_bytes)
    world=load_world(args.season)
    focus=world['team_ids'][args.seat-1]
    if focus!=record['focus_team']:raise AssertionError(('focus mismatch',focus,record['focus_team']))
    trace=record['results']['DECISION_ENGINE']['draft']['trace']
    decisions=[d for d in record['results']['DECISION_ENGINE']['draft']['decisions']
               if d['status']=='UNRESOLVED_MAX_BUDGET' and lo<=int(d['round'])<=hi]
    rows=[];started=time.perf_counter()
    for n,original in enumerate(decisions,1):
        idx=int(original['pick_index'])
        rosters=replay_rosters(world,trace,idx)
        from research.drafts import snapshot_for
        snap=snapshot_for(world,rosters,idx,focus)
        t=time.perf_counter()
        cal=calibrate(snap,original)
        row={'season':args.season,'seat':args.seat,'focus_team':focus,
             'pick_index':idx,'round':int(original['round']),'player_id':original['player_id'],
             'source_status':original['status'],'source_fallback_used':original['fallback_used'],
             'elapsed_seconds':time.perf_counter()-t,**cal}
        rows.append(row)
        print(args.season,'seat',args.seat,'chunk',args.chunk,'decision',n,'/',len(decisions),
              'round',row['round'],'terminal',row['terminal_budget'],
              'resolved',row['terminal_resolved'],
              'fallback_match',row['original_fallback_matches_terminal'],flush=True)
    OUT.mkdir(parents=True,exist_ok=True)
    out={
      'version':'v0.3.19-convergence-calibration-chunk',
      'protocol_version':CAL['protocol_version'],
      'season':args.season,'seat':args.seat,'chunk':args.chunk,'round_range':[lo,hi],
      'source_seat_result_sha256':hashlib.sha256(source_bytes).hexdigest(),
      'source_unresolved_decisions_in_chunk':len(decisions),
      'rows':rows,'elapsed_seconds':time.perf_counter()-started
    }
    path=OUT/f'{args.season}_seat{args.seat}_chunk{args.chunk}.json'
    path.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps({'file':str(path),'rows':len(rows),'elapsed_seconds':out['elapsed_seconds']},indent=2),flush=True)

if __name__=='__main__':main()
