"""Separate forecast-only lineup policy and outcome scorer.

This module does NOT import TeamEvaluator, ResearchRunner, or its score functions.
Lineup plans are committed using public forecasts; hidden outcomes are accepted
only by the scoring stage. Every tested drafting policy uses the same manager.
"""
from __future__ import annotations
from decimal import Decimal,ROUND_HALF_EVEN
import math
import statistics
import numpy as np
from scipy.optimize import linear_sum_assignment
from .worlds import keyed_seed


def validate_audit_scope(data):
    L=data['league']
    if L['scoring_objective']['type']!='H2H_EACH_CATEGORY':raise ValueError('Independent evaluator supports each-category only.')
    if L['lineup_rules']['active_limits'] or L['lineup_rules']['minimum_participation']:
        raise ValueError('Independent audit caps/minimum participation are not implemented; no silent relaxation.')
    if any(s['slot_class'] not in ('ACTIVE','BENCH') for s in L['roster_slots']):
        raise ValueError('Independent audit does not yet model IR or reserve storage.')
    if any(p['status_tags'] for p in data['league_players']):raise ValueError('Dynamic status semantics are outside audit scope.')
    if any('construction_lock' in e for t in data['roster_state']['teams'] for e in t['entries']):raise ValueError('Construction locks are outside audit lineup scope.')


def expected_period_stats(data):
    periods=sorted(p['period_id'] for p in data['exposure_model']['periods'])
    stats=sorted(s['stat_key'] for s in data['league']['stat_definitions'])
    return {p['player_id']:{t:{k:math.fsum(s['probability']*s['player_period_stats'][p['player_id']][t].get(k,0) for s in data['exposure_model']['forecast_scenarios']) for k in stats} for t in periods} for p in data['players']}


def lineup_plan(data,roster):
    """Global additive projection matching, not the engine's nonlinear utility.

    COUNT weights use fixed-cohort per-period standard deviations. RATIO weights
    use volume-aware influence at a pooled public rate. No realized performance,
    reference outcome, draft rank or recommendation score is read here.
    """
    validate_audit_scope(data)
    E=expected_period_stats(data);lp={p['player_id']:p for p in data['league_players']}
    groups={}
    for p in sorted(data['exposure_model']['periods'],key=lambda p:(p['starts_at'],p['period_id'])):
        groups.setdefault(p['lock_group_id'],[]).append(p['period_id'])
    positions={p['position_id']:p for p in data['league']['positions']}
    cohort=sorted(data['reference_model']['normalization_player_ids']);features=[]
    for c in data['league']['categories']:
        eligible=[p for p in cohort if any(positions[q]['position_group_id'] in c['applicable_position_group_ids'] for q in lp[p]['eligibility']['position_ids'])]
        calc=c['calculation'];rate=0.
        if calc['type']=='RATIO':
            den=math.fsum(v.get(calc['denominator_stat_key'],0) for p in eligible for v in E[p].values())
            rate=math.fsum(v.get(calc['numerator_stat_key'],0) for p in eligible for v in E[p].values())/den if den else 0.
        def f(v):
            if calc['type']=='COUNT':return v.get(calc['stat_key'],0)
            return v.get(calc['numerator_stat_key'],0)-rate*v.get(calc['denominator_stat_key'],0)
        values=[f(v) for p in eligible for v in E[p].values() if v['games']>0]
        sd=statistics.pstdev(values) if values else 0.
        # A constant public category receives no differentiating lineup weight.
        features.append((c,rate,sd,set(eligible)))
    slots=[(f'{s["slot_definition_id"]}#{i+1}',s) for s in sorted(data['league']['roster_slots'],key=lambda s:s['slot_definition_id']) if s['slot_class']=='ACTIVE' for i in range(s['capacity'])]
    ids=sorted(roster);plan={}
    for group,periods in groups.items():
        values={}
        for pid in ids:
            score=0.
            for c,rate,sd,applicable in features:
                if not sd or pid not in applicable:continue
                calc=c['calculation'];direction=1 if c['direction']=='HIGHER_IS_BETTER' else -1
                for t in periods:
                    v=E[pid][t]
                    influence=v.get(calc['stat_key'],0) if calc['type']=='COUNT' else v.get(calc['numerator_stat_key'],0)-rate*v.get(calc['denominator_stat_key'],0)
                    score+=direction*c['weight']*influence/sd
            values[pid]=score
        # Rows are slots. Dedicated empty columns permit benching a negative-rate player.
        costs=np.full((len(slots),len(ids)+len(slots)),np.inf)
        costs[:,len(ids):]=0.
        for i,(sid,s) in enumerate(slots):
            for j,pid in enumerate(ids):
                eligible=set(lp[pid]['eligibility']['position_ids'])
                if s['eligibility']['mode']=='POSITION_SET':eligible &= set(s['eligibility']['position_ids'])
                if eligible and any(E[pid][t]['games']>0 for t in periods):costs[i,j]=-values[pid]
        rows,cols=linear_sum_assignment(costs)
        plan[group]=[{'player_id':ids[j],'slot_instance_id':slots[i][0]} for i,j in zip(rows,cols) if j<len(ids)]
    return plan


