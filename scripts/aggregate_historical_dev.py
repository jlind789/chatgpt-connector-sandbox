#!/usr/bin/env python3
from __future__ import annotations
import json,statistics,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
IN=ROOT/'historical/seat_results_v0318'
OUT=ROOT/'historical/results_v0318'
POLICIES=('B1','B2','B3','DECISION_ENGINE')

def main():
    files=sorted(IN.glob('*.json'))
    if len(files)!=12:raise RuntimeError(f'Expected 12 seat results, found {len(files)}')
    rows=[json.loads(p.read_text()) for p in files]
    keys={(r['season'],r['seat']) for r in rows}
    expected={(s,i) for s in ('2022-23','2023-24') for i in range(1,7)}
    if keys!=expected:raise RuntimeError(f'Seat result coverage mismatch: missing={sorted(expected-keys)} extra={sorted(keys-expected)}')
    overall={p:statistics.fmean(r['results'][p]['outcome']['mean_weekly_category_share'] for r in rows) for p in POLICIES}
    byseason={}
    for season in ('2022-23','2023-24'):
        rr=[r for r in rows if r['season']==season]
        scores={p:statistics.fmean(x['results'][p]['outcome']['mean_weekly_category_share'] for x in rr) for p in POLICIES}
        byseason[season]={'scores':scores,'engine_minus_pp':{p:100*(scores['DECISION_ENGINE']-scores[p]) for p in ('B1','B2','B3')},
                          'engine_resolved_decisions':sum(x['summary']['engine_resolved_decisions'] for x in rr),
                          'engine_unresolved_decisions':sum(x['summary']['engine_unresolved_decisions'] for x in rr)}
    deltas={p:[r['summary']['engine_minus_pp'][p] for r in rows] for p in ('B1','B2','B3')}
    resolved=sum(r['summary']['engine_resolved_decisions'] for r in rows)
    unresolved=sum(r['summary']['engine_unresolved_decisions'] for r in rows)
    summary={
      'version':'v0.3.18-historical-development',
      'status':'DEVELOPMENT_ONLY_NOT_PROMOTION_EVIDENCE',
      'seasons':['2022-23','2023-24'],
      'seats_per_season':6,
      'completed_counterfactual_focus_drafts':len(rows)*4,
      'scores':overall,
      'engine_minus_baseline_pp':{p:{
          'mean':statistics.fmean(deltas[p]),
          'median':statistics.median(deltas[p]),
          'min':min(deltas[p]),'max':max(deltas[p]),
          'positive_seats':sum(x>0 for x in deltas[p]),
          'negative_seats':sum(x<0 for x in deltas[p]),
          'zero_seats':sum(x==0 for x in deltas[p])
      } for p in ('B1','B2','B3')},
      'by_season':byseason,
      'engine_decision_confidence':{
          'resolved':resolved,'unresolved':unresolved,
          'unresolved_rate':unresolved/(resolved+unresolved) if resolved+unresolved else None
      },
      'reporting_contract':{
          'ALL':'Final roster outcome is reported for every complete engine draft.',
          'RESOLVED':'Resolved engine picks are reported separately by count and realized started contribution in seat artifacts.',
          'UNRESOLVED':'Unresolved engine picks retain their label/fallback flag and are reported separately in seat artifacts.',
          'causal_warning':'Resolved/unresolved started-production cohorts are descriptive, not causal per-pick value estimates.'
      },
      'validation_season_2024_25_opened':False,
      'holdout_season_2025_26_opened':False,
      'interpretation':'Development evidence only. Any methodology change may use these seasons but must be frozen before 2024-25 validation is opened.'
    }
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (OUT/'all_seat_results.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
