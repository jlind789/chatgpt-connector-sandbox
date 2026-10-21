#!/usr/bin/env python3
"""Development-only 1024 -> 2048 shadow on v0.3.21 original unconfirmed decisions.

Never loads realized outcomes. Replays ALL source checkpoints exactly, then
continues the same runner to 2048 without changing the source draft trajectory.
"""
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
SCRIPTS = ROOT / "scripts"
PROTO_FILE = ROOT / "historical/protocol-v0.3.21-development-freeze.json"
SEASONS = ("2022-23", "2023-24")
TARGET_STATUS = "UNCONFIRMED_EDGE_MAX_BUDGET"
OUT_VERSION = "v0.3.22-dev-shadow-2048-seat/1"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    return sha(path.read_bytes())


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def source_bundle(z: zipfile.ZipFile):
    checks = json.loads(z.read("BUNDLE-CHECKSUMS.json"))
    if len(checks) != 55:
        raise RuntimeError(f"Expected 55 source bundle checksums, got {len(checks)}")
    for name, digest in checks.items():
        if sha(z.read(name)) != digest:
            raise RuntimeError(f"Source bundle failed SHA-256 verification: {name}")
    summary = json.loads(z.read("results/summary.json"))
    gate = json.loads(z.read("results/gate_report.json"))
    manifest = json.loads(z.read("manifest.json"))
    commits = json.loads(z.read("commitment_manifest.json"))
    if (summary.get("version") != "v0.3.21-historical-development"
            or summary.get("seats") != 12 or summary.get("decisions") != 228
            or summary["engine_decision_status"]["unconfirmed_edge"] != 35):
        raise RuntimeError("Not the authoritative v0.3.21 12-seat result bundle")
    if gate["checks"]["unconfirmed_rate_at_most_15pct"] is not False:
        raise RuntimeError("Unexpected source gate status")
    if (manifest["protocol_max_rollouts"] != 1024
            or manifest["files_sha256"]["historical\\protocol-v0.3.21-development-freeze.json"] != file_hash(PROTO_FILE)
            or manifest["files_sha256"]["scripts\\run_historical_v0320_seat.py"] != file_hash(SCRIPTS / "run_historical_v0320_seat.py")):
        raise RuntimeError("Protocol/runner does not match frozen source")
    for season in SEASONS:
        for seat in range(1, 7):
            name = f"{season}_seat{seat}.json"
            commitment = z.read("commitments/" + name)
            result = json.loads(z.read("seat_results/" + name))
            h = sha(commitment)
            if (h != commits["sha256"][name]
                    or h != result["source_commitment_sha256"]):
                raise RuntimeError(f"Source commitment mismatch: {name}")
    return summary, manifest


def prefix_rosters(world, trace, pick_index: int):
    rosters = {t: list(world["initial_rosters"][t]) for t in world["team_ids"]}
    if len(trace) != len(world["draft_order"]):
        raise RuntimeError("Source trace has wrong length")
    seen = set()
    for i, row in enumerate(trace[:pick_index]):
        if row["pick_index"] != i or row["team_id"] != world["draft_order"][i]:
            raise RuntimeError(f"Noncontiguous/invalid draft trace at pick {i}")
        pid = row["player_id"]
        if pid in seen:
            raise RuntimeError(f"Duplicate player in source trace {pid}")
        seen.add(pid)
        rosters[row["team_id"]].append(pid)
    return rosters


def compare_stage(got: dict, saved: dict):
    discrete = ("budget", "leader", "candidate_count", "separated",
                "stable_streak", "resolved_under_rule", "confirmed", "runner_up")
    for key in discrete:
        if got.get(key) != saved.get(key):
            raise RuntimeError(f"Stage {got['budget']} {key} changed: {got.get(key)} vs {saved.get(key)}")
    for key in ("worst_ci95_high_points", "runner_up_gap_points"):
        a, b = got.get(key), saved.get(key)
        if a is None or b is None:
            if a != b:
                raise RuntimeError(f"Stage {got['budget']} {key} null mismatch")
        elif abs(float(a) - float(b)) > 1e-10:
            raise RuntimeError(f"Stage {got['budget']} {key} numerical mismatch {a} vs {b}")