def sum_plan(data,plan,tape):
    """Aggregate a previously committed plan. Never optimize using this tape."""
    groups={}
    for p in data['exposure_model']['periods']:groups.setdefault(p['lock_group_id'],[]).append(p['period_id'])
    totals={s['stat_key']:0. for s in data['league']['stat_definitions']}
    for group,rows in plan.items():
        if len({r['player_id'] for r in rows})!=len(rows) or len({r['slot_instance_id'] for r in rows})!=len(rows):raise ValueError('Duplicated active assignment.')
        for row in rows:
            for period in groups[group]:
                for k,v in tape[row['player_id']][period].items():totals[k]+=v
    return totals


def score_matchup(league,a,b):
    """Official synthetic each-category results, independently implemented."""
    objective=league['scoring_objective'];credits={}
    def value(c,stats):
        x=c['calculation']
        if x['type']=='COUNT':return stats[x['stat_key']]
        den=stats[x['denominator_stat_key']]
        return x['multiplier']*stats[x['numerator_stat_key']]/den if den else None
    for c in league['categories']:
        x,y=value(c,a),value(c,b)
        if x is None and y is None:r=objective['both_undefined_tie_credit']
        elif x is None:r=0.
        elif y is None:r=1.
        else:
            scale=Decimal(1).scaleb(-c['precision'])
            x=Decimal(str(x)).quantize(scale,rounding=ROUND_HALF_EVEN);y=Decimal(str(y)).quantize(scale,rounding=ROUND_HALF_EVEN)
            r=objective['category_tie_credit'] if x==y else float(x>y if c['direction']=='HIGHER_IS_BETTER' else x<y)
        credits[c['category_id']]=r
    score=math.fsum(c['weight']*credits[c['category_id']] for c in league['categories'])/math.fsum(c['weight'] for c in league['categories'])
    return score,credits


def hidden_tape(public_data,family,world_seed,draw,namespace='AUDIT-v1'):
    """Correlated, physically coherent hidden outcomes; no recommendation input.

    Predictive reference generation uses a separate namespace and FORECAST family.
    Audit regimes add player/period shocks and selected model mismatch. Draws are
    keyed by world/player/period, so player outcomes are identical across policies.
    """
    E=expected_period_stats(public_data);result={}
    base=keyed_seed(namespace,family,world_seed,draw)
    periods=sorted(next(iter(E.values())))
    pace={t:np.random.default_rng(keyed_seed(base,'pace',t)).lognormal(-.5*.12**2,.12) for t in periods}
    for p in sorted(E):
        rngp=np.random.default_rng(keyed_seed(base,'player',p))
        sigma=0 if family=='FORECAST' else .20 if family=='BALANCED' else .35
        talent=rngp.lognormal(-.5*sigma*sigma,sigma)
        if family=='ROLE_REVERSAL' and not p.startswith('g_'):
            # Known-to-auditor systematic error, deliberately hidden from draft agents.
            talent*=.65 if int(p.split('_')[-1])%2==0 else 1.30
        result[p]={}
        for t in periods:
            v=E[p][t];out={k:0. for k in v};rng=np.random.default_rng(keyed_seed(base,p,t))
            if not v['games']:
                result[p][t]=out;continue
            played=not (family in ('ROLE_REVERSAL','GOALIE_RATES') and rng.random()<.08)
            out['games']=float(played)
            if played:
                m=talent*(1 if family=='FORECAST' else pace[t])
                if v.get('shots_against',0)>0:
                    against=int(rng.poisson(v['shots_against']*(1 if family=='FORECAST' else pace[t])))
                    q=v['saves']/v['shots_against']
                    if family=='GOALIE_RATES':q=min(.985,max(.75,q+rngp.normal(0,.018)))
                    out['shots_against']=float(against);out['saves']=float(rng.binomial(against,q))
                if v.get('shots',0)>0:
                    shots=int(rng.poisson(v['shots']*m));q=min(1.,max(0.,v['goals']/v['shots']))
                    out['shots']=float(shots);out['goals']=float(rng.binomial(shots,q));out['assists']=float(rng.poisson(v.get('assists',0)*m))
            result[p][t]=out
    return result
