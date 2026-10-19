#!/usr/bin/env python3
"""Aggregate 53 development-only shadow decisions. This is NOT a promotion gate."""
from __future__ import annotations
import argparse, csv, json, collections, os
from pathlib import Path

SEASONS = ("2022-23", "2023-24")

def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", type=Path, required=True)
    args = ap.parse_args()
    run = args.run_dir.resolve()
    seats, identities = [], set()
    for season in SEASONS:
        for seat in range(1,7):
            path = run / "seat_results" / f"{season}_seat{seat}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            if not data.get("complete") or (data["season"], data["seat"]) != (season, seat):
                raise RuntimeError(f"Incomplete or incorrect seat: {path}")
            identities.add((data["protocol_sha256"], data["runner_sha256"], data["max_budget"]))
            seats.append(data)
    if len(identities) != 1:
        raise RuntimeError("Mixed protocol, runner, or budget")
    rows = []
    for data in seats:
        for r in data["rows"]:
            if r["source_status"] != "UNCONFIRMED_EDGE_MAX_BUDGET":
                raise RuntimeError("Non-target decision included")
            if not r["stages"] or r["stages"][0]["budget"] != 1024:
                raise RuntimeError("Missing checkpoint 1024")
            rows.append(dict(season=data["season"], seat=data["seat"], **r))
    if len(rows) != 53:
        raise RuntimeError(f"Expected 53 source unconfirmed decisions, got {len(rows)}")

    status = collections.Counter(r["terminal"] for r in rows)
    baselines = collections.Counter(r["source_final_separated"] for r in rows)
    strata = {
        str(g): dict(collections.Counter(r["terminal"] for r in rows if r["source_final_separated"] == g))
        for g in (False, True)
    }
    confirmed = int(status["CONFIRMED_EDGE"])
    ties = int(status["PRACTICAL_TIE_MAX_BUDGET"])
    unconfirmed = int(status["UNCONFIRMED_EDGE_MAX_BUDGET"])
    summary = {
        "version": "v0.3.21-shadow-extension/1",
        "status": "DEVELOPMENT_DIAGNOSTIC_ONLY_NOT_GATE_OR_NEW_DRAFT_RESULT",
        "seasons": list(SEASONS),
        "target_budget": next(iter(identities))[2],
        "total_originally_unconfirmed": len(rows),
        "source_512_separated": baselines[True],
        "source_512_not_separated": baselines[False],
        "extension_status_counts": dict(status),
        "extension_status_by_source_512_separation": strata,
        "leader_changes_since_512": sum(r["leader_changed_since_512"] for r in rows),
        "remaining_unconfirmed_with_fixed_source_draft": unconfirmed,
        "minimum_required_reduction_vs_v0320": 19,
        "shadow_reduction_of_unconfirmed_count": confirmed + ties,
        "meets_19_target_in_shadow_only": confirmed + ties >= 19,
        "limitations": [
            "Frozen v0.3.20 draft states only; not a v0.3.21 full-draft result.",
            "A different 1024-rollout decision may change subsequent draft states.",
            "The prior 80 practical-tie decisions have not been reassessed.",
            "The old 15% gate remains frozen; shadow replay cannot satisfy it.",
            "2024-25 validation and 2025-26 holdout remain unopened."
        ]
    }
    atomic(run / "summary.json", summary)
    fields = [
        "season", "seat", "round", "pick_index", "source_final_separated",
        "source_final_gap_points", "source_leader", "final_leader",
        "final_gap_points", "terminal", "confirmed",
        "leader_changed_since_512", "elapsed_seconds"
    ]
    with (run / "decision_comparison.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in sorted(rows, key=lambda q: (q["season"], q["seat"], q["round"])):
            w.writerow({k:r.get(k) for k in fields})
    print(json.dumps(summary, indent=2), flush=True)

if __name__ == "__main__":
    main()
