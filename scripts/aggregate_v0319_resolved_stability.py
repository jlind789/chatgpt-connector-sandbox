#!/usr/bin/env python3
from __future__ import annotations
import json, statistics
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];IN=ROOT/'historical/calibration_v0319/resolved_audit_chunks';OUT=ROOT/'historical/calibration_v0319'
def main():
    files=sorted(IN.glob('*.json'))
    if len(files)!=12:raise RuntimeError(f'Expected 12 files, found {len(files)}')
    rows=[r for p in files for r in json.loads(p.read_text())['rows']]
    if len(rows)!=33:raise RuntimeError(f'Expected 33 source-resolved decisions, found {len(rows)}')
    hist=Counter(str(r['source_resolution_budget']) for r in rows)
    summary={
      'version':'v0.3.19-resolved-stability-audit',
      'source_resolved_decisions':len(rows),
      'source_resolution_budget_histogram':dict(sorted(hist.items())),
      'source_leader_matches_next_checkpoint':sum(r['source_leader_matches_next'] for r in rows),
      'source_leader_matches_next_checkpoint_rate':sum(r['source_leader_matches_next'] for r in rows)/len(rows),
      'source_leader_matches_128':sum(r['source_leader_matches_128'] for r in rows),
      'source_leader_matches_128_rate':sum(r['source_leader_matches_128'] for r in rows)/len(rows),
      'source_leader_still_resolved_at_128':sum(r['source_leader_matches_128'] and r['resolved_at_128'] for r in rows),
      'source_leader_still_resolved_at_128_rate':sum(r['source_leader_matches_128'] and r['resolved_at_128'] for r in rows)/len(rows),
      'resolved_at_128_any_leader':sum(r['resolved_at_128'] for r in rows),
      'mean_elapsed_seconds':statistics.fmean(r['elapsed_seconds'] for r in rows),
      'validation_season_2024_25_opened':False,
      'holdout_season_2025_26_opened':False,
      'interpretation':'Development-only stability audit of the 33 decisions that v0.3.18 had already labeled resolved.'
    }
    (OUT/'resolved_stability_audit_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (OUT/'resolved_stability_audit_all.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
