#!/usr/bin/env python3
"""Development-only v0.3.20 -> 1024 shadow replay. Never loads realized outcomes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_KIND = "v0321-shadow-of-v0320-unconfirmed/1"
TARGET_STATUS = "UNCONFIRMED_EDGE_MAX_BUDGET"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_bytes())


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def check_bundle(z: zipfile.ZipFile) -> None:
    checks = json.loads(z.read("BUNDLE-CHECKSUMS.json"))
    for name, expected in checks.items():
        if sha(z.read(name)) != expected:
            raise RuntimeError(f"Bundle checksum mismatch: {name}")
    if len(checks) < 20:
        raise RuntimeError("Suspiciously incomplete result bundle")


def replay_rosters(world: dict, trace: list, idx: int) -> dict:
    rosters = {t: list(world["initial_rosters"][t]) for t in world["team_ids"]}
    if len(trace) != len(world["draft_order"]):
        raise RuntimeError("Draft trace length mismatch")
    for k in range(idx):
        row = trace[k]
        if int(row["pick_index"]) != k or row["team_id"] != world["draft_order"][k]:
            raise RuntimeError(f"Invalid trace at pick {k}")
        rosters[row["team_id"]].append(row["player_id"])
    return rosters


def stage_compare(got: dict, expected: dict) -> None:
    exact = ("leader", "candidate_count", "separated", "stable_streak", "resolved_under_rule", "confirmed")
    for key in exact:
        if got.get(key) != expected.get(key):
            raise RuntimeError(
                f"Source reproduction mismatch at {got['budget']} for {key}: "
                f"{got.get(key)} != {expected.get(key)}"
            )
    for key in ("worst_ci95_high_points", "runner_up_gap_points"):
        a, b = got.get(key), expected.get(key)
        if a is None or b is None:
            if a != b:
                raise RuntimeError(f"Source reproduction mismatch for {key}: {a} != {b}")
        elif abs(float(a) - float(b)) > 1e-10:
            raise RuntimeError(f"Source reproduction mismatch for {key}: {a} != {b}")
    if got.get("runner_up") != expected.get("runner_up"):
        raise RuntimeError("Source runner-up identity mismatch")


def replay_extend(snapshot: dict, original: dict, target: int):
    from research.incremental_runner import ExactCalendarIncrementalRunner
    from research.convergence import paired_rows, race_survivors, resolution
    import run_historical_v0320_seat as base

    d = json.loads(json.dumps(snapshot))
    cfg = base.PROTO["decision_engine"]["adaptive_convergence"]
    d["methodology_config"]["max_lineup_plans"] = int(cfg["lineup_scoring_safety_ceiling"])
    remaining = sum(p["actor_id"] == "USER" for p in d["draft_state"]["remaining_picks"])
    d["methodology_config"]["rollouts"] = 1
    runner = ExactCalendarIncrementalRunner(d, sweeps=1, maximum_storage_patterns=64)
    one = runner.run()
    if one["run_status"] != "ROSTER_ONLY":
        raise RuntimeError("Initial exact-calendar runner unsuccessful")
    if one["recommended_player_id"] != original["one_rollout_leader"]:
        raise RuntimeError("Source one-rollout leader mismatch")
    if len(one["ranked_candidates"]) != original["full_pool_candidate_count"]:
        raise RuntimeError("Source full-pool count mismatch")

    active = base.broad_pool(one, runner, remaining - 1)
    prev = one["recommended_player_id"]
    stable = 0
    previous_resolved_leader = None
    source_stages = {int(x["budget"]): x for x in original["stages"]}
    if source_stages[1]["leader"] != prev:
        raise RuntimeError("Source stage-one leader mismatch")

    stages = []
    budget = 2
    while budget <= target:
        leader, rows = paired_rows(runner, active, budget)
        stable = stable + 1 if leader == prev else 0
        separated, worst = resolution(rows, float(cfg["epsilon_utility_points"]))
        resolved = bool(
            budget >= int(cfg["minimum_resolution_budget"]) and separated and stable >= 1
        )
        confirmed = bool(resolved and previous_resolved_leader == leader)
        ru = rows[1]["player_id"] if len(rows) > 1 else None
        gap = -rows[1]["mean_diff_vs_leader_points"] if len(rows) > 1 else None
        stage = dict(
            budget=budget,
            leader=leader,
            candidate_count=len(active),
            worst_ci95_high_points=worst,
            separated=separated,
            stable_streak=stable,
            resolved_under_rule=resolved,
            confirmed=confirmed,
            runner_up=ru,
            runner_up_gap_points=gap,
        )
        if budget <= 512:
            stage_compare(stage, source_stages[budget])
        else:
            stages.append(stage)
        if budget == target:
            break
        if budget <= 512 and confirmed:
            raise RuntimeError("Source-unconfirmed decision unexpectedly confirmed in replay")
        previous_resolved_leader = leader if resolved else None
        active = race_survivors(
            rows,
            float(cfg["epsilon_utility_points"]),
            min_keep=cfg["broad_pool"]["min_keep"],
            max_keep=cfg["broad_pool"]["max_keep"],
            force={leader, prev},
        )
        prev = leader
        budget *= 2

    if budget != target:
        raise RuntimeError("Target budget was not reached")
    eps = float(cfg["epsilon_utility_points"])
    status = (
        "CONFIRMED_EDGE" if confirmed
        else "PRACTICAL_TIE_MAX_BUDGET" if gap is not None and gap <= eps
        else TARGET_STATUS
    )
    return dict(
        terminal=status,
        stages=stages,
        leader_changed_since_512=leader != original["final_leader"],
        confirmed=confirmed,
        final_leader=leader,
        final_gap_points=gap,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-bundle", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--season", required=True, choices=("2022-23", "2023-24"))
    ap.add_argument("--seat", required=True, type=int, choices=range(1, 7))
    ap.add_argument("--max-budget", type=int, choices=(1024, 2048), default=1024)
    args = ap.parse_args()

    scripts_dir = ROOT / "scripts"
    sys.path.insert(0, str(scripts_dir))
    import run_historical_v0320_seat as base
    base.setup()
    output = args.out_dir.resolve() / f"{args.season}_seat{args.seat}.json"
    with zipfile.ZipFile(args.source_bundle) as z:
        check_bundle(z)
        manifest = json.loads(z.read("manifest.json"))
        source_bytes = z.read(f"seat_results/{args.season}_seat{args.seat}.json")
        source = json.loads(source_bytes)
        if source["version"] != "v0.3.20-development-seat":
            raise RuntimeError("Unexpected source experiment")
        if file_sha(scripts_dir / "run_historical_v0320_seat.py") != manifest["base_runner_sha256"]:
            raise RuntimeError("Base runner SHA differs from source manifest")
        if file_sha(ROOT / "historical/protocol-v0.3.20-development-freeze.json") != manifest["protocol_sha256"]:
            raise RuntimeError("Protocol SHA differs from source manifest")

        world = base.load_world(args.season)
        focus = world["team_ids"][args.seat - 1]
        if source["focus_team"] != focus:
            raise RuntimeError("Focus identity mismatch")
        trace = source["results"]["DECISION_ENGINE"]["draft"]["trace"]
        decisions = [
            x for x in source["results"]["DECISION_ENGINE"]["draft"]["decisions"]
            if x["status"] == TARGET_STATUS
        ]
        identity = dict(
            version=SOURCE_KIND,
            source_sha256=sha(source_bytes),
            runner_sha256=file_sha(scripts_dir / "run_historical_v0320_seat.py"),
            protocol_sha256=manifest["protocol_sha256"],
            max_budget=args.max_budget,
            season=args.season,
            seat=args.seat,
            focus_team=focus,
        )
        if output.exists():
            data = json.loads(output.read_text())
            for key, value in identity.items():
                if data.get(key) != value:
                    raise RuntimeError(f"Stale local shadow output (mismatch: {key})")
        else:
            data = dict(identity, rows=[])
        completed = {int(r["pick_index"]) for r in data["rows"]}
        for n, orig in enumerate(decisions, 1):
            idx = int(orig["pick_index"])
            if idx in completed:
                continue
            rosters = replay_rosters(world, trace, idx)
            from research.drafts import snapshot_for
            snap = snapshot_for(world, rosters, idx, focus)
            start = time.perf_counter()
            res = replay_extend(snap, orig, args.max_budget)
            row = dict(
                pick_index=idx,
                round=int(orig["round"]),
                source_status=orig["status"],
                source_leader=orig["final_leader"],
                source_final_gap_points=orig["final_runner_up_gap_points"],
                source_final_separated=orig["stages"][-1]["separated"],
                elapsed_seconds=time.perf_counter() - start,
                **res,
            )
            data["rows"].append(row)
            data["rows"].sort(key=lambda r: r["pick_index"])
            atomic_json(output, data)
            print(
                f"{args.season} seat{args.seat} target {n}/{len(decisions)} "
                f"round{row['round']}: {row['terminal']} (confirmed={row['confirmed']})",
                flush=True,
            )
    if len(data["rows"]) != len(decisions):
        raise RuntimeError("Shadow replay missing decisions")
    data["complete"] = True
    atomic_json(output, data)
    print(f"Completed shadow of {args.season} seat{args.seat}: {len(decisions)} decisions", flush=True)


if __name__ == "__main__":
    main()
