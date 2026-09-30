#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,gzip,json,math,statistics,sys,time,zipfile
from collections import defaultdict
from pathlib import Path
import numpy as np
from scipy.optimize import linear_sum_assignment

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'.runtime_v0317'
PROTO=json.loads((ROOT/'historical/protocol-v0.3.18-development-freeze.json').read_text())
GEN=ROOT/'historical/generated_v0318'
OUT=ROOT/'historical/seat_results_v0318'
SEASON_CODE={'2022-23':'20222023','2023-24':'20232024'}

def setup():
    if not (RUNTIME/'decision_engine').exists():
        RUNTIME.mkdir(exist_ok=True)
        with zipfile.ZipFile(ROOT/'Fantasy-Decision-Engine-v0.3.17-Development-Backtest-Runtime.zip') as z:z.extractall(RUNTIME)
    sys.path.insert(0,str(RUNTIME))

def read_csv(p):
    with p.open(newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))

def load_world(season):
    with gzip.open(GEN/season/'world.json.gz','rt',encoding='utf-8') as f:return json.load(f)

def load_outcomes(season):
    code=SEASON_CODE[season]
    rows=read_csv(ROOT/'data/historical'/code/'player_game_stats.csv')
    out=defaultdict(lambda:defaultdict(lambda:defaultdict(float)))
    for r in rows:
        pid=str(r['player_id']);d=r['game_date']
        vals=out[pid][d]
        for src,dst in [('G','goals'),('A','assists'),('FOW','faceoff_wins'),('SOG','shots'),('HIT','hits'),('BLK','blocks'),
                        ('W','wins'),('SVS','saves'),('SO','shutouts'),('SA','shots_against')]:
            vals[dst]+=float(r.get(src) or 0)
        vals['games']=1.0
        vals['_goalie_row']=1.0 if r.get('nhl_position')=='G' else 0.0
    return out

def max_budget(remaining):
    for x in PROTO['decision_engine']['adaptive_convergence']['max_budget_by_user_picks_remaining']:
        if remaining>=x['minimum_remaining']:return x['maximum_rollouts']
    raise AssertionError(remaining)

def broad_pool(one,runner,later):
    ranked=one['ranked_candidates'];score=[x['player_id'] for x in ranked]
    intr=[x['player_id'] for x in sorted(ranked,key=lambda x:(-x['intrinsic_value'],x['player_id']))]
    cfg=PROTO['decision_engine']['adaptive_convergence']['broad_pool']
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

def adaptive_pick(snapshot):
    from research.incremental_runner import ExactCalendarIncrementalRunner
    from research.convergence import paired_rows,race_survivors,resolution
    d=json.loads(json.dumps(snapshot))
    remaining=sum(p['actor_id']=='USER' for p in d['draft_state']['remaining_picks'])
    ceiling=max_budget(remaining)
    d['methodology_config']['rollouts']=1
    runner=ExactCalendarIncrementalRunner(d,sweeps=1,maximum_storage_patterns=64)
    one=runner.run()
    if one['run_status']!='ROSTER_ONLY':raise RuntimeError(('engine-one-rollout',one['run_status'],one['messages']))
    later=remaining-1
    active=broad_pool(one,runner,later)
    prev=one['recommended_player_id'];stable=0;budget=2
    final_rows=None;leader=prev;resolved_flag=False
    eps=PROTO['decision_engine']['adaptive_convergence']['epsilon_utility_points']
    minb=PROTO['decision_engine']['adaptive_convergence']['minimum_resolution_budget']
    cfg=PROTO['decision_engine']['adaptive_convergence']['broad_pool']
    stages=[{'budget':1,'leader':prev,'candidate_count':len(one['ranked_candidates']),'source':'full_pool'}]
    while budget<=ceiling:
        leader,rows=paired_rows(runner,active,budget);final_rows=rows
        stable=stable+1 if leader==prev else 0
        separated,worst=resolution(rows,eps)
        stages.append({'budget':budget,'leader':leader,'candidate_count':len(active),'worst_ci95_high_points':worst,
                       'separated':separated,'stable_streak':stable})
        if budget>=minb and separated and stable>=1:
            resolved_flag=True;break
        if budget==ceiling:break
        active=race_survivors(rows,eps,min_keep=cfg['min_keep'],max_keep=cfg['max_keep'],force={leader,prev})
        prev=leader;budget*=2
    status='RESOLVED_WITHIN_ADAPTIVE_RACE' if resolved_flag else 'UNRESOLVED_MAX_BUDGET'
    return leader,{'status':status,'fallback_used':not resolved_flag,'final_budget':budget,'max_budget':ceiling,
                   'one_rollout_leader':one['recommended_player_id'],'final_leader':leader,
                   'final_runner_up':final_rows[1]['player_id'] if final_rows and len(final_rows)>1 else None,
                   'final_runner_up_gap_points':-final_rows[1]['mean_diff_vs_leader_points'] if final_rows and len(final_rows)>1 else None,
                   'stages':stages,'full_pool_candidate_count':len(one['ranked_candidates'])}

