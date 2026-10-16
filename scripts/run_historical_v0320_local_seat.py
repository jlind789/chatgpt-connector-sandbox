#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import run_historical_v0320_seat as base

ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def checkpoint_fingerprint(world, focus: str) -> str:
    from decision_engine.validation import canonical_hash

    payload = {
        "protocol": base.PROTO,
        "snapshot": world["snapshot"],
        "draft_order": world["draft_order"],
        "initial_rosters": world["initial_rosters"],
        "focus_team": focus,
        "base_runner_sha256": sha256_file(ROOT / "scripts/run_historical_v0320_seat.py"),
        "protocol_sha256": sha256_file(ROOT / "historical/protocol-v0.3.20-development-freeze.json"),
    }
    return canonical_hash(payload)


def latest_checkpoint(checkpoint_dir: Path):
    files = sorted(checkpoint_dir.glob("decision-*.json"))
    if not files:
        return None
    rows = []
    for p in files:
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            rows.append((int(obj["next_index"]), p, obj))
        except Exception as exc:
            raise RuntimeError(f"Invalid checkpoint {p}: {exc}") from exc
    rows.sort(key=lambda x: x[0])
    return rows[-1]


def validate_checkpoint(world, focus: str, fingerprint: str, obj) -> None:
    if obj.get("checkpoint_version") != "v0.3.20-local-decision-checkpoint/1":
        raise RuntimeError("Checkpoint version mismatch.")
    if obj.get("fingerprint") != fingerprint:
        raise RuntimeError("Checkpoint fingerprint mismatch; stale checkpoint reuse is forbidden.")
    if obj.get("focus_team") != focus:
        raise RuntimeError("Checkpoint focus team mismatch.")
    next_index = int(obj["next_index"])
    trace = obj["trace"]
    if len(trace) != next_index:
        raise RuntimeError("Checkpoint trace length does not match next_index.")
    for i, row in enumerate(trace):
        if int(row["pick_index"]) != i:
            raise RuntimeError(f"Checkpoint trace is non-contiguous at index {i}.")
        if row["team_id"] != world["draft_order"][i]:
            raise RuntimeError(f"Checkpoint team mismatch at draft index {i}.")
    expected = {t: list(world["initial_rosters"][t]) for t in world["team_ids"]}
    for row in trace:
        expected[row["team_id"]].append(row["player_id"])
    saved = {t: sorted(v) for t, v in obj["rosters"].items()}
    rebuilt = {t: sorted(v) for t, v in expected.items()}
    if saved != rebuilt:
        raise RuntimeError("Checkpoint roster state does not match its draft trace.")


def engine_draft_checkpointed(world, focus: str, checkpoint_dir: Path | None):
    from research.baselines import choose
    from research.drafts import snapshot_for

    rosters = {t: list(world["initial_rosters"][t]) for t in world["team_ids"]}
    solver, iv, anchor = base.draft_primitives(world)
    trace = []
    decisions = []
    start_index = 0
    fingerprint = checkpoint_fingerprint(world, focus)

    if checkpoint_dir is not None:
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        found = latest_checkpoint(checkpoint_dir)
        if found is not None:
            _, path, obj = found
            validate_checkpoint(world, focus, fingerprint, obj)
            rosters = {t: list(v) for t, v in obj["rosters"].items()}
            trace = list(obj["trace"])
            decisions = list(obj["decisions"])
            start_index = int(obj["next_index"])
            print(f"RESUME {world['family']} {focus}: {path.name} -> draft index {start_index}", flush=True)

    taken = {p for ids in rosters.values() for p in ids}
    pool = {p["player_id"] for p in world["snapshot"]["league_players"] if p["rosterable"]} - taken
    started = time.perf_counter()

    for index in range(start_index, len(world["draft_order"])):
        team = world["draft_order"][index]
        later = world["draft_order"][index + 1 :].count(team)
        if team == focus:
            snap = snapshot_for(world, rosters, index, focus)
            chosen, evidence = base.adaptive_pick(snap)
            decisions.append(
                {
                    "pick_index": index,
                    "round": index // len(world["team_ids"]) + 1,
                    "player_id": chosen,
                    **evidence,
                }
            )
            policy = "DECISION_ENGINE"
        else:
            policy = ("B1", "B3", "B2")[index % 3]
            chosen = choose(policy, rosters[team], pool, later, solver, iv, anchor)

        if chosen is None:
            return {
                "status": "NO_FEASIBLE_PICK",
                "trace": trace,
                "rosters": rosters,
                "decisions": decisions,
            }
        if chosen not in pool:
            raise AssertionError(("unavailable pick", index, chosen))
        reason = solver.candidate_reason(rosters[team], chosen, pool, later)
        if reason:
            raise AssertionError(("illegal pick", reason, index, chosen))

        pool.remove(chosen)
        rosters[team].append(chosen)
        trace.append(
            {
                "pick_index": index,
                "team_id": team,
                "policy": policy,
                "player_id": chosen,
            }
        )

        if team == focus and checkpoint_dir is not None:
            checkpoint = {
                "checkpoint_version": "v0.3.20-local-decision-checkpoint/1",
                "fingerprint": fingerprint,
                "focus_team": focus,
                "next_index": index + 1,
                "decision_count": len(decisions),
                "rosters": {t: list(v) for t, v in rosters.items()},
                "trace": trace,
                "decisions": decisions,
            }
            atomic_json(checkpoint_dir / f"decision-{len(decisions):02d}.json", checkpoint)

    for t, ids in rosters.items():
        if not solver.best(ids, terminal=True):
            raise AssertionError("invalid completed roster " + t)

    return {
        "status": "COMPLETE",
        "trace": trace,
        "rosters": {t: sorted(v) for t, v in rosters.items()},
        "decisions": decisions,
        "elapsed_seconds": time.perf_counter() - started,
    }


