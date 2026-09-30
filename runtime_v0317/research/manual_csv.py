"""Lossless, strict UTF-8 CSV transport for normalized snapshots. Offline only.

Rules remain explicit JSON; numerical tables are long-form CSV. No inferred
eligibility, no name matching, no missing-to-zero conversion, no silent dedupe.
"""
from __future__ import annotations
import copy
import csv
import hashlib
import json
import math
from pathlib import Path
from decision_engine.validation import validate, EngineError, canonical_hash

HEADERS={
'players.csv':['player_id','sport_code','display_name','real_team_id','platform_player_id','rosterable','eligibility','primary_position','eligibility_source','eligibility_as_of','status_tags','external_ids_json'],
'projections.csv':['player_id','stat_key','value'],
'ownership.csv':['team_id','player_id','acquisition_type','reported_slot_definition_id','construction_lock_json'],
'period_projections.csv':['scenario_id','player_id','period_id','stat_key','value'],
'reference.csv':['reference_id','stat_key','value']}

def err(code,text):raise EngineError(code,text)
def number(text):
    if not text.strip():err('CSV_MISSING_VALUE','Missing numerical value is not zero.')
    try:v=float(text)
    except ValueError:err('CSV_INVALID_NUMBER','Invalid numerical value.')
    if not math.isfinite(v):err('CSV_NONFINITE','Nonfinite value rejected.')
    return v

def unique_key(seen,key):
    if key in seen:err('CSV_DUPLICATE_KEY',str(key))
    seen.add(key)