def draft_primitives(world):
    from research.efficient_assignment import EfficientRosterSolver
    from decision_engine.scoring import IntrinsicModel
    from research.baselines import ReplacementAnchor
    data=world['snapshot'];solver=EfficientRosterSolver(data);iv=IntrinsicModel(data);anchor=ReplacementAnchor(data,iv)
    return solver,iv,anchor

def baseline_draft(world,focus,policy):
    from research.baselines import choose
    solver,iv,anchor=draft_primitives(world)
    rosters={t:list(world['initial_rosters'][t]) for t in world['team_ids']}
    pool={p['player_id'] for p in world['snapshot']['league_players'] if p['rosterable']}
    trace=[];started=time.perf_counter()
    for index,team in enumerate(world['draft_order']):
        later=world['draft_order'][index+1:].count(team)
        agent=policy if team==focus else ('B1','B3','B2')[index%3]
        p=choose(agent,rosters[team],pool,later,solver,iv,anchor)
        if p is None:return {'status':'NO_FEASIBLE_PICK','trace':trace,'rosters':rosters,'policy':policy,'focus_team':focus}
        pool.remove(p);rosters[team].append(p);trace.append({'pick_index':index,'team_id':team,'policy':agent,'player_id':p})
    for t,ids in rosters.items():
        if not solver.best(ids,terminal=True):raise AssertionError('invalid baseline completed roster '+t)
    return {'status':'COMPLETE','policy':policy,'focus_team':focus,'rosters':{t:sorted(v) for t,v in rosters.items()},
            'trace':trace,'elapsed_seconds':time.perf_counter()-started}

