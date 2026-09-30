"""Predeclared diagnostic replays: no fitted coefficients or changed defaults."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import statistics
import time
import numpy as np
from decision_engine.validation import canonical_hash
from .worlds import keyed_seed
from .replication import (build_world,build_models,role_adjusted_public,score_committed_cases,
                          _atomic_json,league_rows)
from .robustness import contrast,source_hash
from .drafts import DraftContext
from .robust_scoring import CDFEvaluator
from .independent import hidden_tape


def prefix_ranking(result: dict, budget: int) -> list[dict]:
    """Exact prefix means from one fixed, paired rollout stream.

    Decision scores are recomputed with the same IV-based anchor and tie-breaks;
    no future realized outcomes enter this numerical-convergence calculation.
    """
    if result.get('run_status')!='ROSTER_ONLY':raise ValueError('A complete roster-only result is required.')
    if type(budget) is not int or budget<1:raise ValueError('Positive integer budget required.')
    rows=result['ranked_candidates']
    if not rows or any(len(r['neutral_path_utility_samples'])<budget for r in rows):
        raise ValueError('Budget exceeds available paired samples.')
    utilities={r['player_id']:math.fsum(r['neutral_path_utility_samples'][:budget])/budget for r in rows}
    reference=result['score_reference_player_id']
    ranked=[{'player_id':r['player_id'],'intrinsic_value':r['intrinsic_value'],
             'neutral_utility':utilities[r['player_id']],
             'decision_score':100*(utilities[r['player_id']]-utilities[reference])}
            for r in rows]
    return sorted(ranked,key=lambda r:(-r['decision_score'],-r['decision_score'],-r['intrinsic_value'],r['player_id']))


def regret_summary(values: list[float]) -> dict:
    if not values:return {'states':0}
    if any(not math.isfinite(v) or v < -1e-10 for v in values):raise ValueError('Finite nonnegative regrets required.')
    return {'states':len(values),'mean_regret_points':statistics.fmean(values),
        'median_regret_points':statistics.median(values),
        'p95_regret_points':float(np.quantile(values,.95)),'worst_regret_points':max(values),
        'mean_target_0_10_pass':statistics.fmean(values)<=.10,
        'p95_target_0_25_pass':float(np.quantile(values,.95))<=.25}


def _read_compatible(parent: Path, stratum_id: str, label: int, protocol: dict) -> dict:
    w=json.loads((parent/f'{stratum_id}_{label}.json').read_text())
    if w['protocol_sha256']!=canonical_hash(protocol):raise ValueError('Parent protocol differs.')
    commit=json.loads((parent/'commitments'/f'{stratum_id}_{label}.json').read_text())
    if canonical_hash(commit)!=w['pre_outcome_commitment_sha256']:
        raise ValueError('Parent commitment changed.')
    return w


def run_numerical_diagnostic(protocol: dict, parent: Path, output: Path) -> dict:
    config=protocol['numerical_diagnostic']
    if config['reference_budget']!=max(config['budgets']):raise ValueError('Reference must be largest normal budget.')
    root=Path(__file__).resolve().parents[1];code_hash=source_hash(root)
    state_rows=[];started=time.perf_counter()
    for sid in config['strata']:
        stratum=next(s for s in protocol['strata'] if s['stratum_id']==sid)
        label=config['seed_label']
        raw=build_world(stratum,label,protocol)
        saved=_read_compatible(parent,sid,label,protocol)
        selected=next(c for c in saved['cases'] if c['focus_team']=='user' and c['policy']=='EUR' and c['management_policy']=='ADDITIVE')
        if selected['status']!='COMPLETE':raise ValueError('No source draft for prespecified stability state.')
        base_models,_=build_models(raw,protocol);base=base_models['EUR']
        turns=[r['pick_index'] for r in selected['draft']['trace'] if r['team_id']=='user']
        states=[('FIRST',turns[0]),('MIDDLE',turns[len(turns)//2]),('LAST',turns[-1])]
        for phase,index in states:
            owned=copy.deepcopy(raw['initial_rosters'])
            for pick in selected['draft']['trace']:
                if pick['pick_index']>=index:break
                owned[pick['team_id']].append(pick['player_id'])
            maximum=config['check_budget'] if phase=='FIRST' else config['reference_budget']
            model=copy.deepcopy(base);model['snapshot']['methodology_config']['rollouts']=maximum
            context=DraftContext(model,CDFEvaluator)
            t=time.perf_counter();result=context.engine_pick(owned,index,'user');elapsed=time.perf_counter()-t
            forecasts=prefix_ranking(result,config['reference_budget'])
            q={r['player_id']:r['neutral_utility'] for r in forecasts};best=forecasts[0]['neutral_utility']
            budgets={}
            for n in config['budgets']:
                rank=prefix_ranking(result,n);chosen=rank[0]['player_id']
                budgets[str(n)]={'recommended_player_id':chosen,
                    'regret_at_reference_points':max(0.,100*(best-q[chosen])),
                    'same_first_choice':chosen==forecasts[0]['player_id'],
                    'ranked_candidates':rank}
            check=None
            if maximum>config['reference_budget']:
                checked=prefix_ranking(result,maximum);values={r['player_id']:r['neutral_utility'] for r in checked}
                check={'budget':maximum,'recommended_player_id':checked[0]['player_id'],
                    'reference_regret_at_check_points':max(0.,100*(checked[0]['neutral_utility']-values[forecasts[0]['player_id']])),
                    'ranked_candidates':checked}
            state_rows.append({'stratum_id':sid,'seed_label':label,'phase':phase,'pick_index':index,
                'source_draft_commitment_sha256':selected['commitment_sha256'],
                'input_sha256':result['provenance']['full_input_sha256'],
                'candidate_count':len(forecasts),'maximum_budget_executed':maximum,
                'maximum_budget_elapsed_seconds':elapsed,'budgets':budgets,'reference_check':check})
    def summary(rows):
        return {str(n):{**regret_summary([s['budgets'][str(n)]['regret_at_reference_points'] for s in rows]),
            'same_first_choice_count':sum(s['budgets'][str(n)]['same_first_choice'] for s in rows)} for n in config['budgets']}
    checks=[r['reference_check']['reference_regret_at_check_points'] for r in state_rows if r['reference_check']]
    report={'status':'CONDITIONAL_NUMERICAL_DIAGNOSTIC_NOT_PREDICTIVE_VALIDATION',
        'protocol_sha256':canonical_hash(protocol),'source_sha256':code_hash,
        'reference_budget':config['reference_budget'],'check_budget':config['check_budget'],
        'all_states':summary(state_rows),
        'states_with_future_user_picks':summary([s for s in state_rows if s['phase']!='LAST']),
        'reference_check_summary':regret_summary(checks),'states':state_rows,
        'scope':'Fixed EUR model/field/scenarios; four declared worlds, first/middle/last decisions. Prefix means reuse identical paired paths. Final picks are reported separately; no realized outcomes or tuning.',
        'latency_scope':'Only maximum-budget timings measured, not prefix-budget latency.',
        'elapsed_seconds':time.perf_counter()-started}
    if source_hash(root)!=code_hash:raise ValueError('Source changed during numerical diagnostic.')
    _atomic_json(output,report);return report


def run_role_diagnostic(protocol: dict,parent: Path,output: Path) -> dict:
    """Cross hidden role assignments while holding each public draft/plan fixed.

    The public league is the inferential cluster, not each role scenario or tape.
    Role scenarios share the same outcome-stream root for paired stress contrasts.
    """
    config=protocol['role_diagnostic'];root=Path(__file__).resolve().parents[1]
    code_hash=source_hash(root);started=time.perf_counter();records=[];references=[]
    stratum=next(s for s in protocol['strata'] if s['stratum_id']==config['stratum_id'])
    for label in protocol['seed_labels']:
        raw=build_world(stratum,label,protocol)
        saved=_read_compatible(parent,stratum['stratum_id'],label,protocol)
        source=[c for c in saved['cases'] if c['management_policy']=='ADDITIVE' and c['policy'] in config['policies']]
        if any(c['status']!='COMPLETE' for c in source):raise ValueError('Cannot silently skip failed source draft.')
        source=[{'focus_team':c['focus_team'],'policy':c['policy'],'management_policy':'ADDITIVE',
                 'status':'COMPLETE','committed_plans':c['committed_plans']} for c in source]
        source_hash_before=canonical_hash(source);snapshot_before=canonical_hash(raw['snapshot'])
        seed=keyed_seed('M034_ROLE_DIAGNOSTIC_ROOT',label,stratum['stratum_id'])
        outcomes_seed=keyed_seed('M034_ROLE_DIAGNOSTIC_EVENTS',seed)
        baseline=None
        for magnitude in config['magnitudes']:
            assignments=range(1) if magnitude==0 else range(config['assignments'])
            for assignment in assignments:
                hidden,factors=role_adjusted_public(raw['snapshot'],seed,assignment,magnitude)
                cases=copy.deepcopy(source)
                tapes=(hidden_tape(hidden,'BALANCED',outcomes_seed,d,namespace='M034_DIAG_ROLE_EVENTS') for d in range(config['audit_draws']))
                hashes=score_committed_cases(raw['snapshot'],raw['team_ids'],cases,tapes)
                scores={p:statistics.fmean(c['category_share'] for c in cases if c['policy']==p) for p in config['policies']}
                if magnitude==0:baseline=scores
                if baseline is None:raise ValueError('Zero-shift control must precede role magnitudes.')
                record={'world_id':saved['world_id'],'stratum_id':stratum['stratum_id'],
                        'seed_label':label,'magnitude':magnitude,'role_assignment':assignment,
                        'scores':scores,'factors':factors,'tape_hash_root':canonical_hash(hashes),
                        'advantage_vs_B3_pp':{p:100*(scores[p]-scores['B3']) for p in config['policies']},
                        'change_in_advantage_vs_no_shift_pp':{p:100*((scores[p]-scores['B3'])-(baseline[p]-baseline['B3'])) for p in config['policies']}}
                records.append(record)
        if canonical_hash(source)!=source_hash_before or canonical_hash(raw['snapshot'])!=snapshot_before:
            raise AssertionError('Auditor mutated a committed public decision input.')
        references.append({'seed_label':label,'world_id':saved['world_id'],
            'parent_commitment_sha256':saved['pre_outcome_commitment_sha256'],
            'plans_sha256':source_hash_before,'snapshot_sha256':snapshot_before})
    levels={}
    for magnitude in config['magnitudes']:
        level=[r for r in records if r['magnitude']==magnitude];clusters=[]
        for label in protocol['seed_labels']:
            group=[r for r in level if r['seed_label']==label]
            clusters.append({'stratum_id':config['stratum_id'],'world_id':group[0]['world_id'],
                'scores':{p:statistics.fmean(r['scores'][p] for r in group) for p in config['policies']}})
        levels[str(magnitude)]={'public_league_clusters':len(clusters),'role_assignments_evaluated':len(level),
            'mean_category_share':{p:statistics.fmean(r['scores'][p] for r in clusters) for p in config['policies']},
            'versus_B3':{p:contrast(clusters,p,'B3',keyed_seed('M034_ROLE_BOOT',magnitude,p)) for p in config['policies'] if p!='B3'},
            'scenario_distribution_vs_B3':{p:{'mean_pp':statistics.fmean(r['advantage_vs_B3_pp'][p] for r in level),
                'p05_pp':float(np.quantile([r['advantage_vs_B3_pp'][p] for r in level],.05)),
                'p95_pp':float(np.quantile([r['advantage_vs_B3_pp'][p] for r in level],.95)),
                'fraction_below_minus_one_pp':statistics.fmean(r['advantage_vs_B3_pp'][p]<-1 for r in level)}
                for p in config['policies'] if p!='B3'}}
    report={'status':'FIXED_DRAFT_ROLE_ASSIGNMENT_DIAGNOSTIC',
        'protocol_sha256':canonical_hash(protocol),'source_sha256':code_hash,
        'public_league_clusters':len(references),'new_drafts_generated':0,
        'role_scenario_count':len(records),'levels':levels,'commitment_references':references,
        'records':records,'scope':'Conditional replay of precommitted additive-management drafts. Factors are never shown to agents. Role scenarios and outcome draws are not independent public leagues; intervals cluster after averaging assignments within each league.',
        'elapsed_seconds':time.perf_counter()-started}
    if source_hash(root)!=code_hash:raise ValueError('Source changed during role diagnostic.')
    _atomic_json(output,report);return report


def depth_pair_contrasts(results: list,protocol: dict) -> dict:
    rows,excluded=league_rows(results,protocol['policies'],'ADDITIVE')
    paired={}
    for r in rows:
        layout='DEEP' if 'DEEP' in r['stratum_id'] else 'SHALLOW'
        paired.setdefault(r['public_pair_id'],{})[layout]=r
    pairs=[]
    for key,g in sorted(paired.items()):
        if set(g)!= {'DEEP','SHALLOW'}:continue
        scores={}
        for p in protocol['policies']:
            scores[p+'_DEEP']=g['DEEP']['scores'][p]-g['DEEP']['scores']['B3']
            scores[p+'_SHALLOW']=g['SHALLOW']['scores'][p]-g['SHALLOW']['scores']['B3']
        pairs.append({'world_id':key,'stratum_id':'PAIRED_DEPTH','scores':scores})
    return {'paired_public_pools':len(pairs),'excluded_paired_seats':excluded,
        'change_in_advantage_vs_B3':{p:contrast(pairs,p+'_DEEP',p+'_SHALLOW',keyed_seed('M034_DEPTH_BOOT',p))
            for p in protocol['policies'] if p!='B3'},
        'scope':'Same 12-player pool, forecast means, public randomness and audit tapes; 3 versus 5 roster slots. Field and draft horizon necessarily adapt to declared league demand. No independent-world claim for the two layouts.'}