def export_bundle(data,directory):
    data=validate(data);base=Path(directory);base.mkdir(parents=True,exist_ok=True)
    cfg=copy.deepcopy(data);lp={p['player_id']:p for p in data['league_players']};rows={k:[] for k in HEADERS}
    for p in sorted(data['players'],key=lambda p:p['player_id']):
        q=lp[p['player_id']];e=q['eligibility']
        if any('|' in x for x in e['position_ids']+q['status_tags']):err('CSV_DELIMITER_IN_VALUE','Pipe delimiter inside enum is not supported.')
        rows['players.csv'].append([p['player_id'],p['sport_code'],p['display_name'],p.get('real_team_id',''),q.get('platform_player_id',''),str(q['rosterable']).lower(),'|'.join(e['position_ids']),e.get('primary_position_id',''),e['source'],e['as_of'],'|'.join(q['status_tags']),json.dumps(p.get('external_ids',[]),ensure_ascii=False,separators=(',',':'))])
    cfg['players']=[];cfg['league_players']=[]
    for p in data['projection_set']['player_projections']:
        if set(p)!={'player_id','stat_values'}:err('CSV_UNSUPPORTED_PROJECTION_METADATA','Extra projection metadata requires an explicit transport extension.')
        for k,v in sorted(p['stat_values'].items()):rows['projections.csv'].append([p['player_id'],k,repr(v)])
    cfg['projection_set']['player_projections']=[]
    for team in data['roster_state']['teams']:
        for e in team['entries']:
            rows['ownership.csv'].append([team['team_id'],e['player_id'],e['acquisition_type'],e.get('reported_slot_definition_id',''),json.dumps(e['construction_lock'],sort_keys=True) if 'construction_lock' in e else ''])
    for t in cfg['roster_state']['teams']:t['entries']=[]
    for s in data['exposure_model']['forecast_scenarios']:
        for p,periods in sorted(s['player_period_stats'].items()):
            for t,stats in sorted(periods.items()):
                for k,v in sorted(stats.items()):rows['period_projections.csv'].append([s['scenario_id'],p,t,k,repr(v)])
    for s in cfg['exposure_model']['forecast_scenarios']:s['player_period_stats']={}
    for f in data['reference_model']['field']:
        for k,v in sorted(f['stat_values'].items()):rows['reference.csv'].append([f['reference_id'],k,repr(v)])
    for f in cfg['reference_model']['field']:f['stat_values']={}
    config=base/'league.json';config.write_text(json.dumps(cfg,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    for name,values in rows.items():
        with (base/name).open('w',encoding='utf-8',newline='') as h:
            writer=csv.writer(h);writer.writerow(HEADERS[name]);writer.writerows(sorted(values,key=lambda x:tuple(str(v) for v in x)))
    manifest={'format':'decision-engine-manual-csv/0.3.5','description':'CSV transport, not independent source-vintage verification. Editing requires resealing hashes.',
              'files':{name:hashlib.sha256((base/name).read_bytes()).hexdigest() for name in ['league.json',*HEADERS]},
              'source_snapshot_sha256':canonical_hash(data)}
    (base/'bundle.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest

def import_bundle(directory):
    base=Path(directory);manifest=json.loads((base/'bundle.json').read_text(encoding='utf-8-sig'))
    if manifest.get('format')!='decision-engine-manual-csv/0.3.5':err('CSV_FORMAT','Unknown bundle version.')
    if set(manifest['files'])!={'league.json',*HEADERS}:err('CSV_FILE_SET','Exact supported file set required.')
    for name,digest in manifest['files'].items():
        p=(base/name).resolve()
        if not p.is_relative_to(base.resolve()) or p.is_symlink():err('CSV_PATH','Unsafe source path.')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:err('CSV_HASH_MISMATCH',name)
    d=json.loads((base/'league.json').read_text(encoding='utf-8-sig'))
    if d['players'] or d['league_players'] or d['projection_set']['player_projections'] or any(t['entries'] for t in d['roster_state']['teams']):err('CSV_DUPLICATED_TABLE','JSON and CSV cannot both supply the same table.')
    if any(s['player_period_stats'] for s in d['exposure_model']['forecast_scenarios']) or any(f['stat_values'] for f in d['reference_model']['field']):err('CSV_DUPLICATED_TABLE','Duplicate scenario/reference values.')
    tables={}
    for name,headers in HEADERS.items():
        with (base/name).open(encoding='utf-8-sig',newline='') as h:
            reader=csv.DictReader(h)
            if reader.fieldnames!=headers:err('CSV_HEADERS',name+' headers must match contract exactly.')
            tables[name]=list(reader)
            if any(None in r or any(v is None for v in r.values()) for r in tables[name]):err('CSV_ROW_WIDTH',name)
    seen=set()
    for r in tables['players.csv']:
        pid=r['player_id'];unique_key(seen,pid)
        if r['rosterable'] not in ('true','false'):err('CSV_BOOLEAN','rosterable must be true or false.')
        eligibility=r['eligibility'].split('|') if r['eligibility'] else []
        if len(set(eligibility))!=len(eligibility):err('CSV_DUPLICATE_ELIGIBILITY',pid)
        player={'player_id':pid,'sport_code':r['sport_code'],'display_name':r['display_name']}
        if r['real_team_id']:player['real_team_id']=r['real_team_id']
        external=json.loads(r['external_ids_json'])
        if external:player['external_ids']=external
        d['players'].append(player)
        e={'position_ids':eligibility,'source':r['eligibility_source'],'as_of':r['eligibility_as_of']}
        if r['primary_position']:e['primary_position_id']=r['primary_position']
        q={'player_id':pid,'league_id':d['league']['league_id'],'rosterable':r['rosterable']=='true','eligibility':e,'status_tags':r['status_tags'].split('|') if r['status_tags'] else []}
        if r['platform_player_id']:q['platform_player_id']=r['platform_player_id']
        d['league_players'].append(q)
    projections={};seen=set()
    for r in tables['projections.csv']:
        key=(r['player_id'],r['stat_key']);unique_key(seen,key)
        projections.setdefault(r['player_id'],{})[r['stat_key']]=number(r['value'])
    d['projection_set']['player_projections']=[{'player_id':p,'stat_values':v} for p,v in sorted(projections.items())]
    teams={t['team_id']:t for t in d['roster_state']['teams']};seen=set()
    for r in tables['ownership.csv']:
        unique_key(seen,r['player_id'])
        if r['team_id'] not in teams:err('CSV_UNKNOWN_TEAM',r['team_id'])
        e={'player_id':r['player_id'],'acquisition_type':r['acquisition_type']}
        if r['reported_slot_definition_id']:e['reported_slot_definition_id']=r['reported_slot_definition_id']
        if r['construction_lock_json']:e['construction_lock']=json.loads(r['construction_lock_json'])
        teams[r['team_id']]['entries'].append(e)
    scenarios={s['scenario_id']:s for s in d['exposure_model']['forecast_scenarios']};seen=set()
    for r in tables['period_projections.csv']:
        key=tuple(r[k] for k in ['scenario_id','player_id','period_id','stat_key']);unique_key(seen,key)
        if r['scenario_id'] not in scenarios:err('CSV_UNKNOWN_SCENARIO',r['scenario_id'])
        scenarios[r['scenario_id']]['player_period_stats'].setdefault(r['player_id'],{}).setdefault(r['period_id'],{})[r['stat_key']]=number(r['value'])
    fields={f['reference_id']:f for f in d['reference_model']['field']};seen=set()
    for r in tables['reference.csv']:
        unique_key(seen,(r['reference_id'],r['stat_key']))
        if r['reference_id'] not in fields:err('CSV_UNKNOWN_REFERENCE',r['reference_id'])
        fields[r['reference_id']]['stat_values'][r['stat_key']]=number(r['value'])
    return validate(d)
