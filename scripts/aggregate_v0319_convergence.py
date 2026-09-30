#!/usr/bin/env python3
from __future__ import annotations
import json, statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
IN=ROOT/'historical/calibration_v0319/chunks'
OUT=ROOT/'historical/calibration_v0319'
EXPECTED_CHUNKS=2*6*5

def mean_or_none(xs):
    return statistics.fmean(xs) if xs else None

def main():
    files=sorted(IN.glob('*.json'))
    if len(files)!=EXPECTED_CHUNKS:raise RuntimeError(f'Expected {EXPECTED_CHUNKS} chunk files, found {len(files)}')
    chunks=[json.loads(p.read_text()) for p in files]
    rows=[r for c in chunks for r in c['rows']]
    source=json.loads((ROOT/'historical/results_v0318/summary.json').read_text())
    expected=source['engine_decision_confidence']['unresolved']
    if len(rows)!=expected:raise RuntimeError(f'Expected {expected} unresolved decisions, found {len(rows)}')
    keys=[(r['season'],r['seat'],r['pick_index']) for r in rows]
    if len(set(keys))!=len(keys):raise RuntimeError('Duplicate calibrated decision key.')

    def bucket(sub):
        first=Counter(str(r['first_resolution_budget']) if r['first_resolution_budget'] is not None else 'NONE' for r in sub)
        terminal=Counter(str(r['terminal_budget']) for r in sub)
        resolved=[r for r in sub if r['terminal_resolved']]
        unresolved=[r for r in sub if not r['terminal_resolved']]
        return {
          'decisions':len(sub),
          'resolved_at_128':sum(r['resolved_at_128'] for r in sub),
          'resolved_terminal':len(resolved),
          'unresolved_after_512':sum((not r['terminal_resolved']) and r['terminal_budget']==512 for r in sub),
          'first_resolution_budget_histogram':dict(sorted(first.items())),
          'terminal_budget_histogram':dict(sorted(terminal.items())),
          'original_fallback_matches_128':sum(r['original_fallback_matches_128'] for r in sub),
          'original_fallback_matches_terminal':sum(r['original_fallback_matches_terminal'] for r in sub),
          'original_fallback_terminal_match_rate':mean_or_none([1.0 if r['original_fallback_matches_terminal'] else 0.0 for r in sub]),
          'terminal_resolved_fallback_match_rate':mean_or_none([1.0 if r['original_fallback_matches_terminal'] else 0.0 for r in resolved]),
          'terminal_unresolved_fallback_match_rate':mean_or_none([1.0 if r['original_fallback_matches_terminal'] else 0.0 for r in unresolved]),
          'mean_terminal_runner_up_gap_points':mean_or_none([r['terminal_runner_up_gap_points'] for r in sub if r['terminal_runner_up_gap_points'] is not None]),
          'mean_elapsed_seconds':mean_or_none([r['elapsed_seconds'] for r in sub])
        }

    by_ceiling={}
    for ceiling in (16,32,64):
        by_ceiling[str(ceiling)]=bucket([r for r in rows if r['original_max_budget']==ceiling])
    by_season={s:bucket([r for r in rows if r['season']==s]) for s in ('2022-23','2023-24')}

    early=[r for r in rows if r['first_resolution_budget'] is not None and r['first_resolution_budget']<128]
    early_stable=[r for r in early if r['first_resolution_matches_128'] and r['resolved_at_128']]
    stages=defaultdict(list)
    for r in rows:
        for s in r['stages']:
            if s['budget']>=8:stages[s['budget']].append((r,s))
    checkpoints={}
    for b in sorted(stages):
        rr=stages[b]
        checkpoints[str(b)]={
          'decisions_reaching_budget':len(rr),
          'resolved_under_rule':sum(s['resolved_under_rule'] for _,s in rr),
          'mean_candidate_count':mean_or_none([s['candidate_count'] for _,s in rr]),
          'mean_runner_up_gap_points':mean_or_none([s['runner_up_gap_points'] for _,s in rr if s.get('runner_up_gap_points') is not None]),
          'mean_worst_ci95_high_points':mean_or_none([s['worst_ci95_high_points'] for _,s in rr if s.get('worst_ci95_high_points') is not None])
        }

    summary={
      'version':'v0.3.19-convergence-calibration',
      'status':'DEVELOPMENT_ONLY_NOT_PROMOTION_EVIDENCE',
      'source_results_version':source['version'],
      'source_unresolved_decisions':expected,
      'calibrated_unresolved_decisions':len(rows),
      'overall':bucket(rows),
      'by_original_max_budget':by_ceiling,
      'by_season':by_season,
      'checkpoint_diagnostics':checkpoints,
      'early_resolution_stability_to_128':{
        'first_resolved_before_128':len(early),
        'same_leader_and_resolved_at_128':len(early_stable),
        'rate':len(early_stable)/len(early) if early else None
      },
      'validation_season_2024_25_opened':False,
      'holdout_season_2025_26_opened':False,
      'interpretation':'Development-only convergence calibration. No historical outcome values were used by the calibration runner. Any methodology change must be frozen before validation is opened.'
    }
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (OUT/'all_calibrated_decisions.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
