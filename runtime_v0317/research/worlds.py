"""Public fictional leagues. Hidden outcome tapes are generated separately."""
from __future__ import annotations
import hashlib
import json
import random
from decision_engine.fixtures import build_fixture, count_category, ratio_category
from decision_engine.validation import validate

FAMILIES=('BALANCED','CALENDAR_CONGESTION','ROLE_REVERSAL','GOALIE_RATES')

def keyed_seed(*parts):
    return int.from_bytes(hashlib.sha256(json.dumps(parts,separators=(',',':')).encode()).digest()[:8],'big')


def public_world(family,seed,teams=2,rollouts=8,keepers=False):
    """Synthetic 2-period leagues, declared before observing any audit outcomes.

    No live player names, league import, inferred HOH rules, or market data.
    More role supply than mandatory demand prevents most pathological starvation;
    the draft harness still validates each actual selection and completion.
    """
    if family not in FAMILIES:raise ValueError('Unknown family '+family)
    if not 2<=teams<=4:raise ValueError('Research world supports 2--4 teams.')
    rng=random.Random(keyed_seed('PUBLIC-v1',family,seed,teams))
    goalie=family=='GOALIE_RATES'
    cats=[count_category('G','goals'),count_category('A','assists'),count_category('SOG','shots')]
    slots=[('C',1,['C'],'ACTIVE'),('RW',1,['RW'],'ACTIVE')]
    if goalie:
        cats += [count_category('SVS','saves','GOALIE'),ratio_category('SVPCT','saves','shots_against')]
        slots += [('G',1,['G'],'ACTIVE')]
    slots += [('BN',1,[],'BENCH')]
    players={}
    roles=['C','RW']+(['G'] if goalie else [])
    for role in roles:
        for i in range(teams*2):
            pid=f'{role.lower()}_{i:02d}';quality=rng.uniform(.55,1.6)
            elig=[role]
            if role!='G' and i==0:elig=['C','RW']
            days=[0,1] if rng.random()<.5 else [rng.randrange(2)]
            if family=='CALENDAR_CONGESTION':
                days=[0] if quality>=1 else [1]
            schedule={}
            for day in days:
                if role=='G':
                    against=rng.uniform(26,35);rate=rng.uniform(.88,.94)
                    vals={'games':1.,'saves':against*rate,'shots_against':against}
                else:
                    goals=.42*quality*rng.uniform(.7,1.3)
                    assists=.6*quality*rng.uniform(.5,1.5)
                    shots=3.2*quality*rng.uniform(.8,1.2)
                    vals={'games':1.,'goals':goals,'assists':assists,'shots':shots}
                schedule[day]=vals
            players[pid]=(elig,schedule)
    size=len(slots);order=[]
    for r in range(size):
        order+=list(range(teams)) if r%2==0 else list(reversed(range(teams)))
    names=['user']+[f'opponent{i}' for i in range(1,teams)]
    owners={t:[] for t in names}
    if keepers:
        # Explicit initial keeper state consumes the first round and is shared by all policies.
        eligible=sorted(players)
        for i,t in enumerate(names):owners[t]=[eligible[i]]
        order=order[teams:]
    actors=['USER' if i==0 else f'anon{i}' for i in order]
    # In keeper cases the second round may start with an opponent. normalize initial
    # public ledger temporarily; the tournament reorients at each actual decision.
    safeactors=actors if actors and actors[0]=='USER' else ['USER']+actors
    d=build_fixture(f'synthetic_{family.lower()}_{seed}_{teams}',slots,players,owners['user'],safeactors,[{'goals':1,'assists':2,'shots':8,**({'saves':50,'shots_against':55} if goalie else {})}],categories=cats,opponent_groups=[owners[t] for t in names[1:]],period_count=2,rollouts=rollouts)
    d['league']['draft_config']['draft_type']='SNAKE'
    d['methodology_config']['seed']=keyed_seed('ROLLOUT-v1',family,seed,teams)%2147483647
    d['methodology_config']['max_search_nodes']=1000000
    d['methodology_config']['max_lineup_plans']=200000
    d['source_metadata']['adapter_version']='0.3.2'
    validate(d)
    return {'snapshot':d,'family':family,'world_seed':seed,'team_ids':names,'initial_rosters':owners,'draft_order':[names[i] for i in order],'roster_size':size,'public_generator_version':'PUBLIC-v1','keeper_mode':keepers}
