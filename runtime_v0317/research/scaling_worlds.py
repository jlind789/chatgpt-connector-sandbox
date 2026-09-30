"""HOH-shaped FICTIONAL workload, not a reconstruction of the user's draft."""
from __future__ import annotations
import random
from decision_engine.fixtures import build_fixture,count_category,ratio_category
from decision_engine.validation import validate


def full_roster_world(seed=35001,initial_count=18,teams=6,pool_size=250,periods=7,rollouts=1):
    if initial_count not in (7,14,18):raise ValueError('Supported workload checkpoints: 7,14,18.')
    if teams not in (2,6):raise ValueError('Declared workload supports 2 or 6 teams.')
    if pool_size<teams*19:raise ValueError('Insufficient player universe.')
    rng=random.Random(seed)
    roles=['C','LW','RW','D','G'];allocation=[.22,.18,.18,.26,.16]
    counts=[int(pool_size*x) for x in allocation];counts[0]+=pool_size-sum(counts)
    players={};byrole={r:[] for r in roles}
    cats=[count_category(cid,k) for cid,k in [('G','goals'),('A','assists'),('FOW','faceoff_wins'),('SOG','shots'),('HIT','hits'),('BLK','blocks'),('DEF','defense_points')]]
    cats+=[count_category(cid,k,'GOALIE') for cid,k in [('W','wins'),('SVS','saves'),('SO','shutouts')]]+[ratio_category('SVPCT','saves','shots_against')]
    for role,count in zip(roles,counts):
        for i in range(count):
            pid=f'{role.lower()}_{i:03d}';byrole[role].append(pid);q=rng.uniform(.55,1.7)
            elig=[role]
            if role in ('C','LW','RW') and i%5==0:elig=sorted({role,roles[(roles.index(role)+1)%3]})
            schedule={}
            days=sorted(rng.sample(range(periods),min(periods,2+i%3)))
            for t in days:
                if role=='G':
                    against=rng.uniform(24,35);saves=against*rng.uniform(.88,.94)
                    schedule[t]={'games':1.,'wins':rng.uniform(.3,.7),'saves':saves,'shots_against':against,'shutouts':rng.uniform(.015,.09)}
                else:
                    g=q*rng.uniform(.15,.45);a=q*rng.uniform(.25,.65)
                    schedule[t]={'games':1.,'goals':g,'assists':a,'faceoff_wins':q*rng.uniform(5,12) if role=='C' else rng.uniform(0,.5),'shots':q*rng.uniform(1.7,3.4),'hits':q*rng.uniform(.5,2.5),'blocks':q*rng.uniform(1,2.5) if role=='D' else q*rng.uniform(.2,1),'defense_points':g+a if role=='D' else 0.}
            players[pid]=(elig,schedule)
    # Explicit storage composition only for synthetic starting rosters; no manager policy.
    composition={7:{'C':2,'LW':1,'RW':1,'D':2,'G':1},14:{'C':3,'LW':2,'RW':2,'D':5,'G':2},18:{'C':4,'LW':2,'RW':2,'D':7,'G':3}}[initial_count]
    owners=[]
    for team in range(teams):
        roster=[]
        for role,n in composition.items():roster+=byrole[role][team*n:(team+1)*n]
        owners.append(roster)
    actors=[]
    for rnd in range(19-initial_count):
        order=list(range(teams)) if rnd%2==0 else list(reversed(range(teams)))
        actors+=['USER' if i==0 else f'anon{i}' for i in order]
    slots=[(p,n,[p],'ACTIVE') for p,n in [('C',2),('LW',2),('RW',2),('D',4),('G',2)]]+[('UTIL',2,['C','LW','RW','D'],'ACTIVE'),('BN',5,[],'BENCH')]
    refs=[]
    for i in range(24):
        m=.7+i*.025
        refs.append({'games':40*m,'goals':12*m,'assists':20*m,'faceoff_wins':85*m,'shots':105*m,'hits':50*m,'blocks':40*m,'defense_points':9*m,'wins':3*m,'saves':160*m,'shots_against':160*m/(.885+i*.002),'shutouts':.25*m})
    d=build_fixture(f'fictional_full_roster_{seed}_{initial_count}_{teams}',slots,players,owners[0],actors,refs,cats,owners[1:],period_count=periods,rollouts=rollouts)
    d['league']['draft_config']['draft_type']='SNAKE'
    # DEF belongs to the athlete's scoring role, NOT whether assigned to a D/UTIL slot.
    for p in d['league']['positions']:p['position_group_id']='GOALIE' if p['position_id']=='G' else 'DEFENDER' if p['position_id']=='D' else 'FORWARD'
    for c in d['league']['categories']:
        if c['category_id']=='DEF':c['applicable_position_group_ids']=['DEFENDER']
        elif c['applicable_position_group_ids']==['SKATER']:c['applicable_position_group_ids']=['FORWARD','DEFENDER']
    d['methodology_config']['seed']=seed
    d['methodology_config']['max_lineup_plans']=10000000
    d['source_metadata']['adapter_version']='0.3.5'
    return validate(d)
