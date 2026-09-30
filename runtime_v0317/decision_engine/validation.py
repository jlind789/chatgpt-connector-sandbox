"""Strict wire/schema checks and cross-object validation. No inferred league rules."""
from __future__ import annotations
import copy
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker

class EngineError(Exception):
    def __init__(self, code: str, message: str, path: str='', status: str='INPUT_ERROR'):
        super().__init__(message)
        self.code, self.path, self.status = code, path, status
    def as_dict(self):
        return {'severity':'ERROR','code':self.code,'path':self.path,'message':str(self)}

class SearchLimit(EngineError):
    def __init__(self, context: str):
        super().__init__('EXACT_SEARCH_BUDGET_EXCEEDED',f'Deterministic search limit reached: {context}. No partial ranking is certified.',status='RESOURCE_LIMIT')

class Budget:
    def __init__(self, maximum: int, context: str): self.maximum,self.context,self.nodes=maximum,context,0
    def tick(self):
        self.nodes+=1
        if self.nodes>self.maximum: raise SearchLimit(self.context)

SCHEMA=json.loads(Path(__file__).with_name('input.schema.json').read_text())
VALIDATOR=Draft202012Validator(SCHEMA,format_checker=FormatChecker())

def category_keys(category: dict) -> set[str]:
    c=category['calculation']
    return {c['stat_key']} if c['type']=='COUNT' else {c['numerator_stat_key'],c['denominator_stat_key']}

def fail(code, message, path=''): raise EngineError(code,message,path)
def unique(rows, key, path):
    ids=[r[key] for r in rows]
    if len(ids)!=len(set(ids)): fail('DUPLICATE_ID',f'Duplicate {key}',path)
    return {r[key]:r for r in rows}
def finite_walk(x, path=''):
    if isinstance(x,float) and not math.isfinite(x): fail('NONFINITE_NUMBER','NaN and infinity are not accepted.',path)
    if isinstance(x,dict):
        for k,v in x.items(): finite_walk(v,path+'/'+k)
    elif isinstance(x,list):
        for i,v in enumerate(x): finite_walk(v,path+'/'+str(i))
def timestamp(s): return datetime.fromisoformat(s.replace('Z','+00:00'))