def engine_draft(world,focus):
    from research.drafts import snapshot_for
    from research.baselines import choose
    rosters={t:list(world['initial_rosters'][t]) for t in world['team_ids']}
    solver,iv,anchor=draft_primitives(world)
    taken={p for ids in rosters.values() for p in ids}
    pool={p['player_id'] for p in world['snapshot']['league_players'] if p['rosterable']}-taken
    trace=[];decisions=[];started=time.perf_counter()
    for index,team in enumerate(world['draft_order']):
        later=world['draft_order'][index+1:].count(team)
        if team==focus:
            snap=snapshot_for(world,rosters,index,focus)
            chosen,evidence=adaptive_pick(snap)
            decisions.append({'pick_index':index,'round':index//len(world['team_ids'])+1,'player_id':chosen,**evidence})
            policy='DECISION_ENGINE'
        else:
            policy=('B1','B3','B2')[index%3]
            chosen=choose(policy,rosters[team],pool,later,solver,iv,anchor)
        if chosen is None:return {'status':'NO_FEASIBLE_PICK','trace':trace,'rosters':rosters,'decisions':decisions}
        if chosen not in pool:raise AssertionError('unavailable pick')
        reason=solver.candidate_reason(rosters[team],chosen,pool,later)
        if reason:raise AssertionError(('illegal pick',reason,index,chosen))
        pool.remove(chosen);rosters[team].append(chosen)
        trace.append({'pick_index':index,'team_id':team,'policy':policy,'player_id':chosen})
    for t,ids in rosters.items():
        if not solver.best(ids,terminal=True):raise AssertionError('invalid completed roster '+t)
    return {'status':'COMPLETE','trace':trace,'rosters':{t:sorted(v) for t,v in rosters.items()},
            'decisions':decisions,'elapsed_seconds':time.perf_counter()-started}

def active_slots(data):
    slots=[]
    for s in data['league']['roster_slots']:
        if s['slot_class']!='ACTIVE':continue
        for i in range(s['capacity']):slots.append((f'{s["slot_definition_id"]}#{i+1}',s))
    return slots

def common_lineup_manager(data,rosters,outcome_map,outcomes):
    """Forecast-only assignment weights; realized participation only controls availability."""
    from decision_engine.scoring import IntrinsicModel
    iv=IntrinsicModel(data);lp={p['player_id']:p for p in data['league_players']}
    slots=active_slots(data)
    period_dates=sorted(p['period_id'] for p in data['exposure_model']['periods'])
    p2g={p['period_id']:p['evaluation_group_id'] for p in data['exposure_model']['periods']}
    statkeys=[x['stat_key'] for x in data['league']['stat_definitions']]
    team_week={}
    started_contrib={}
    for team,roster in rosters.items():
        weeks=defaultdict(lambda:{k:0.0 for k in statkeys})
        contrib=defaultdict(lambda:{'started_days':0,'goals':0.,'assists':0.,'faceoff_wins':0.,'shots':0.,'hits':0.,'blocks':0.,'defense_points':0.,'wins':0.,'saves':0.,'shutouts':0.,'shots_against':0.})
        for d in period_dates:
            candidates=[]
            for pid in roster:
                nhl=outcome_map.get(pid) or ''
                if not nhl:continue
                actual=outcomes.get(nhl,{}).get(d)
                if not actual:continue
                # A backup goalie with no actual participation is not treated as an available start.
                if 'G' in lp[pid]['eligibility']['position_ids'] and actual.get('shots_against',0)<=0 and actual.get('saves',0)<=0 and actual.get('wins',0)<=0:
                    continue
                candidates.append(pid)
            if not candidates:continue
            costs=np.full((len(slots),len(candidates)+len(slots)),np.inf);costs[:,len(candidates):]=0.
            opts={}
            for j,pid in enumerate(candidates):
                elig=set(lp[pid]['eligibility']['position_ids']);opts[pid]=elig
                val=iv.value(pid)
                for i,(sid,s) in enumerate(slots):
                    ok=bool(elig) if s['eligibility']['mode']=='ANY_ROSTERABLE' else bool(elig & set(s['eligibility']['position_ids']))
                    if ok:costs[i,j]=-val
            rr,cc=linear_sum_assignment(costs)
            selected=[candidates[j] for i,j in zip(rr,cc) if j<len(candidates)]
            gid=p2g[d]
            for pid in selected:
                nhl=outcome_map[pid];a=outcomes[nhl][d]
                elig=set(lp[pid]['eligibility']['position_ids'])
                vals={k:float(a.get(k,0.0)) for k in statkeys}
                vals['defense_points']=(vals['goals']+vals['assists']) if 'D' in elig else 0.0
                for k in statkeys:weeks[gid][k]+=vals[k]
                contrib[pid]['started_days']+=1
                for k in contrib[pid]:
                    if k!='started_days':contrib[pid][k]+=vals.get(k,0.0)
        team_week[team]=dict(weeks);started_contrib[team]=dict(contrib)
    return team_week,started_contrib

def category_score(league,a,b):
    from decimal import Decimal,ROUND_HALF_EVEN
    out={}
    for c in league['categories']:
        calc=c['calculation']
        def value(x):
            if calc['type']=='COUNT':return x.get(calc['stat_key'],0.)
            den=x.get(calc['denominator_stat_key'],0.)
            return calc.get('multiplier',1.)*x.get(calc['numerator_stat_key'],0.)/den if den else None
        x,y=value(a),value(b)
        if x is None and y is None:r=league['scoring_objective']['both_undefined_tie_credit']
        elif x is None:r=0.
        elif y is None:r=1.
        else:
            q=Decimal(1).scaleb(-c['precision']);xx=Decimal(str(x)).quantize(q,rounding=ROUND_HALF_EVEN);yy=Decimal(str(y)).quantize(q,rounding=ROUND_HALF_EVEN)
            if xx==yy:r=league['scoring_objective']['category_tie_credit']
            else:r=float(xx>yy if c['direction']=='HIGHER_IS_BETTER' else xx<yy)
        out[c['category_id']]=r
    return sum(c['weight']*out[c['category_id']] for c in league['categories'])/sum(c['weight'] for c in league['categories']),out

def realized_focus_score(data,rosters,focus,outcome_map,outcomes):
    team_week,contrib=common_lineup_manager(data,rosters,outcome_map,outcomes)
    gids=[g['evaluation_group_id'] for g in data['exposure_model']['evaluation_groups']]
    vals=[];cat=defaultdict(list)
    for gid in gids:
        a=team_week.get(focus,{}).get(gid,{})
        for opp in rosters:
            if opp==focus:continue
            b=team_week.get(opp,{}).get(gid,{})
            s,c=category_score(data['league'],a,b);vals.append(s)
            for k,v in c.items():cat[k].append(v)
    return {'mean_weekly_category_share':statistics.fmean(vals) if vals else None,
            'matchup_comparisons':len(vals),
            'category_credits':{k:statistics.fmean(v) for k,v in cat.items()},
            'started_contributions':contrib.get(focus,{})}

def main():
    setup();ap=argparse.ArgumentParser();ap.add_argument('season',choices=PROTO['development_seasons']);ap.add_argument('seat',type=int,choices=range(1,7));args=ap.parse_args()
    season=args.season;seat=args.seat;world=load_world(season);data=world['snapshot'];focus=world['team_ids'][seat-1]
    outmap=json.loads((GEN/season/'outcome_id_map.json').read_text());outcomes=load_outcomes(season)
    names={p['player_id']:p['display_name'] for p in data['players']}
    results={}
    for policy in ('B1','B2','B3'):
        t=time.perf_counter();dr=baseline_draft(world,focus,policy)
        if dr['status']!='COMPLETE':raise RuntimeError((season,seat,policy,dr['status']))
        outcome=realized_focus_score(data,dr['rosters'],focus,outmap,outcomes)
        results[policy]={'draft':dr,'outcome':outcome,'elapsed_seconds':time.perf_counter()-t}
        print(season,'seat',seat,policy,'score',outcome['mean_weekly_category_share'],flush=True)
    t=time.perf_counter();dr=engine_draft(world,focus)
    if dr['status']!='COMPLETE':raise RuntimeError((season,seat,'ENGINE',dr['status']))
    outcome=realized_focus_score(data,dr['rosters'],focus,outmap,outcomes)
    decisions=dr['decisions'];resolved=[x for x in decisions if x['status']=='RESOLVED_WITHIN_ADAPTIVE_RACE'];unresolved=[x for x in decisions if x['status']=='UNRESOLVED_MAX_BUDGET']
    # Cohort reporting: realized started contribution is descriptive, not causal marginal draft value.
    contrib=outcome.pop('started_contributions')
    def cohort(ids):
        rows=[contrib.get(x['player_id'],{}) for x in ids]
        keys=['started_days','goals','assists','faceoff_wins','shots','hits','blocks','defense_points','wins','saves','shutouts','shots_against']
        return {'decision_count':len(ids),'realized_started_contribution':{k:sum(float(r.get(k,0)) for r in rows) for k in keys}}
    engine={'draft':dr,'outcome':outcome,'elapsed_seconds':time.perf_counter()-t,
            'confidence_reporting':{'ALL':cohort(decisions),'RESOLVED':cohort(resolved),'UNRESOLVED':cohort(unresolved),
                                    'unresolved_rate':len(unresolved)/len(decisions) if decisions else 0.0,
                                    'note':'Cohort contribution is descriptive started production, not a causal per-pick value estimate.'}}
    results['DECISION_ENGINE']=engine
    record={'version':'v0.3.18-development-seat','season':season,'seat':seat,'focus_team':focus,
            'protocol':PROTO,'results':results,
            'summary':{'scores':{p:results[p]['outcome']['mean_weekly_category_share'] for p in results},
                       'engine_minus_pp':{p:100*(results['DECISION_ENGINE']['outcome']['mean_weekly_category_share']-results[p]['outcome']['mean_weekly_category_share']) for p in ('B1','B2','B3')},
                       'engine_resolved_decisions':len(resolved),'engine_unresolved_decisions':len(unresolved),
                       'engine_unresolved_rate':len(unresolved)/len(decisions) if decisions else 0.0}}
    OUT.mkdir(parents=True,exist_ok=True);path=OUT/f'{season}_seat{seat}.json';path.write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record['summary'],indent=2),flush=True)

if __name__=='__main__':main()
