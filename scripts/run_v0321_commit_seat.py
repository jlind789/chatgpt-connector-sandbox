#!/usr/bin/env python3
"""Phase A: commit v0.3.21 development drafts WITHOUT reading realized outcomes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_historical_v0320_seat as base
import run_historical_v0320_local_seat as checkpointed

PROTOCOL_FILE = ROOT / "historical/protocol-v0.3.21-development-freeze.json"
PROTO = json.loads(PROTOCOL_FILE.read_text(encoding="utf-8"))
assert PROTO["decision_engine"]["adaptive_convergence"]["maximum_rollouts"] == 1024
base.PROTO = PROTO


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def fingerprint(world: dict, focus: str) -> str:
    from decision_engine.validation import canonical_hash
    snapshot = {
        "protocol_sha256": sha(PROTOCOL_FILE),
        "base_runner_sha256": sha(ROOT / "scripts/run_historical_v0320_seat.py"),
        "checkpoint_helper_sha256": sha(ROOT / "scripts/run_historical_v0320_local_seat.py"),
        "commit_runner_sha256": sha(Path(__file__)),
        "focus_team": focus,
        "snapshot": world["snapshot"],
        "draft_order": world["draft_order"],
        "initial_rosters": world["initial_rosters"],
    }
    return canonical_hash(snapshot)


# The frozen helper is reused for its per-Decision Engine pick atomic checkpoints.
# Its fingerprint is upgraded to the new protocol and phase-A runner identity.
checkpointed.checkpoint_fingerprint = fingerprint


def strip_runtime_meta(draft: dict) -> dict:
    return {k: v for k, v in draft.items() if k != "elapsed_seconds"}


def main() -> None:
    base.setup()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("season", choices=PROTO["development_seasons"])
    ap.add_argument("seat", type=int, choices=range(1, 7))
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--checkpoint-dir", type=Path, required=True)
    args = ap.parse_args()

    season, seat = args.season, args.seat
    world = base.load_world(season)
    # The inherited checkpoint helper uses family only for its resume log.
    world.setdefault("family", season)
    focus = world["team_ids"][seat - 1]
    world_file = base.GEN / season / "world.json.gz"
    output = args.output_dir / f"{season}_seat{seat}.json"
    proof = {
        "protocol_sha256": sha(PROTOCOL_FILE),
        "base_runner_sha256": sha(ROOT / "scripts/run_historical_v0320_seat.py"),
        "checkpoint_helper_sha256": sha(ROOT / "scripts/run_historical_v0320_local_seat.py"),
        "commit_runner_sha256": sha(Path(__file__)),
        "world_gzip_sha256": sha(world_file),
        "runtime_zip_sha256": sha(ROOT / "Fantasy-Decision-Engine-v0.3.17-Development-Backtest-Runtime.zip"),
    }

    if output.exists():
        record = json.loads(output.read_text(encoding="utf-8"))
        if (record.get("proof") != proof or record.get("season") != season
                or record.get("seat") != seat or record.get("focus_team") != focus
                or set(record.get("drafts", {})) != {"B1","B2","B3","DECISION_ENGINE"}):
            raise RuntimeError("Existing commitment is invalid or stale; refusing to overwrite")
        for draft in record["drafts"].values():
            if draft.get("status") != "COMPLETE":
                raise RuntimeError("Stored commitment is incomplete")
        print(f"Existing frozen commitment verified: {season} seat{seat}", flush=True)
        return

    # IMPORTANT: This process never loads outcome_id_map, player_game_stats,
    # realized scores, or any other realized-outcome evaluation input.
    drafts = {}
    for policy in ("B1", "B2", "B3"):
        draft = base.baseline_draft(world, focus, policy)
        if draft["status"] != "COMPLETE":
            raise RuntimeError((season, seat, policy, draft["status"]))
        drafts[policy] = strip_runtime_meta(draft)
        print(f"{season} seat{seat} committed baseline {policy}", flush=True)

    draft = checkpointed.engine_draft_checkpointed(world, focus, args.checkpoint_dir)
    if draft["status"] != "COMPLETE":
        raise RuntimeError((season, seat, "DECISION_ENGINE", draft["status"]))
    drafts["DECISION_ENGINE"] = strip_runtime_meta(draft)

    decisions = draft["decisions"]
    allowed = {
        "CONFIRMED_EDGE", "PRACTICAL_TIE_MAX_BUDGET",
        "UNCONFIRMED_EDGE_MAX_BUDGET"
    }
    if len(decisions) != 19 or any(x["status"] not in allowed for x in decisions):
        raise RuntimeError("Invalid Engine decision coverage/status")

    record = {
        "version": "v0.3.21-development-draft-commitment/1",
        "phase": "A_OUTCOME_BLIND_DRAFT_COMMITMENT",
        "season": season,
        "seat": seat,
        "focus_team": focus,
        "proof": proof,
        "drafts": drafts,
    }
    atomic_json(output, record)
    print(f"OUTCOME_BLIND_COMMITMENT_WRITTEN {season} seat{seat}: {output}", flush=True)


if __name__ == "__main__":
    main()