def validate(data: dict[str,Any]) -> dict[str,Any]:
    """Return a private immutable-by-convention copy after all supported semantic checks."""
    finite_walk(data)
    errors=sorted(VALIDATOR.iter_errors(data),key=lambda e:('/'.join(map(str,e.path)),e.message))
    if errors:
        e=errors[0];fail('SCHEMA_VALIDATION',e.message,'/'+ '/'.join(map(str,e.path)))
    d=copy.deepcopy(data);L=d['league'];cfg=d['methodology_config'];E=d['exposure_model'];R=d['reference_model']
    supported=[(L['sport_code']=='HOCKEY','UNSUPPORTED_SPORT'),(L['ownership_mode']=='EXCLUSIVE','UNSUPPORTED_OWNERSHIP_MODE'),(L['scoring_objective']['type']=='H2H_EACH_CATEGORY','UNSUPPORTED_SCORING_OBJECTIVE'),(L['draft_config']['draft_type'] in ('SNAKE','LINEAR'),'UNSUPPORTED_DRAFT_TYPE'),(L['lineup_rules']['information_policy']=='STATIC_PRE_DRAFT','UNSUPPORTED_INFORMATION_POLICY'),(d['manager_profile']['strategy_settings']['opponent_strategy_mode']=='POOL_ONLY','UNSUPPORTED_OPPONENT_MODE'),(not d['manager_profile']['strategy_settings']['timing_enabled'],'TIMING_NOT_IMPLEMENTED')]
    for ok,code in supported:
        if not ok: raise EngineError(code,'This setting is outside EXACT_SMALL_ROSTER_V1.',status='UNSUPPORTED_CAPABILITY')
    players=unique(d['players'],'player_id','players');lp=unique(d['league_players'],'player_id','league_players');proj=unique(d['projection_set']['player_projections'],'player_id','projection_set');pos=unique(L['positions'],'position_id','positions');cats=unique(L['categories'],'category_id','categories');slots=unique(L['roster_slots'],'slot_definition_id','roster_slots');teams=unique(d['teams'],'team_id','teams');rosters=unique(d['roster_state']['teams'],'team_id','roster_state');periods=unique(E['periods'],'period_id','periods');statdefs=unique(L['stat_definitions'],'stat_key','stat_definitions')
    unique(E['forecast_scenarios'],'scenario_id','forecast_scenarios');unique(R['field'],'reference_id','reference.field');unique(L['league_constraints'],'constraint_id','league_constraints');unique(d['manager_profile']['position_policies'],'policy_id','position_policies');unique(d['manager_profile']['category_preferences'],'category_id','category_preferences');unique(d['draft_state']['remaining_picks'],'pick_id','remaining_picks');unique(L['lineup_rules']['active_limits'],'limit_id','active_limits');unique(L['lineup_rules']['minimum_participation'],'requirement_id','minimum_participation')
    if not set(players)==set(lp)==set(proj): fail('PLAYER_UNIVERSE_MISMATCH','Canonical, league and projection player IDs must describe the same supplied universe.')
    if set(rosters)!=set(teams) or len(teams)!=L['number_of_teams'] or d['user_team_id'] not in teams: fail('TEAM_UNIVERSE_MISMATCH','Team count, user ID, and roster team records must agree.')
    horizon=L['scoring_objective']['horizon_id']
    split_horizon=d['schema_version']=='decision-engine-input/0.3.1'
    if split_horizon:
        if horizon!=E['horizon_id']:fail('HORIZON_MISMATCH','Scoring and exposure need the same lineup-utility horizon.')
        if d['capability_profile'] not in ('HORIZON_SPLIT_REPRESENTATIVE_SEASON_V1','HORIZON_SPLIT_EXACT_CALENDAR_V1'):fail('HORIZON_PROFILE_REQUIRED','Split-horizon inputs require a declared supported split-horizon capability profile.')
        groups=unique(E.get('evaluation_groups',[]),'evaluation_group_id','evaluation_groups')
        if not groups:fail('EVALUATION_GROUPS_REQUIRED','Split-horizon inputs require weighted scoring evaluation groups.')
        recon_mode=E.get('projection_reconciliation_mode')
        if recon_mode not in ('WEIGHTED_EVALUATION_GROUPS_TO_PROJECTION_TOTAL','EXPOSURE_SUBSET_OF_PROJECTION_HORIZON'):fail('PROJECTION_RECONCILIATION_REQUIRED','Split-horizon inputs must declare a supported projection/exposure reconciliation mode.')
        if any('evaluation_group_id' not in x for x in periods.values()):fail('EVALUATION_GROUP_REQUIRED','Every exposure period needs an evaluation group in split-horizon mode.')
        if any(x['evaluation_group_id'] not in groups for x in periods.values()):fail('UNKNOWN_EVALUATION_GROUP','Exposure period references an unknown evaluation group.')
        represented=math.fsum(x['weight'] for x in groups.values())
        if not math.isclose(represented,L['scoring_objective']['evaluation_period_count'],rel_tol=0,abs_tol=1e-12):fail('EVALUATION_PERIOD_COUNT_MISMATCH','Scoring evaluation-period count must equal the sum of evaluation-group weights.')
    else:
        groups={}
        if not (horizon==E['horizon_id']==d['projection_set']['horizon_id']):fail('HORIZON_MISMATCH','Scoring, exposure and projections need the same horizon.')
    if d['projection_set']['season']!=L['season'] or d['projection_set']['sport_code']!=L['sport_code']:fail('PROJECTION_CONTEXT_MISMATCH','Projection context does not match league.')
    if any(p['sport_code']!=L['sport_code'] for p in players.values()):fail('PLAYER_SPORT_MISMATCH','Player sport differs from league.')
    if any(t['league_id']!=L['league_id'] for t in teams.values()):fail('UNKNOWN_LEAGUE_REFERENCE','Team belongs to a different league.')
    for p in lp.values():
        elig=p['eligibility'];ids=set(elig['position_ids'])
        if p['league_id']!=L['league_id'] or not ids<=set(pos):fail('INVALID_ELIGIBILITY','Eligibility contains unknown league or position.')
        if any(not pos[x]['rosterable'] for x in ids):fail('INVALID_ELIGIBILITY','Eligibility references a non-rosterable position.')
        if 'primary_position_id' in elig and elig['primary_position_id'] not in ids:fail('INVALID_ELIGIBILITY','Primary position must belong to the eligibility set.')
    for s in slots.values():
        if s['eligibility']['mode']=='POSITION_SET' and not set(s['eligibility']['position_ids'])<=set(pos):fail('UNKNOWN_POSITION','Slot references unknown positions.')
    allstat=set(statdefs)
    unique(L['stat_relationships'],'relationship_id','stat_relationships')
    for relation in L['stat_relationships']:
        if not {relation['left_stat_key'],relation['right_stat_key']}<=allstat:fail('UNKNOWN_STAT_RELATIONSHIP','A statistic relationship references an undefined key.')
    for c in cats.values():
        if not category_keys(c)<=allstat:fail('UNKNOWN_STAT','Scoring formula references undefined statistics.')
        if not set(c['applicable_position_group_ids'])<={p['position_group_id'] for p in pos.values()}:fail('UNKNOWN_POSITION_GROUP','Category references undefined position groups.')
    for rule in L['league_constraints']+d['manager_profile']['position_policies']:
        if not set(rule['position_ids'])<=set(pos):fail('UNKNOWN_POSITION','Constraint references an undefined position.')
        if rule.get('type')=='POSITION_COUNT' and not rule['position_ids']:fail('EMPTY_POSITION_CONSTRAINT','A position constraint needs positions.')
        if rule.get('type')=='TOTAL_ROSTER_COUNT' and rule['position_ids']:fail('INVALID_TOTAL_RULE','Total-count rules cannot specify positions.')
        if rule['counting_basis']=='PRIMARY_POSITION' and rule.get('type')!='TOTAL_ROSTER_COUNT' and any('primary_position_id' not in p['eligibility'] for p in lp.values() if p['rosterable']):fail('PRIMARY_POSITION_REQUIRED','Do not infer a primary position.')
        if 'policy_id' in rule:
            if not any(x in rule for x in ('minimum','maximum','target')):fail('EMPTY_POLICY','A policy needs a bound or target.')
            for key in ('minimum','maximum'):
                if key in rule and rule[key]['enforcement']=='SOFT' and 'weight' not in rule[key]:fail('SOFT_WEIGHT_REQUIRED','Soft bounds require an explicit weight.')
            if all(x in rule for x in ('minimum','maximum')) and all(rule[x]['enforcement']=='HARD' for x in ('minimum','maximum')) and rule['minimum']['value']>rule['maximum']['value']:fail('MANAGER_POLICY_CONFLICT','Hard minimum exceeds hard maximum.')
    # Direct contradictions can be diagnosed without searching a draft path.
    for policy in d['manager_profile']['position_policies']:
        for rule in L['league_constraints']:
            if rule['type']!='POSITION_COUNT' or set(policy['position_ids'])!=set(rule['position_ids']) or policy['counting_basis']!=rule['counting_basis'] or set(policy['scope_slot_classes'])!=set(rule['scope_slot_classes']):continue
            minimum=policy.get('minimum');maximum=policy.get('maximum')
            if minimum and minimum['enforcement']=='HARD' and rule['operator'] in ('LTE','EQ') and minimum['value']>rule['limit']:fail('MANAGER_POLICY_CONFLICT','Manager hard minimum exceeds the league maximum.')
            if maximum and maximum['enforcement']=='HARD' and rule['operator'] in ('GTE','EQ') and maximum['value']<rule['limit']:fail('MANAGER_POLICY_CONFLICT','Manager hard maximum is below the league minimum.')
    owned=[]
    slot_instances={f'{s["slot_definition_id"]}#{i+1}':s for s in slots.values() for i in range(s['capacity'])}
    for r in rosters.values():
        for entry in r['entries']:
            p=entry['player_id']
            if p not in lp:fail('UNKNOWN_REFERENCE','Roster contains unknown player.')
            if not lp[p]['rosterable']:fail('NONROSTERABLE_OWNED_PLAYER','An owned player is not rosterable.')
            owned.append(p)
            if r['team_id']!=d['user_team_id'] and 'construction_lock' in entry:raise EngineError('OPPONENT_LOCK_NOT_SUPPORTED','Anonymous POOL_ONLY demand cannot preserve named opponent construction locks in this profile.',status='UNSUPPORTED_CAPABILITY')
            if 'reported_slot_definition_id' in entry and entry['reported_slot_definition_id'] not in slots:fail('UNKNOWN_SLOT','Reported slot is undefined.')
            if 'construction_lock' in entry:
                lock=entry['construction_lock'];sid=lock['slot_instance_id'];position=lock['assigned_position_id']
                if sid not in slot_instances or position not in lp[p]['eligibility']['position_ids']:fail('INVALID_ASSIGNMENT_LOCK','Locked position or exact slot is invalid.')
                s=slot_instances[sid]
                if s['eligibility']['mode']=='POSITION_SET' and position not in s['eligibility']['position_ids']:fail('INVALID_ASSIGNMENT_LOCK','Position is not allowed in locked slot.')
    if len(owned)!=len(set(owned)):fail('DUPLICATE_PLAYER_OWNERSHIP','Exclusive-ownership player appears more than once.')
    exc=set(d['draft_state']['manually_excluded_player_ids'])
    if not exc<=set(players):fail('UNKNOWN_REFERENCE','Excluded player ID is unknown.')
    if exc&set(owned):fail('CONTRADICTORY_AVAILABILITY','A player cannot be both owned and manually excluded.')
    if not set(R['normalization_player_ids'])<=set(players):fail('UNKNOWN_NORMALIZATION_PLAYER','Anchor cohort contains unknown player.')
    for c in cats.values():
        if not any(applies(c,p,lp,pos) for p in R['normalization_player_ids']):fail('EMPTY_CATEGORY_COHORT','Applicable category has no anchor population.')
    preferences=d['manager_profile']['category_preferences']
    for x in preferences:
        if x['category_id'] not in cats:fail('UNKNOWN_CATEGORY','Preference references an unknown category.')
        if x['mode']=='PRIORITIZE' and ('multiplier' not in x or x['multiplier']<=0):fail('STRATEGY_MULTIPLIER_REQUIRED','PRIORITIZE requires a positive multiplier.')
        if x['mode'] in ('STANDARD','PUNT') and 'multiplier' in x:fail('AMBIGUOUS_CATEGORY_STRATEGY','Only PRIORITIZE accepts a multiplier.')
    pref={x['category_id']:x for x in preferences}
    if all(pref.get(c,{}).get('mode')=='PUNT' for c in cats):fail('ALL_CATEGORIES_PUNTED','The strategy objective has no positive category weight.')
    if E['exposure_stat_key'] not in allstat:fail('UNKNOWN_EXPOSURE_STAT','Exposure stat must have a definition.')
    for item in L['lineup_rules']['active_limits']+L['lineup_rules']['minimum_participation']:
        if item['stat_key'] not in allstat:fail('UNKNOWN_PARTICIPATION_STAT','Participation rule references an undefined stat.')
        if 'failed_category_ids' in item and not set(item['failed_category_ids'])<=set(cats):fail('UNKNOWN_CATEGORY','Participation consequence references unknown category.')
    if L['lineup_rules']['lock_mode']=='DAILY' and len({p['lock_group_id'] for p in periods.values()})!=len(periods):fail('INVALID_DAILY_LOCK_GROUPS','Daily model requires a distinct lock group for each period.')
    if len({p['starts_at'] for p in periods.values()})!=len(periods):fail('DUPLICATE_PERIOD_TIME','Period starts must be unique in this profile.')
    cutoff=timestamp(d['created_at'])
    stamps=[d['projection_set']['created_at'],E['as_of'],R['as_of'],d['roster_state']['as_of'],d['source_metadata']['imported_at']]+[p['eligibility']['as_of'] for p in lp.values()]
    if any(timestamp(s)>cutoff for s in stamps):fail('FUTURE_INPUT_INFORMATION','Input information timestamp is later than the decision cutoff.')
    if any(timestamp(p['starts_at'])<=cutoff for p in periods.values()):fail('PAST_EXPOSURE_PERIOD','This pre-draft research profile requires future exposure periods.')
    def stat_values(values,path):
        if not set(values)<=allstat:fail('UNKNOWN_STAT','Undefined statistic in supplied values.',path)
        for relation in L['stat_relationships']:
            left,right=relation['left_stat_key'],relation['right_stat_key']
            if left in values and right in values:
                a,b=values[left],values[right];op=relation['operator']
                if (op=='LTE' and a>b) or (op=='GTE' and a<b) or (op=='EQ' and a!=b):fail('STAT_IDENTITY_VIOLATION',f'{left} {op} {right} does not hold.',path)
        for key,value in values.items():
            bounds=statdefs[key]
            if 'minimum' in bounds and value<bounds['minimum'] or 'maximum' in bounds and value>bounds['maximum']:fail('STAT_DOMAIN_VIOLATION',f'{key} violates declared domain.',path)
    participation_keys={x['stat_key'] for x in L['lineup_rules']['active_limits']+L['lineup_rules']['minimum_participation']}
    required={p:{E['exposure_stat_key']}|participation_keys|set().union(*(category_keys(c) for c in cats.values() if applies(c,p,lp,pos))) for p in players}
    for p in players:
        stat_values(proj[p]['stat_values'],f'projections/{p}')
        if not required[p]<=set(proj[p]['stat_values']):fail('MISSING_CATEGORY_PROJECTION',f'Missing applicable projection for {p}.')
    probs=math.fsum(s['probability'] for s in E['forecast_scenarios'])
    if not math.isclose(probs,1,rel_tol=0,abs_tol=1e-12):fail('SCENARIO_PROBABILITY_SUM','Forecast probabilities must sum to one.')
    for s in E['forecast_scenarios']:
        if set(s['player_period_stats'])!=set(players):fail('EXPOSURE_PLAYER_MISMATCH','Each joint forecast scenario must cover the full player universe.')
        for p,periodstats in s['player_period_stats'].items():
            if set(periodstats)!=set(periods):fail('EXPOSURE_PERIOD_MISMATCH','Use explicit zero projections for off periods.')
            for t,stats in periodstats.items():
                stat_values(stats,f'scenarios/{s["scenario_id"]}/{p}/{t}')
                if stats.get(E['exposure_stat_key'])==0 and any(v!=0 for k,v in stats.items() if k!=E['exposure_stat_key']):fail('PRODUCTION_WITHOUT_EXPOSURE','An off-period cannot contain production.')
                if not required[p]<=set(stats):fail('MISSING_PERIOD_PROJECTION',f'Missing applicable period projection for {p}/{t}.')
                for c in cats.values():
                    if applies(c,p,lp,pos) and c['calculation']['type']=='RATIO':
                        n=stats[c['calculation']['numerator_stat_key']];den=stats[c['calculation']['denominator_stat_key']]
                        if den<0 or (den==0 and n!=0):fail('INVALID_RATIO_EXPOSURE','Negative denominator or nonzero numerator at zero denominator.')
    for p in players:
        for key,value in proj[p]['stat_values'].items():
            if any(key not in s['player_period_stats'][p][t] for s in E['forecast_scenarios'] for t in periods):fail('MISSING_PERIOD_PROJECTION',f'Projection key {key} missing in period data for {p}.')
            if split_horizon:
                group_weight={gid:g['weight'] for gid,g in groups.items()}
                expected=math.fsum(s['probability']*math.fsum(group_weight[periods[t]['evaluation_group_id']]*s['player_period_stats'][p][t][key] for t in sorted(periods)) for s in sorted(E['forecast_scenarios'],key=lambda x:x['scenario_id']))
            else:
                expected=math.fsum(s['probability']*math.fsum(s['player_period_stats'][p][t][key] for t in sorted(periods)) for s in sorted(E['forecast_scenarios'],key=lambda x:x['scenario_id']))
            if split_horizon and recon_mode=='EXPOSURE_SUBSET_OF_PROJECTION_HORIZON':
                if expected < -1e-9 or expected > value + max(1e-9,abs(value)*1e-9):
                    fail('EXPOSURE_RECONCILIATION',f'{p}/{key}: exposure subset {expected} must be between zero and full projection {value}.')
            elif not math.isclose(value,expected,rel_tol=1e-9,abs_tol=1e-9):
                fail('EXPOSURE_RECONCILIATION',f'{p}/{key}: total {value} != weighted period sum {expected}.')
    refrequired=set().union(*(category_keys(c) for c in cats.values()))|{x['stat_key'] for x in L['lineup_rules']['minimum_participation']}
    for f in R['field']:
        stat_values(f['stat_values'],'reference/field')
        if not refrequired<=set(f['stat_values']):fail('MISSING_REFERENCE_STAT','Reference outcome lacks scoring or participation inputs.')
        if split_horizon:
            grouped=f.get('evaluation_group_stat_values')
            if grouped is None or set(grouped)!=set(groups):fail('REFERENCE_EVALUATION_GROUP_MISMATCH','Each reference row must cover every scoring evaluation group.')
            for gid,vals in grouped.items():
                stat_values(vals,f'reference/field/{gid}')
                if not refrequired<=set(vals):fail('MISSING_REFERENCE_STAT','Reference evaluation group lacks scoring or participation inputs.')
                for c in cats.values():
                    if c['calculation']['type']=='RATIO':
                        n=vals[c['calculation']['numerator_stat_key']];den=vals[c['calculation']['denominator_stat_key']]
                        if den<0 or (den==0 and n!=0):fail('INVALID_REFERENCE_RATIO','Invalid ratio exposure in reference evaluation group.')
        for c in cats.values():
            if c['calculation']['type']=='RATIO':
                n=f['stat_values'][c['calculation']['numerator_stat_key']];den=f['stat_values'][c['calculation']['denominator_stat_key']]
                if den<0 or (den==0 and n!=0):fail('INVALID_REFERENCE_RATIO','Invalid ratio exposure in reference field.')
    if 'replacement_thresholds' in R:
        if not set(R['replacement_thresholds'])<=set(pos) or 'replacement_threshold_provenance' not in R:fail('INVALID_REPLACEMENT_THRESHOLDS','Declared thresholds require known positions and provenance.')
    if 'external_ranks' in d:
        if not set(d['external_ranks'])<=set(players) or 'external_rank_provenance' not in d:fail('EXTERNAL_RANK_PROVENANCE_REQUIRED','Static ranks need known IDs and archive provenance.')
    demands=unique(d['draft_state']['anonymous_demands'],'agent_id','anonymous_demands')
    if 'USER' in demands or len(demands)!=L['number_of_teams']-1:fail('INVALID_ANONYMOUS_DEMAND','Declare one anonymous agent per non-user team, excluding reserved USER.')
    opponent_count=sum(len(r['entries']) for t,r in rosters.items() if t!=d['user_team_id'])
    if sum(a['initial_roster_count'] for a in demands.values())!=opponent_count:fail('ANONYMOUS_POOL_COUNT_MISMATCH','Anonymous counts must equal pooled non-user ownership.')
    picks=d['draft_state']['remaining_picks']
    if any(p['actor_id'] not in {'USER',*demands} for p in picks):fail('UNKNOWN_DRAFT_ACTOR','Pick actor is not USER or a declared anonymous agent.')
    if picks and picks[0]['actor_id']!='USER':raise EngineError('USER_NOT_ON_CLOCK','Recommendation currently requires a user selection first in the remaining ledger.',status='UNSUPPORTED_CAPABILITY')
    if (d['draft_state']['status']=='COMPLETE') != (not picks):fail('DRAFT_STATUS_CONFLICT','COMPLETE must have no remaining picks; live states need a remaining ledger.')
    return d

def applies(category,pid,lp,pos):
    groups={pos[x]['position_group_id'] for x in lp[pid]['eligibility']['position_ids']}
    return bool(groups&set(category['applicable_position_group_ids']))

def canonical_hash(data: Any) -> str:
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
