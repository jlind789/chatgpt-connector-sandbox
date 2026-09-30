"""Explicit fictional league factories; never substitute these for a real league import."""
from __future__ import annotations
from datetime import date, timedelta
import json
from pathlib import Path
STAMP='2026-09-18T14:00:00Z'
CLASSES=['ACTIVE','BENCH','INJURED_RESERVE','RESERVE']

def count_category(cid,key,group='SKATER',direction='HIGHER_IS_BETTER'):
    return {'category_id':cid,'display_name':cid,'applicable_position_group_ids':[group],'direction':direction,'weight':1,'precision':6,'calculation':{'type':'COUNT','stat_key':key}}

def ratio_category(cid,numerator,denominator,group='GOALIE'):
    return {'category_id':cid,'display_name':cid,'applicable_position_group_ids':[group],'direction':'HIGHER_IS_BETTER','weight':1,'precision':6,'calculation':{'type':'RATIO','numerator_stat_key':numerator,'denominator_stat_key':denominator,'multiplier':1}}

def position_policy(pid,position,maximum=None,minimum=None,target=None,hard=True,basis='ASSIGNED_POSITION'):
    r={'policy_id':pid,'position_ids':[position],'counting_basis':basis,'scope_slot_classes':CLASSES}
    for name,value in [('maximum',maximum),('minimum',minimum)]:
        if value is not None:r[name]={'value':value,'enforcement':'HARD' if hard else 'SOFT','evaluation_phase':'DRAFT_COMPLETION' if name=='minimum' else 'AT_ALL_TIMES',**({} if hard else {'weight':1})}
    if target is not None:r['target']={'value':target,'weight':1}
    return r

