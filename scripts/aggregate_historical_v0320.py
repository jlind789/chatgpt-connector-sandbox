#!/usr/bin/env python3
from __future__ import annotations
import json,statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
IN=ROOT/'historical/seat_results_v0320'
OUT=ROOT/'historical/results_v0320'
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
        byseason[season]={
          'scores':scores,
          'engine_minus_pp':{p:100*(scores['DECISION_ENGINE']-scores[p]) for p in ('B1','B2','B3')},
          'engine_confirmed_decisions':sum(x['summary']['engine_confirmed_decisions'] for x in rr),
          'engine_practical_tie_decisions':sum(x['summary']['engine_practical_tie_decisions'] for x in rr),
          'engine_unconfirmed_edge_decisions':sum(x['summary']['engine_unconfirmed_edge_decisions'] for x in rr)
        }
    deltas={p:[r['summary']['engine_minus_pp'][p] for r in rows] for p in ('B1','B2','B3')}
    confirmed=sum(r['summary']['engine_confirmed_decisions'] for r in rows)
    ties=sum(r['summary']['engine_practical_tie_decisions'] for r in rows)
    uncertain=sum(r['summary']['engine_unconfirmed_edge_decisions'] for r in rows)
    total=confirmed+ties+uncertain
    summary={
      'version':'v0.3.20-historical-development',
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
      'engine_decision_status':{
          'confirmed_edge':confirmed,
          'practical_tie':ties,
          'unconfirmed_edge':uncertain,
          'total':total,
          'confirmed_rate':confirmed/total if total else None,
          'practical_tie_rate':ties/total if total else None,
          'unconfirmed_edge_rate':uncertain/total if total else None
      },
      'reporting_contract':{
          'ALL':'Final roster outcome is reported for every complete engine draft.',
          'CONFIRMED_EDGE':'Two consecutive resolved checkpoints with the same leader.',
          'PRACTICAL_TIE':'At 512 rollouts the final runner-up mean gap is at or below 0.10 utility points.',
          'UNCONFIRMED_EDGE':'At 512 rollouts a larger mean edge remains without two-checkpoint confirmation.',
          'causal_warning':'Status-cohort started production is descriptive, not causal per-pick value.'
      },
      'validation_season_2024_25_opened':False,
      'holdout_season_2025_26_opened':False,
      'interpretation':'Development evidence only. Promotion requires a frozen policy before 2024-25 validation is opened.'
    }
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (OUT/'all_seat_results.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
