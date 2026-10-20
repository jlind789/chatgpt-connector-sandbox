#!/usr/bin/env python3
"""Phase B: score immutable Phase A commitments using realized outcomes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_historical_v0320_seat as base

PROTOCOL_FILE = ROOT / "historical/protocol-v0.3.21-development-freeze.json"
PROTO = json.loads(PROTOCOL_FILE.read_text(encoding="utf-8"))
base.PROTO = PROTO


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp,path)


def main():
    base.setup()
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("season",choices=PROTO["development_seasons"])
    ap.add_argument("seat",type=int,choices=range(1,7))
    ap.add_argument("--commit-dir",type=Path,required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    args=ap.parse_args()
    season,seat=args.season,args.seat
    src=args.commit_dir/f"{season}_seat{seat}.json"
    if not src.is_file():
        raise RuntimeError("Missing Phase A draft commitment; refusing to score")

    commit_sha=sha(src)
    commitment=json.loads(src.read_text(encoding="utf-8"))
    if (commitment.get("version")!="v0.3.21-development-draft-commitment/1"
            or commitment.get("phase")!="A_OUTCOME_BLIND_DRAFT_COMMITMENT"
            or commitment["season"]!=season or commitment["seat"]!=seat):
        raise RuntimeError("Malformed/mismatched Phase A commitment")
    proof=commitment["proof"]
    required={
        "protocol_sha256":sha(PROTOCOL_FILE),
        "base_runner_sha256":sha(ROOT/"scripts/run_historical_v0320_seat.py"),
        "checkpoint_helper_sha256":sha(ROOT/"scripts/run_historical_v0320_local_seat.py"),
        "commit_runner_sha256":sha(ROOT/"scripts/run_v0321_commit_seat.py"),
        "world_gzip_sha256":sha(base.GEN/season/"world.json.gz"),
        "runtime_zip_sha256":sha(ROOT/"Fantasy-Decision-Engine-v0.3.17-Development-Backtest-Runtime.zip")
    }
    if proof!=required:
        raise RuntimeError("Phase A provenance mismatches local frozen source")

    world=base.load_world(season)
    data=world["snapshot"]
    focus=world["team_ids"][seat-1]
    if commitment["focus_team"]!=focus:
        raise RuntimeError("Focus identity mismatch")
    if set(commitment["drafts"])!={"B1","B2","B3","DECISION_ENGINE"}:
        raise RuntimeError("Incomplete draft policy coverage")
    for policy,draft in commitment["drafts"].items():
        if draft.get("status")!="COMPLETE":
            raise RuntimeError("Incomplete draft commitment")
        if len(draft["trace"])!=len(world["draft_order"]):
            raise RuntimeError("Incomplete draft trace")

    output=args.output_dir/f"{season}_seat{seat}.json"
    if output.exists():
        old=json.loads(output.read_text(encoding="utf-8"))
        if (old.get("source_commitment_sha256")==commit_sha
                and old.get("version")=="v0.3.21-development-seat"):
            print(f"Previously evaluated immutable commitment: {season} seat{seat}")
            return
        raise RuntimeError("Existing evaluation stale or mismatched")

    # This is the first point at which realized outcome data is loaded.
    outmap=json.loads((base.GEN/season/"outcome_id_map.json").read_text(encoding="utf-8"))
    outcomes=base.load_outcomes(season)
    results={}
    for policy,draft in commitment["drafts"].items():
        outcome=base.realized_focus_score(data,draft["rosters"],focus,outmap,outcomes)
        results[policy]={"draft":draft,"outcome":outcome}
        print(f"{season} seat{seat} {policy} score {outcome['mean_weekly_category_share']}",flush=True)

    engine=results["DECISION_ENGINE"]
    decisions=engine["draft"]["decisions"]
    confirmed=[d for d in decisions if d["status"]=="CONFIRMED_EDGE"]
    ties=[d for d in decisions if d["status"]=="PRACTICAL_TIE_MAX_BUDGET"]
    uncertain=[d for d in decisions if d["status"]=="UNCONFIRMED_EDGE_MAX_BUDGET"]
    if len(confirmed)+len(ties)+len(uncertain)!=len(decisions) or len(decisions)!=19:
        raise RuntimeError("Incorrect status accounting")

    contrib=engine["outcome"].pop("started_contributions")
    keys=["started_days","goals","assists","faceoff_wins","shots","hits",
          "blocks","defense_points","wins","saves","shutouts","shots_against"]
    def cohort(group):
        rr=[contrib.get(d["player_id"],{}) for d in group]
        return dict(decision_count=len(group),
                    realized_started_contribution={k:sum(float(x.get(k,0)) for x in rr)
                                                   for k in keys})

    engine["confidence_reporting"]={
        "ALL":cohort(decisions),
        "CONFIRMED_EDGE":cohort(confirmed),
        "PRACTICAL_TIE":cohort(ties),
        "UNCONFIRMED_EDGE":cohort(uncertain),
        "confirmed_rate":len(confirmed)/len(decisions),
        "practical_tie_rate":len(ties)/len(decisions),
        "unconfirmed_edge_rate":len(uncertain)/len(decisions),
        "note":"Cohort started-production is descriptive, not causal per-pick value"
    }

    scores={p:results[p]["outcome"]["mean_weekly_category_share"]
            for p in ("B1","B2","B3","DECISION_ENGINE")}
    record={
        "version":"v0.3.21-development-seat",
        "season":season,"seat":seat,"focus_team":focus,
        "protocol":PROTO,
        "source_commitment_sha256":commit_sha,
        "draft_commitment_precedes_outcome_load":True,
        "results":results,
        "summary":{
            "scores":scores,
            "engine_minus_pp":{p:100*(scores["DECISION_ENGINE"]-scores[p])
                               for p in ("B1","B2","B3")},
            "engine_confirmed_decisions":len(confirmed),
            "engine_practical_tie_decisions":len(ties),
            "engine_unconfirmed_edge_decisions":len(uncertain),
        }
    }
    atomic_json(output,record)
    print("OUTCOME_EVALUATION_WRITTEN",output,flush=True)


if __name__=="__main__":
    main()