def build_fixture(name,slot_specs,players,user_ids,actors,reference_values,categories=None,opponent_groups=None,policies=None,period_count=1,rollouts=8,required_active=True):
    """players: {id: (eligibility-list, {period-index: stat-dict})}. Unspecified off days are explicit zeroes."""
    categories=categories or [count_category('G','goals')];opponent_groups=opponent_groups or []
    positions=sorted({p for elig,_ in players.values() for p in elig}|{p for sid,capacity,eligible,cls in slot_specs for p in eligible})
    keys={'games'}
    for c in categories:
        x=c['calculation'];keys.update([x['stat_key']] if x['type']=='COUNT' else [x['numerator_stat_key'],x['denominator_stat_key']])
    for _,schedule in players.values():
        for stats in schedule.values():keys.update(stats)
    for stats in reference_values:keys.update(stats)
    keys=sorted(keys)
    periods=[{'period_id':f'd{i+1}','lock_group_id':f'lock{i+1}','starts_at':(date(2026,10,1)+timedelta(days=i)).isoformat()+'T12:00:00Z'} for i in range(period_count)]
    scenarios={};projections=[]
    for pid,(elig,schedule) in sorted(players.items()):
        scenarios[pid]={}
        for i in range(period_count):
            values={k:0.0 for k in keys};values.update(schedule.get(i,{}));scenarios[pid][f'd{i+1}']=values
        projections.append({'player_id':pid,'stat_values':{k:sum(scenarios[pid][f'd{i+1}'][k] for i in range(period_count)) for k in keys}})
    slots=[{'slot_definition_id':sid,'display_name':sid,'capacity':capacity,'slot_class':cls,'eligibility':{'mode':'POSITION_SET','position_ids':eligible} if eligible else {'mode':'ANY_ROSTERABLE'},'required_status_tags_any_of':[],'counts_toward_roster_total':cls not in ('INJURED_RESERVE','RESERVE'),'required_at_completion':required_active and cls=='ACTIVE'} for sid,capacity,eligible,cls in slot_specs]
    size=sum(s['capacity'] for s in slots if s['counts_toward_roster_total'])
    teams=[{'team_id':'user','league_id':name,'display_name':'User'}]+[{'team_id':f'opponent{i+1}','league_id':name,'display_name':f'Opponent {i+1}'} for i in range(len(opponent_groups))]
    rosters=[{'team_id':'user','entries':[{'player_id':p,'acquisition_type':'KEEPER'} for p in user_ids]}]+[{'team_id':f'opponent{i+1}','entries':[{'player_id':p,'acquisition_type':'KEEPER'} for p in ids]} for i,ids in enumerate(opponent_groups)]
    return {'schema_version':'decision-engine-input/0.3','capability_profile':'EXACT_SMALL_ROSTER_V1','snapshot_id':name,'created_at':STAMP,'user_team_id':'user','league':{'league_id':name,'sport_code':'HOCKEY','season':'2026-27','platform':'MANUAL','ownership_mode':'EXCLUSIVE','number_of_teams':len(teams),'positions':[{'position_id':p,'display_name':p,'position_group_id':'GOALIE' if p=='G' else 'SKATER','rosterable':True} for p in positions],'roster_slots':slots,'categories':categories,'league_constraints':[{'constraint_id':'terminal_roster_total','type':'TOTAL_ROSTER_COUNT','position_ids':[],'counting_basis':'ASSIGNED_POSITION','scope_slot_classes':CLASSES,'operator':'EQ','limit':size,'evaluation_phase':'ROSTER_COMPLETION'}],'stat_definitions':[{'stat_key':k,'minimum':0} for k in keys],'stat_relationships':[{'relationship_id':'save_identity','left_stat_key':'saves','operator':'LTE','right_stat_key':'shots_against'}] if {'saves','shots_against'}<=set(keys) else [],'scoring_objective':{'type':'H2H_EACH_CATEGORY','category_tie_credit':0.5,'both_undefined_tie_credit':0.5,'rounding':'HALF_EVEN','horizon_id':'synthetic_matchup_1','evaluation_period_count':1},'lineup_rules':{'lock_mode':'DAILY','information_policy':'STATIC_PRE_DRAFT','active_limits':[],'minimum_participation':[]},'draft_config':{'draft_type':'LINEAR'}},'manager_profile':{'manager_profile_id':'synthetic_manager','position_policies':policies or [],'category_preferences':[],'strategy_settings':{'opponent_strategy_mode':'POOL_ONLY','timing_enabled':False}},'players':[{'player_id':p,'sport_code':'HOCKEY','display_name':p.replace('_',' ').title()} for p in sorted(players)],'league_players':[{'player_id':p,'league_id':name,'rosterable':True,'eligibility':{'position_ids':sorted(set(elig)),'source':'MANUAL','as_of':STAMP},'status_tags':[]} for p,(elig,_) in sorted(players.items())],'projection_set':{'projection_set_id':name+'_projections','sport_code':'HOCKEY','season':'2026-27','horizon_id':'synthetic_matchup_1','source':'FICTIONAL_TEST_DATA','created_at':STAMP,'player_projections':projections},'teams':teams,'roster_state':{'as_of':STAMP,'teams':rosters},'draft_state':{'status':'IN_PROGRESS' if actors else 'COMPLETE','remaining_picks':[{'pick_id':f'pick{i+1}','actor_id':actor} for i,actor in enumerate(actors)],'anonymous_demands':[{'agent_id':f'anon{i+1}','initial_roster_count':len(ids)} for i,ids in enumerate(opponent_groups)],'manually_excluded_player_ids':[]},'exposure_model':{'exposure_model_id':name+'_exposure','calendar_id':name+'_synthetic_calendar','horizon_id':'synthetic_matchup_1','as_of':STAMP,'data_kind':'SYNTHETIC','exposure_stat_key':'games','periods':periods,'forecast_scenarios':[{'scenario_id':'forecast1','probability':1,'player_period_stats':scenarios}]},'reference_model':{'reference_model_id':name+'_reference','as_of':STAMP,'normalization_player_ids':sorted(players),'field_id':name+'_fictional_field','generation_policy':'EXPLICIT_SYNTHETIC_FIXTURE','field':[{'reference_id':f'ref{i+1}','weight':1,'stat_values':{**{k:0 for k in keys},**v}} for i,v in enumerate(reference_values)]},'methodology_config':{'methodology_id':'M0.3-ROSTER-RESEARCH-1','seed':41,'rollouts':rollouts,'soft_policy_lambda':0.005,'neutral_temperature':len(teams),'max_search_nodes':500000,'max_lineup_plans':100000,'continuation_policy':'B3'},'source_metadata':{'source_platform':'MANUAL','adapter_name':'fictional_fixture_builder','adapter_version':'0.3.1','imported_at':STAMP}}

