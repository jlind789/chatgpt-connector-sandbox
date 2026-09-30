#!/usr/bin/env python3
from __future__ import annotations
import csv, json, math, re, sys, unicodedata, zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RUNTIME_ZIP=ROOT/'Fantasy-Decision-Engine-v0.3.17-Development-Backtest-Runtime.zip'
RUNTIME=ROOT/'.runtime_v0317'
PROTOCOL=ROOT/'historical/protocol-v0.3.18-development-freeze.json'
OUT=ROOT/'historical/generated_v0318'
SEASONS={
    '2022-23': {'code':'20222023','input':'2022-23'},
    '2023-24': {'code':'20232024','input':'2023-24'},
}

def ensure_runtime():
    if not (RUNTIME/'decision_engine').exists():
        RUNTIME.mkdir(exist_ok=True)
        with zipfile.ZipFile(RUNTIME_ZIP) as z:z.extractall(RUNTIME)
    sys.path.insert(0,str(RUNTIME))

def fnum(x,default=0.0):
    try:
        if x is None or str(x).strip()=='':return default
        return float(x)
    except:return default

def norm_name(x):
    x=unicodedata.normalize('NFKD',x or '').encode('ascii','ignore').decode().lower()
    x=re.sub(r'\b(jr|sr|ii|iii|iv)\b','',x)
    x=re.sub(r'[^a-z0-9]+','',x)
    return x

def monday_anchor(d):
    return d-timedelta(days=d.weekday())

def read_csv(p):
    with p.open(newline='',encoding='utf-8-sig') as f:return list(csv.DictReader(f))

def exact_team_dates(schedule):
    team=defaultdict(set); dates=set()
    for r in schedule:
        d=r['game_date'];dates.add(d)
        team[r['away_team']].add(d);team[r['home_team']].add(d)
    return team,sorted(dates)

def categories(count_category,ratio_category):
    cats=[
      count_category('G','goals'),count_category('A','assists'),
      count_category('FOW','faceoff_wins'),count_category('SOG','shots'),
      count_category('HIT','hits'),count_category('BLK','blocks'),
      count_category('DEF','defense_points','DEFENDER'),
      count_category('W','wins','GOALIE'),count_category('SVS','saves','GOALIE'),
      count_category('SO','shutouts','GOALIE'),
      ratio_category('SVPCT','saves','shots_against','GOALIE')
    ]
    return cats

def eligibility(raw):
    out=[]
    for x in (raw or '').replace(' ','').split(','):
        if not x:continue
        x={'L':'LW','R':'RW'}.get(x,x)
        if x not in out:out.append(x)
    return out

def projected_stats(row):
    isg=row['type'].lower()=='goalie'
    vals={'games':fnum(row['GP']),'goals':0.,'assists':0.,'faceoff_wins':0.,'shots':0.,
          'hits':0.,'blocks':0.,'defense_points':0.,'wins':0.,'saves':0.,
          'shutouts':0.,'shots_against':0.}
    if isg:
        vals['wins']=fnum(row['W']);vals['saves']=fnum(row['SVS']);vals['shutouts']=fnum(row['SO'])
        svp=fnum(row['SV_pct'])
        vals['shots_against']=vals['saves']/svp if svp>0 else vals['saves']
    else:
        vals['goals']=fnum(row['G']);vals['assists']=fnum(row['A']);vals['faceoff_wins']=fnum(row['FOW'])
        vals['shots']=fnum(row['SOG']);vals['hits']=fnum(row['HIT']);vals['blocks']=fnum(row['BLK'])
        vals['defense_points']=fnum(row['DEF'])
    return vals

