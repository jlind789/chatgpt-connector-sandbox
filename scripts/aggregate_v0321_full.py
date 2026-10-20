#!/usr/bin/env python3
"""Aggregate all 12 v0.3.21 development seats and apply the unchanged gate."""
from __future__ import annotations
import argparse
import json
import math
import statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BASELINE=ROOT/"historical/results_v0318/summary.json"


def write(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True)+"\n",encoding="utf-8")


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--run-dir",type=Path,required=True)
    args=ap.parse_args()
    run=args.run_dir.resolve()
    files=sorted((run/"seat_results").glob("*.json"))
    expected={(s,i) for s in ("2022-23","2023-24") for i in range(1,7)}
    if len(files)!=12:
        raise RuntimeError(f"Expected 12 results, got {len(files)}")
    rows=[json.loads(p.read_text(encoding="utf-8")) for p in files]
    if {(r["season"],r["seat"]) for r in rows}!=expected:
        raise RuntimeError("Seat identity/coverage mismatch")
    if not all(r["version"]=="v0.3.21-development-seat" for r in rows):
        raise RuntimeError("Unexpected result version")
    if not all(r.get("draft_commitment_precedes_outcome_load") for r in rows):
        raise RuntimeError("Missing commit/evaluate boundary marker")
    commitment_index=json.loads((run/"commitment_manifest.json").read_text(encoding="utf-8"))
    if len(commitment_index["sha256"])!=12:
        raise RuntimeError("Commitment manifest coverage mismatch")
    for r in rows:
        import hashlib
        name=f"{r['season']}_seat{r['seat']}.json"
        committed=(run/"commitments"/name).read_bytes()
        csha=hashlib.sha256(committed).hexdigest()
        if (r["source_commitment_sha256"]!=csha
                or commitment_index["sha256"].get(name)!=csha):
            raise RuntimeError("Committed draft hash changed after Phase A")
        if len(r["results"]["DECISION_ENGINE"]["draft"]["decisions"])!=19:
            raise RuntimeError("Expected 19 Engine picks per seat")
        for draft in r["results"].values():
            if draft["draft"]["status"]!="COMPLETE":
                raise RuntimeError("Incomplete roster in result")

    policies=("B1","B2","B3","DECISION_ENGINE")
    scores={p:statistics.fmean(x["summary"]["scores"][p] for x in rows) for p in policies}
    by_season={}
    for season in ("2022-23","2023-24"):
        rr=[x for x in rows if x["season"]==season]
        ss={p:statistics.fmean(x["summary"]["scores"][p] for x in rr) for p in policies}
        by_season[season]={"scores":ss,
                            "engine_minus_B2_pp":100*(ss["DECISION_ENGINE"]-ss["B2"])}
    deltas={p:[r["summary"]["engine_minus_pp"][p] for r in rows] for p in ("B1","B2","B3")}
    counts={
        "confirmed_edge":sum(r["summary"]["engine_confirmed_decisions"] for r in rows),
        "practical_tie":sum(r["summary"]["engine_practical_tie_decisions"] for r in rows),
        "unconfirmed_edge":sum(r["summary"]["engine_unconfirmed_edge_decisions"] for r in rows)
    }
    if sum(counts.values())!=228:
        raise RuntimeError("Decision-status counts do not sum to 228")
    rate=counts["unconfirmed_edge"]/228
    restart=json.loads((run/"restart_certification/PASS.json").read_text(encoding="utf-8"))
    restart_ok=(
        restart.get("equivalent") is True
        and restart.get("comparison")=="BYTE_IDENTICAL_OUTCOME_BLIND_COMMITMENT"
        and restart.get("commitment_sha256")==commitment_index["sha256"]["2022-23_seat1.json"]
    )
    if not restart_ok:
        raise RuntimeError("Missing/invalid restart certification")

    reference=json.loads(BASELINE.read_text(encoding="utf-8"))
    baseline_equal=all(
        abs(scores[p]-reference["scores"][p])<=1e-12
        for p in ("B1","B2","B3")
    ) and all(
        abs(by_season[s]["scores"][p]-reference["by_season"][s]["scores"][p])<=1e-12
        for s in ("2022-23","2023-24") for p in ("B1","B2","B3")
    )
    gate={
        "version":"v0.3.21-full-development-gate/1",
        "scope":"DEVELOPMENT_ONLY_NO_UNSEEN_DATA",
        "gate_reference":"docs/decision-engine/PROMOTION-GATE-v0.3.20.md",
        "checks":{
            "12_complete_engine_seats":True,
            "B1_B2_B3_match_v0318":baseline_equal,
            "commitment_precedes_outcome_load":True,
            "restart_equivalence_certified":restart_ok,
            "228_decisions_exactly_one_status":True,
            "unconfirmed_rate_at_most_15pct":rate<=0.15,
            "practical_ties_preserve_status":True,
            "deterministic_simulation_continuations_preserve_status":all(
                d["fallback_used"] == (d["status"]!="CONFIRMED_EDGE")
                for r in rows for d in r["results"]["DECISION_ENGINE"]["draft"]["decisions"]
            ),
            "engine_mean_exceeds_B2":scores["DECISION_ENGINE"]>scores["B2"],
            "engine_mean_not_below_B1_or_B3":all(scores["DECISION_ENGINE"]>=scores[p] for p in ("B1","B3")),
            "median_seat_E_minus_B2_positive":statistics.median(deltas["B2"])>0,
            "at_least_7_of_12_seats_E_above_B2":sum(x>0 for x in deltas["B2"])>=7,
            "no_development_season_below_B2_by_over_1pp":all(by_season[s]["engine_minus_B2_pp"]>=-1.0 for s in by_season),
            "unseen_seasons_not_opened":"NOT_PROGRAMMATICALLY_VERIFIED"
        }
    }
    pass_values=[x for x in gate["checks"].values() if isinstance(x,bool)]
    gate["all_programmatically_checked_requirements_pass"]=all(pass_values)
    gate["promotion_requires_owner_review_and_unseen_season_integrity_confirmation"]=True
    summary={
        "version":"v0.3.21-historical-development",
        "status":"DEVELOPMENT_ONLY_NOT_VALIDATION",
        "scores":scores,
        "by_season":by_season,
        "seats":12,
        "decisions":228,
        "engine_decision_status":counts,
        "unconfirmed_edge_rate":rate,
        "engine_minus_baseline_pp":{
            p:{"mean":statistics.fmean(deltas[p]),
               "median":statistics.median(deltas[p]),
               "positive_seats":sum(x>0 for x in deltas[p]),
               "negative_seats":sum(x<0 for x in deltas[p])}
            for p in ("B1","B2","B3")
        },
        "gate_programmatically_passes":gate["all_programmatically_checked_requirements_pass"],
        "validation_season_2024_25_opened":False,
        "holdout_season_2025_26_opened":False
    }
    write(run/"results/summary.json",summary)
    write(run/"results/gate_report.json",gate)
    print(json.dumps(summary,indent=2),flush=True)
    print(json.dumps(gate,indent=2),flush=True)


if __name__=="__main__":
    main()