def extend(snapshot: dict, source: dict):
    from research.incremental_runner import ExactCalendarIncrementalRunner
    from research.convergence import paired_rows, race_survivors, resolution
    import run_historical_v0320_seat as base

    conv = base.PROTO["decision_engine"]["adaptive_convergence"]
    if conv["maximum_rollouts"] != 1024:
        raise RuntimeError("Frozen source protocol maximum is not 1024")
    d = json.loads(json.dumps(snapshot))
    d["methodology_config"]["max_lineup_plans"] = int(conv["lineup_scoring_safety_ceiling"])
    d["methodology_config"]["rollouts"] = 1
    runner = ExactCalendarIncrementalRunner(d, sweeps=1, maximum_storage_patterns=64)
    first = runner.run()
    if first["run_status"] != "ROSTER_ONLY":
        raise RuntimeError("Unsuccessful one-rollout runner")
    if first["recommended_player_id"] != source["one_rollout_leader"]:
        raise RuntimeError("Source one-rollout leader failed reproduction")
    if len(first["ranked_candidates"]) != source["full_pool_candidate_count"]:
        raise RuntimeError("Source full-pool count failed reproduction")
    stages = source["stages"]
    if (stages[0]["budget"] != 1
            or stages[0]["leader"] != first["recommended_player_id"]
            or stages[0]["candidate_count"] != len(first["ranked_candidates"])):
        raise RuntimeError("Stage 1 reproduction failed")

    remaining = sum(p["actor_id"] == "USER" for p in d["draft_state"]["remaining_picks"])
    active = base.broad_pool(first, runner, remaining - 1)
    prev = first["recommended_player_id"]
    previous_resolved = None
    stable = 0
    epsilon = float(conv["epsilon_utility_points"])
    c = conv["broad_pool"]
    budget = 2
    confirmed = False

    expected_by_budget = {int(st["budget"]): st for st in stages}
    if set(expected_by_budget) != {1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024}:
        raise RuntimeError("Original source stage coverage incomplete")
    while budget <= 2048:
        leader, rows = paired_rows(runner, active, budget)
        stable = stable + 1 if leader == prev else 0
        separated, worst = resolution(rows, epsilon)
        resolved = bool(budget >= conv["minimum_resolution_budget"] and separated and stable >= 1)
        confirmed = bool(resolved and previous_resolved == leader)
        gap = -rows[1]["mean_diff_vs_leader_points"] if len(rows) > 1 else None
        ru = rows[1]["player_id"] if len(rows) > 1 else None
        stage = {
            "budget": budget,
            "leader": leader,
            "candidate_count": len(active),
            "worst_ci95_high_points": worst,
            "separated": separated,
            "stable_streak": stable,
            "resolved_under_rule": resolved,
            "confirmed": confirmed,
            "runner_up": ru,
            "runner_up_gap_points": gap,
        }
        if budget <= 1024:
            compare_stage(stage, expected_by_budget[budget])
            if confirmed:
                raise RuntimeError("Source-unconfirmed case confirmed before the terminal stage")
        else:
            status = (
                "CONFIRMED_EDGE" if confirmed
                else "PRACTICAL_TIE_MAX_BUDGET" if gap is not None and gap <= epsilon
                else "UNCONFIRMED_EDGE_MAX_BUDGET"
            )
            return {
                "extended_status": status,
                "new_stage": stage,
                "source_1024_separated": source["stages"][-1]["separated"],
                "source_1024_gap": source["final_runner_up_gap_points"],
                "source_1024_leader": source["final_leader"],
                "leader_changed": leader != source["final_leader"],
            }

        previous_resolved = leader if resolved else None
        active = race_survivors(rows, epsilon, min_keep=c["min_keep"],
                                max_keep=c["max_keep"], force={leader, prev})
        prev = leader
        budget *= 2
    raise RuntimeError("2048 checkpoint missing")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--season", choices=SEASONS, required=True)
    parser.add_argument("--seat", type=int, choices=range(1, 7), required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(SCRIPTS))
    import run_historical_v0320_seat as base
    base.setup()
    base.PROTO = json.loads(PROTO_FILE.read_text(encoding="utf-8"))

    with zipfile.ZipFile(args.source_bundle) as z:
        source_bundle(z)
        name = f"{args.season}_seat{args.seat}.json"
        source_bytes = z.read("seat_results/" + name)
        source = json.loads(source_bytes)
        world = base.load_world(args.season)
        focus = world["team_ids"][args.seat - 1]
        if source["focus_team"] != focus:
            raise RuntimeError("Focus team mismatch")
        trace = source["results"]["DECISION_ENGINE"]["draft"]["trace"]
        decisions = [d for d in source["results"]["DECISION_ENGINE"]["draft"]["decisions"]
                     if d["status"] == TARGET_STATUS]
        output = args.output_dir / name
        identity = {
            "version": OUT_VERSION,
            "season": args.season,
            "seat": args.seat,
            "focus_team": focus,
            "source_seat_sha256": sha(source_bytes),
            "source_bundle_sha256": file_hash(args.source_bundle),
            "source_protocol_sha256": file_hash(PROTO_FILE),
            "shadow_runner_sha256": file_hash(Path(__file__)),
            "target_budget": 2048,
        }
        if output.exists():
            record = json.loads(output.read_text(encoding="utf-8"))
            if any(record.get(k) != v for k, v in identity.items()):
                raise RuntimeError("Refusing stale/incompatible shadow checkpoints")
        else:
            record = dict(identity, rows=[])
        seen = {int(r["pick_index"]) for r in record["rows"]}
        if len(seen) != len(record["rows"]):
            raise RuntimeError("Duplicate saved shadow decisions")

        for n, original in enumerate(decisions, 1):
            idx = int(original["pick_index"])
            if idx in seen:
                continue
            rosters = prefix_rosters(world, trace, idx)
            from research.drafts import snapshot_for
            snap = snapshot_for(world, rosters, idx, focus)
            start = time.perf_counter()
            result = extend(snap, original)
            record["rows"].append({
                "pick_index": idx,
                "round": int(original["round"]),
                "elapsed_seconds": time.perf_counter() - start,
                **result,
            })
            record["rows"].sort(key=lambda row: row["pick_index"])
            write_json(output, record)
            print(f"{args.season} seat{args.seat} original unconfirmed {n}/{len(decisions)} "
                  f"round{original['round']}: {result['extended_status']}", flush=True)

        if len(record["rows"]) != len(decisions):
            raise RuntimeError("Expected shadow-decision population missing")
        record["complete"] = True
        write_json(output, record)
        print(f"COMPLETE {name}: {len(decisions)} original unconfirmed cases", flush=True)


if __name__ == "__main__":
    main()