def build_mapping(preseason,outcomes):
    bynorm=defaultdict(lambda:{'ids':set(),'names':set(),'teams':set()})
    for r in outcomes:
        n=norm_name(r['player'])
        if not n:continue
        bynorm[n]['ids'].add(str(r['player_id']));bynorm[n]['names'].add(r['player'])
        if r.get('team'):bynorm[n]['teams'].add(r['team'])
    rows=[];matched=ambiguous=0
    for pr in preseason:
        n=norm_name(pr['player']);cell=bynorm.get(n)
        status='NO_REALIZED_NHL_GAME'
        nhlid=''
        realized_names=''
        if cell:
            realized_names='|'.join(sorted(cell['names']))
            if len(cell['ids'])==1:
                status='MATCHED_UNIQUE_NAME';nhlid=next(iter(cell['ids']));matched+=1
            else:
                same_team=[]
                for pid in cell['ids']:
                    # We only have aggregate name/team sets here; do not pretend a team disambiguation if IDs share a name.
                    pass
                status='AMBIGUOUS_NAME';ambiguous+=1
        rows.append({'player':pr['player'],'preseason_team':pr['team'],'espn_positions':pr['espn_positions'],
                     'normalized_name':n,'nhl_player_id':nhlid,'realized_names':realized_names,'status':status})
    return rows,matched,ambiguous

def sum_plan_by_week(data,plan,E):
    period_to_group={p['period_id']:p['evaluation_group_id'] for p in data['exposure_model']['periods']}
    group_locks=defaultdict(list)
    for p in data['exposure_model']['periods']:group_locks[p['evaluation_group_id']].append(p['lock_group_id'])
    stat_keys=[s['stat_key'] for s in data['league']['stat_definitions']]
    out={}
    for gid,locks in group_locks.items():
        s={k:0. for k in stat_keys}
        for lock in locks:
            for a in plan.get(lock,[]):
                pid=a['player_id']
                for k,v in E[pid][lock].items():s[k]+=v
        out[gid]=s
    return out