def main():
    base.setup()

    ap = argparse.ArgumentParser()
    ap.add_argument("season", choices=base.PROTO["development_seasons"])
    ap.add_argument("seat", type=int, choices=range(1, 7))
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--checkpoint-dir", type=Path)
    args = ap.parse_args()

    season = args.season
    seat = args.seat
    world = base.load_world(season)
    data = world["snapshot"]
    focus = world["team_ids"][seat - 1]
    outmap = json.loads((base.GEN / season / "outcome_id_map.json").read_text())
    outcomes = base.load_outcomes(season)

    results = {}
    for policy in ("B1", "B2", "B3"):
        t = time.perf_counter()
        dr = base.baseline_draft(world, focus, policy)
        if dr["status"] != "COMPLETE":
            raise RuntimeError((season, seat, policy, dr["status"]))
        outcome = base.realized_focus_score(data, dr["rosters"], focus, outmap, outcomes)
        results[policy] = {
            "draft": dr,
            "outcome": outcome,
            "elapsed_seconds": time.perf_counter() - t,
        }
        print(season, "seat", seat, policy, "score", outcome["mean_weekly_category_share"], flush=True)

    t = time.perf_counter()
    dr = engine_draft_checkpointed(world, focus, args.checkpoint_dir)
    if dr["status"] != "COMPLETE":
        raise RuntimeError((season, seat, "ENGINE", dr["status"]))

    outcome = base.realized_focus_score(data, dr["rosters"], focus, outmap, outcomes)
    decisions = dr["decisions"]
    confirmed = [x for x in decisions if x["status"] == "CONFIRMED_EDGE"]
    ties = [x for x in decisions if x["status"] == "PRACTICAL_TIE_MAX_BUDGET"]
    uncertain = [x for x in decisions if x["status"] == "UNCONFIRMED_EDGE_MAX_BUDGET"]
    if len(confirmed) + len(ties) + len(uncertain) != len(decisions):
        raise AssertionError("Unexpected v0.3.20 decision status.")

    contrib = outcome.pop("started_contributions")

    def cohort(ids):
        rows = [contrib.get(x["player_id"], {}) for x in ids]
        keys = [
            "started_days",
            "goals",
            "assists",
            "faceoff_wins",
            "shots",
            "hits",
            "blocks",
            "defense_points",
            "wins",
            "saves",
            "shutouts",
            "shots_against",
        ]
        return {
            "decision_count": len(ids),
            "realized_started_contribution": {
                k: sum(float(r.get(k, 0)) for r in rows) for k in keys
            },
        }

    engine = {
        "draft": dr,
        "outcome": outcome,
        "elapsed_seconds": time.perf_counter() - t,
        "confidence_reporting": {
            "ALL": cohort(decisions),
            "CONFIRMED_EDGE": cohort(confirmed),
            "PRACTICAL_TIE": cohort(ties),
            "UNCONFIRMED_EDGE": cohort(uncertain),
            "confirmed_rate": len(confirmed) / len(decisions) if decisions else 0.0,
            "practical_tie_rate": len(ties) / len(decisions) if decisions else 0.0,
            "unconfirmed_edge_rate": len(uncertain) / len(decisions) if decisions else 0.0,
            "note": "Cohort contribution is descriptive started production, not a causal per-pick value estimate.",
        },
    }
    results["DECISION_ENGINE"] = engine

    record = {
        "version": "v0.3.20-development-seat",
        "season": season,
        "seat": seat,
        "focus_team": focus,
        "protocol": base.PROTO,
        "results": results,
        "summary": {
            "scores": {
                p: results[p]["outcome"]["mean_weekly_category_share"] for p in results
            },
            "engine_minus_pp": {
                p: 100
                * (
                    results["DECISION_ENGINE"]["outcome"]["mean_weekly_category_share"]
                    - results[p]["outcome"]["mean_weekly_category_share"]
                )
                for p in ("B1", "B2", "B3")
            },
            "engine_confirmed_decisions": len(confirmed),
            "engine_practical_tie_decisions": len(ties),
            "engine_unconfirmed_edge_decisions": len(uncertain),
        },
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / f"{season}_seat{seat}.json"
    atomic_json(path, record)
    print(json.dumps(record["summary"], indent=2), flush=True)


if __name__ == "__main__":
    main()
