"""Fictional rule demonstrations; no inferred HOH ownership or IR eligibility."""
from __future__ import annotations
import copy
from decision_engine.fixtures import build_fixture,position_policy,CLASSES
from decision_engine.validation import canonical_hash,validate
from research.scaling_worlds import full_roster_world


def league_cap(cid,position,limit,basis='ASSIGNED_POSITION',scope=None,operator='LTE',phase='AT_ALL_TIMES'):
    return {'constraint_id':cid,'type':'POSITION_COUNT','position_ids':[position],'counting_basis':basis,
            'scope_slot_classes':scope or CLASSES,'operator':operator,'limit':limit,'evaluation_phase':phase}


def reserve_fixture():
    d=build_fixture('fictional_static_ir',[('C',1,['C'],'ACTIVE'),('BN',1,[],'BENCH'),('IR',1,[],'INJURED_RESERVE')],
        {'a':(['C'],{0:{'games':1,'goals':8}}),'b_ir':(['C'],{0:{'games':1,'goals':100}}),
         'c':(['C'],{0:{'games':1,'goals':9}}),'d':(['C'],{0:{'games':1,'goals':3}})},
        ['a','b_ir'],['USER'],[{'goals':8.5}],rollouts=1)
    d['league']['roster_slots'][-1]['required_status_tags_any_of']=['INJURED']
    next(p for p in d['league_players'] if p['player_id']=='b_ir')['status_tags']=['INJURED']
    return validate(d)


def full_rules_fixture(initial_count=18,with_reserves=False,rollouts=1):
    d=full_roster_world(seed=36001,initial_count=initial_count,rollouts=rollouts)
    d['snapshot_id']='FICTIONAL_RULES_'+str(initial_count)+('_IR' if with_reserves else '')
    d['manager_profile']['position_policies']=[position_policy('personal_G_max3','G',maximum=3),
        position_policy('personal_LW_max2','LW',maximum=2),position_policy('personal_RW_max2','RW',maximum=2),
        position_policy('personal_C_target5','C',target=5),position_policy('personal_D_target7','D',target=7)]
    for p in d['manager_profile']['position_policies']:p['scope_slot_classes']=['ACTIVE','BENCH']
    d['league']['league_constraints'].append(league_cap('fictional_league_G_max4','G',4,scope=['ACTIVE','BENCH']))
    if with_reserves:
        ir={'slot_definition_id':'IR','display_name':'IR','capacity':2,'slot_class':'INJURED_RESERVE',
            'eligibility':{'mode':'ANY_ROSTERABLE'},'required_status_tags_any_of':['INJURED'],
            'counts_toward_roster_total':False,'required_at_completion':False}
        d['league']['roster_slots'].append(ir)
        owned={e['player_id'] for t in d['roster_state']['teams'] for e in t['entries']}
        extra=sorted((p['player_id'] for p in d['league_players'] if p['player_id'] not in owned and p['eligibility']['position_ids']==['D']),reverse=True)[:2]
        for pid in extra:
            next(p for p in d['league_players'] if p['player_id']==pid)['status_tags']=['INJURED']
            d['roster_state']['teams'][0]['entries'].append({'player_id':pid,'acquisition_type':'KEEPER'})
        # Unchanged league total counts nineteen regular places; IR storage is excluded.
    return validate(d)


def lock_fixture():
    d=build_fixture('fictional_staggered_locks',[('C',1,['C'],'ACTIVE'),('LW',1,['LW'],'ACTIVE'),('BN',1,[],'BENCH')],
        {'a':(['C','LW'],{0:{'games':1,'goals':3}}),'b':(['C','LW'],{0:{'games':1,'goals':4}}),
         'c':(['C','LW'],{0:{'games':1,'goals':5}})},['a','b','c'],[],[{'goals':5}])
    before=[{'player_id':'a','slot_instance_id':'C#1','assigned_position_id':'C'},
            {'player_id':'b','slot_instance_id':'LW#1','assigned_position_id':'LW'},
            {'player_id':'c','slot_instance_id':'BN#1','assigned_position_id':'C'}]
    c={'schema_version':'decision-engine-lineup-transition/0.3.6','snapshot_sha256':canonical_hash(d),'period_id':'d1',
       'lock_basis':'PLAYER_SCHEDULED_GAME_TIME','decision_at':'2026-10-01T19:30:00Z',
       'period_starts_at':'2026-10-01T00:00:00Z','period_ends_at':'2026-10-02T00:00:00Z',
       'previous_committed_at':'2026-10-01T18:00:00Z','schedule_as_of':'2026-10-01T18:00:00Z',
       'lock_slot_classes':CLASSES,'previously_locked_player_ids':[],
       'scheduled_starts':{'a':['2026-10-01T19:00:00Z'],'b':['2026-10-01T21:00:00Z'],'c':['2026-10-01T22:00:00Z']},
       'before_plan':before,'after_plan':copy.deepcopy(before)}
    return d,c