def build_season(season, cfg):
    from decision_engine.fixtures import build_fixture,count_category,ratio_category
    from decision_engine.validation import validate,canonical_hash
    from research.drafts import pure_baseline_draft
    from research.independent import lineup_plan,expected_period_stats

    pre=read_csv(RUNTIME/'historical-backtest-inputs-v0.3.17'/cfg['input']/'preseason_players.csv')
    schedule=read_csv(ROOT/'data/historical'/cfg['code']/'schedule.csv')
    outcomes=read_csv(ROOT/'data/historical'/cfg['code']/'player_game_stats.csv')
    team_dates,dates=exact_team_dates(schedule)
    if len(schedule)!=1312:raise RuntimeError(f'{season}: schedule expected 1312, got {len(schedule)}')
    first=date.fromisoformat(dates[0]);last=date.fromisoformat(dates[-1]);anchor=monday_anchor(first)
    week_count=((last-anchor).days//7)+1
    date_index={d:i for i,d in enumerate(dates)}
    weeks={d:f'W{((date.fromisoformat(d)-anchor).days//7)+1:02d}' for d in dates}

    players={};projection={};names={};real_team={}
    for i,row in enumerate(pre):
        pid=f'{cfg["code"]}_{i:04d}';elig=eligibility(row['espn_positions'])
        if not elig:continue
        s=projected_stats(row);projection[pid]=s;names[pid]=row['player'];real_team[pid]=row['team']
        tdates=team_dates.get(row['team'],set());nteam=len(tdates)
        sched={}
        if nteam:
            for d in tdates:
                if d not in date_index:continue
                vals={k:(v/nteam) for k,v in s.items()}
                sched[date_index[d]]=vals
        players[pid]=(elig,sched)

    # Standard HoH-shaped redraft benchmark. No personal strategy targets.
    slots=[('C',2,['C'],'ACTIVE'),('LW',2,['LW'],'ACTIVE'),('RW',2,['RW'],'ACTIVE'),('D',4,['D'],'ACTIVE'),
           ('UTIL',2,['C','LW','RW','D'],'ACTIVE'),('G',2,['G'],'ACTIVE'),('BN',5,[],'BENCH')]
    order=[]
    for rnd in range(19):order += list(range(6)) if rnd%2==0 else list(reversed(range(6)))
    actors=['USER' if i==0 else f'anon{i}' for i in order]
    # Placeholder field is replaced below using projection-only baseline rosters.
    zero={k:0. for k in next(iter(projection.values()))}
    d=build_fixture(f'historical_dev_{cfg["code"]}',slots,players,[],actors,[zero],
                    categories=categories(count_category,ratio_category),opponent_groups=[[] for _ in range(5)],
                    period_count=len(dates),rollouts=1,required_active=True)
    d['schema_version']='decision-engine-input/0.3.1'
    d['capability_profile']='HORIZON_SPLIT_EXACT_CALENDAR_V1'
    d['created_at']=json.loads((RUNTIME/'historical-backtest-inputs-v0.3.17/manifest.json').read_text())['seasons'][season]['declared_cutoff']+'T12:00:00Z'
    d['league']['season']=season;d['league']['draft_config']['draft_type']='SNAKE'
    d['league']['scoring_objective']={'type':'H2H_EACH_CATEGORY','category_tie_credit':0.5,'both_undefined_tie_credit':0.5,
        'rounding':'HALF_EVEN','horizon_id':f'{cfg["code"]}_full_regular_season_weeks','evaluation_period_count':week_count}
    # Correct scoring-role applicability: D-only DEF, forwards/defenders other skater categories.
    for p in d['league']['positions']:
        p['position_group_id']='GOALIE' if p['position_id']=='G' else 'DEFENDER' if p['position_id']=='D' else 'FORWARD'
    for c in d['league']['categories']:
        if c['category_id']=='DEF':c['applicable_position_group_ids']=['DEFENDER']
        elif c['applicable_position_group_ids']==['SKATER']:c['applicable_position_group_ids']=['FORWARD','DEFENDER']
    # Replace projection totals with original season projections; build_fixture totals were exposure sums.
    d['projection_set']={'projection_set_id':f'crome_{cfg["code"]}_frozen','sport_code':'HOCKEY','season':season,
        'horizon_id':f'{cfg["code"]}_season_intrinsic','source':'Frozen Crome Aggregate Projections workbook',
        'created_at':d['created_at'],'player_projections':[{'player_id':p,'stat_values':projection[p]} for p in sorted(projection)]}
    # Re-key periods to exact NHL dates and weeks.
    old_periods=d['exposure_model']['periods'];old_scenario=d['exposure_model']['forecast_scenarios'][0]
    pstats={}
    for pid in sorted(projection):
        pstats[pid]={}
        for day in dates:
            idx=date_index[day];oldid=f'd{idx+1}'
            pstats[pid][day]=old_scenario['player_period_stats'][pid][oldid]
    d['exposure_model']={'exposure_model_id':f'{cfg["code"]}_official_exact_calendar','calendar_id':f'official_nhl_{cfg["code"]}',
        'horizon_id':f'{cfg["code"]}_full_regular_season_weeks','as_of':d['created_at'],'data_kind':'ARCHIVED','exposure_stat_key':'games',
        'periods':[{'period_id':day,'lock_group_id':day,'starts_at':day+'T12:00:00Z','evaluation_group_id':weeks[day]} for day in dates],
        'evaluation_groups':[{'evaluation_group_id':f'W{i:02d}','weight':1.0} for i in range(1,week_count+1)],
        'projection_reconciliation_mode':'WEIGHTED_EVALUATION_GROUPS_TO_PROJECTION_TOTAL',
        'forecast_scenarios':[{'scenario_id':'crome_mean_exact_calendar','probability':1.0,'player_period_stats':pstats}]}
    d['manager_profile']['position_policies']=[]
    d['methodology_config'].update({'seed':1700+int(cfg['code'][:4]),'rollouts':1,'soft_policy_lambda':0.005,
        'neutral_temperature':6,'max_search_nodes':1000000,'max_lineup_plans':250000,'continuation_policy':'B3'})
    d['source_metadata']={'source_platform':'ESPN','adapter_name':'historical_crome_exact_calendar_v0318',
        'adapter_version':'0.3.18','imported_at':'2026-09-30T00:00:00Z'}
    # Player display names + identity are historical workbook identities, not current platform identities.
    for p in d['players']:p['display_name']=names[p['player_id']]
    # Placeholder normalization cohort is every rosterable frozen preseason player.
    d['reference_model']['normalization_player_ids']=sorted(projection)
    d['reference_model']['generation_policy']='BASELINE_MIXTURE_V1'
    d['reference_model']['field_id']='placeholder_projection_only'
    d['reference_model']['reference_model_id']='placeholder_projection_only'
    d['reference_model']['field']=[{'reference_id':'placeholder','weight':1.0,'stat_values':zero,
        'evaluation_group_stat_values':{f'W{i:02d}':zero for i in range(1,week_count+1)}}]
    d=validate(d)

    team_ids=['user']+[f'opponent{i}' for i in range(1,6)]
    team_names=team_ids
    draft_order=[team_names[i] for i in order]
    world={'snapshot':d,'family':f'HISTORICAL_{cfg["code"]}','world_seed':int(cfg['code'][:4]),'team_ids':team_ids,
           'initial_rosters':{t:[] for t in team_ids},'draft_order':draft_order,'roster_size':19,
           'public_generator_version':'HISTORICAL_CROME_EXACT_CALENDAR_V0318','keeper_mode':False}

    # Projection-only reference rosters; historical outcomes are not read here.
    ref_rosters=[]
    for policy in ('B1','B2','B3'):
        dr=pure_baseline_draft(world,policy)
        if dr['status']!='COMPLETE':raise RuntimeError(f'{season} reference {policy} failed: {dr["status"]}')
        for team,roster in sorted(dr['rosters'].items()):
            ref_rosters.append((policy,team,roster))
    E=expected_period_stats(d);field=[];ref_trace=[]
    for policy,team,roster in ref_rosters:
        plan=lineup_plan(d,roster)
        grouped=sum_plan_by_week(d,plan,E)
        avg={k:sum(grouped[g][k] for g in grouped)/len(grouped) for k in zero}
        field.append({'reference_id':f'{policy}_{team}','weight':1.0,'stat_values':avg,'evaluation_group_stat_values':grouped})
        ref_trace.append({'policy':policy,'team_id':team,'roster':roster})
    d['reference_model']={'reference_model_id':f'{cfg["code"]}_projection_only_baseline_mixture',
        'as_of':d['created_at'],'normalization_player_ids':sorted(projection),
        'field_id':f'{cfg["code"]}_projection_only_B1_B2_B3','generation_policy':'BASELINE_MIXTURE_V1','field':field}
    d=validate(d);world['snapshot']=d
    world['reference_generation']={'version':'HISTORICAL_PROJECTION_ONLY_BASELINE_MIXTURE_V0318',
        'historical_outcomes_used':False,'baseline_rosters':ref_trace,'field_sha256':canonical_hash(field)}

    mapping,matched,ambiguous=build_mapping(pre,outcomes)
    # Attach NHL outcome identity map only outside the recommendation snapshot.
    by_player={r['player']:r for r in mapping}
    player_outcome_map={}
    for pid,nm in names.items():
        m=by_player.get(nm)
        player_outcome_map[pid]=m['nhl_player_id'] if m else ''

    od=OUT/season;od.mkdir(parents=True,exist_ok=True)
    (od/'world.json').write_text(json.dumps(world,indent=2)+'\n')
    with (od/'player_identity_map.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=mapping[0].keys());w.writeheader();w.writerows(mapping)
    (od/'outcome_id_map.json').write_text(json.dumps(player_outcome_map,indent=2)+'\n')
    audit={'season':season,'protocol':'historical-development/0.3.18-preoutcome-freeze',
           'preseason_players':len(pre),'engine_players':len(projection),'official_games':len(schedule),
           'first_game':dates[0],'last_game':dates[-1],'weekly_evaluation_groups':week_count,
           'projection_only_reference_rosters':len(field),'identity_unique_matches':matched,
           'identity_ambiguous':ambiguous,'identity_no_realized_game':len(pre)-matched-ambiguous,
           'snapshot_sha256':canonical_hash(d),'reference_field_sha256':canonical_hash(field),
           'historical_outcome_values_used_to_build_recommendation_snapshot':False}
    (od/'fixture_audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    return audit

def main():
    ensure_runtime()
    protocol=json.loads(PROTOCOL.read_text())
    OUT.mkdir(parents=True,exist_ok=True)
    audits=[build_season(s,SEASONS[s]) for s in protocol['development_seasons']]
    summary={'version':'v0.3.18-fixture-freeze','protocol_sha256':__import__('hashlib').sha256(PROTOCOL.read_bytes()).hexdigest(),
             'historical_outcomes_used_for_recommendation_inputs':False,'audits':audits}
    (OUT/'fixture_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()

# trigger: v0.3.18-fixture-build
