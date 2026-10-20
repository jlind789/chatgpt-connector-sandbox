#!/usr/bin/env python3
"""Certify that restarting after 18 completed v0.3.21 Engine picks is byte-identical."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RUNNER=ROOT/"scripts/run_v0321_commit_seat.py"

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def atomic(p, obj):
    p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_name(p.name+".tmp")
    t.write_text(json.dumps(obj,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(t,p)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir",type=Path,required=True)
    args=ap.parse_args()
    run=args.run_dir.resolve()
    season,seat="2022-23",1
    name=f"{season}_seat{seat}.json"
    source=run/"commitments"/name
    cp=run/"checkpoints"/f"{season}_seat{seat}"/"decision-18.json"
    if not source.is_file() or not cp.is_file():
        raise RuntimeError("Full Phase A seat-1 source and penultimate decision checkpoint required")
    artifact=run/"restart_certification"
    marker=artifact/"PASS.json"
    if marker.exists():
        report=json.loads(marker.read_text(encoding="utf-8"))
        if (report.get("commitment_sha256")==sha(source)
                and report.get("source_checkpoint_sha256")==sha(cp)
                and report.get("runner_sha256")==sha(RUNNER)
                and report.get("equivalent") is True):
            print("Restart equivalence remains valid for frozen sources",flush=True)
            return
        raise RuntimeError("Previous restart certification stale")

    dst=artifact/"checkpoint"/"decision-18.json"
    dst.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(cp,dst)
    output=artifact/"commitments"
    env=os.environ.copy()
    cmd=[sys.executable,str(RUNNER),season,str(seat),
         "--output-dir",str(output),
         "--checkpoint-dir",str(dst.parent)]
    logfile=artifact/"resumed-run.log"
    with logfile.open("w",encoding="utf-8") as log:
        result=subprocess.run(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"Resumed seat-1 execution failed ({result.returncode}); read {logfile}")
    resumed=output/name
    if not resumed.is_file():
        raise RuntimeError("Missing recomputed resumed output")
    if source.read_bytes()!=resumed.read_bytes():
        raise RuntimeError("RESTART SEMANTIC EQUIVALENCE FAILURE; Phase B is blocked")
    report={
        "version":"v0321-local-restart-equivalence/1",
        "equivalent":True,
        "comparison":"BYTE_IDENTICAL_OUTCOME_BLIND_COMMITMENT",
        "season":season,
        "seat":seat,
        "resume_after_completed_engine_decisions":18,
        "commitment_sha256":sha(source),
        "source_checkpoint_sha256":sha(cp),
        "runner_sha256":sha(RUNNER),
        "resumed_sha256":sha(resumed),
    }
    atomic(marker,report)
    print("RESTART EQUIVALENCE PASSED — original and resumed draft commitments byte-identical",flush=True)

if __name__=="__main__":
    main()