def calendar_bench():
    players={'a_owned':(['C'],{0:{'games':1,'goals':10},1:{'games':1,'goals':10}}),'b_congested':(['C'],{0:{'games':1,'goals':6},1:{'games':1,'goals':6}}),'c_complementary':(['C'],{2:{'games':1,'goals':5},3:{'games':1,'goals':5}})}
    return build_fixture('calendar_bench',[('C',1,['C'],'ACTIVE'),('BN',1,[],'BENCH')],players,['a_owned'],['USER'],[{'goals':25},{'goals':28},{'goals':32}],period_count=4)

def scarcity():
    players={p:([pos],{0:{'games':1,'goals':v}}) for p,pos,v in [('a_center_star','C',10),('b_wing_star','RW',9),('c_center_alternative','C',9),('d_wing_alternative','RW',2)]}
    d=build_fixture('scarcity',[('C',1,['C'],'ACTIVE'),('RW',1,['RW'],'ACTIVE')],players,[],['USER','anon1','USER','anon1'],[{'goals':16}],opponent_groups=[[]],rollouts=16)
    d['methodology_config']['neutral_temperature']=0.001
    return d

def four_goalies():
    players={f'goalie_{i}':(['G'],{0:{'games':1,'saves':50 if i<4 else 100}}) for i in range(1,5)}
    return build_fixture('four_goalies',[('G',4,['G'],'ACTIVE')],players,['goalie_1','goalie_2','goalie_3'],['USER'],[{'saves':175}],categories=[count_category('SVS','saves','GOALIE')])

def flexible_roster():
    categories=[count_category('G','goals'),count_category('A','assists'),count_category('SOG','shots'),count_category('SVS','saves','GOALIE'),ratio_category('SVPCT','saves','shots_against')]
    records=[('flex_a',['C','LW'],4,5,20,0,0),('flex_b',['LW','RW'],5,4,22,0,0),('goalie_owned',['G'],0,0,0,90,100),('center_candidate',['C'],6,6,25,0,0),('wing_candidate',['RW'],5,5,28,0,0),('goalie_candidate',['G'],0,0,0,95,100),('opponent_keeper',['C'],6,4,24,0,0),('depth_lw',['LW'],1,2,10,0,0),('depth_rw',['RW'],2,1,9,0,0),('depth_c',['C'],2,2,8,0,0),('depth_g',['G'],0,0,0,85,100),('depth_lw_spare',['LW'],1,1,6,0,0)]
    players={p:(pos,{0:{'games':1,'goals':g,'assists':a,'shots':s,'saves':sv,'shots_against':sa}}) for p,pos,g,a,s,sv,sa in records}
    d=build_fixture('flexible_roster',[('C',1,['C'],'ACTIVE'),('LW',1,['LW'],'ACTIVE'),('RW',1,['RW'],'ACTIVE'),('G',1,['G'],'ACTIVE'),('BN',1,[],'BENCH')],players,['flex_a','flex_b','goalie_owned'],['USER','anon1','USER','anon1','anon1','anon1'],[{'goals':14,'assists':14,'shots':65,'saves':89,'shots_against':100},{'goals':16,'assists':16,'shots':75,'saves':93,'shots_against':100}],categories=categories,opponent_groups=[['opponent_keeper']],policies=[position_policy('manager_goalie_cap','G',maximum=1)],rollouts=16)
    d['league']['league_constraints'].append({'constraint_id':'league_goalie_cap','type':'POSITION_COUNT','position_ids':['G'],'counting_basis':'ASSIGNED_POSITION','scope_slot_classes':CLASSES,'operator':'LTE','limit':2,'evaluation_phase':'AT_ALL_TIMES'})
    return d

def ratio_volume():
    players={'volume_goalie':(['G'],{0:{'games':10,'saves':900,'shots_against':1000}}),'small_goalie':(['G'],{0:{'games':2,'saves':190,'shots_against':200}})}
    return build_fixture('ratio_volume',[('G',2,['G'],'ACTIVE')],players,['volume_goalie','small_goalie'],[],[{'saves':900,'shots_against':1000}],categories=[count_category('SVS','saves','GOALIE'),ratio_category('SVPCT','saves','shots_against')])

FACTORIES={'calendar_bench':calendar_bench,'scarcity':scarcity,'four_goalies':four_goalies,'flexible_roster':flexible_roster,'ratio_volume':ratio_volume}

def write_fixtures(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    for name,factory in FACTORIES.items():(directory/(name+'.json')).write_text(json.dumps(factory(),indent=2)+'\n')

if __name__=='__main__':write_fixtures(Path(__file__).resolve().parents[1]/'fixtures')
