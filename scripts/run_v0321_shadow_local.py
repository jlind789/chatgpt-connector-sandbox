#!/usr/bin/env python3
"""Parallel restart-safe local research for the v0.3.21 development-only shadow."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
import sys
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts/diagnose_v0321_shadow.py"
AGG = ROOT / "scripts/aggregate_v0321_shadow.py"
BASE = ROOT / "scripts/run_historical_v0320_seat.py"
PROTO = ROOT / "historical/protocol-v0.3.20-development-freeze.json"
RUNTIME = ROOT / "Fantasy-Decision-Engine-v0.3.17-Development-Backtest-Runtime.zip"

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)

def now():
    return datetime.now(timezone.utc).isoformat()

def valid_result(path, season, seat, source_sha, max_budget):
    if not path.exists():
        return False
    d=json.loads(path.read_text(encoding="utf-8"))
    return bool(
        d.get("complete") and d["season"]==season and int(d["seat"])==seat
        and d["source_sha256"]==source_sha and d["max_budget"]==max_budget
    )

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-bundle", required=True, type=Path)
    ap.add_argument("--run-dir", type=Path, default=ROOT/"historical/local_runs/v0321-shadow-1024")
    ap.add_argument("--workers", type=int, default=max(1,min(4,(os.cpu_count() or 2)//2)))
    ap.add_argument("--max-budget", type=int, choices=(1024,2048), default=1024)
    args=ap.parse_args()
    if args.workers < 1:
        raise SystemExit("workers must be >= 1")

    with zipfile.ZipFile(args.source_bundle) as z:
        source_sha={}
        for season in ("2022-23","2023-24"):
            for seat in range(1,7):
                name=f"seat_results/{season}_seat{seat}.json"
                source_sha[(season,seat)]=hashlib.sha256(z.read(name)).hexdigest()

    run=args.run_dir.resolve()
    manifest=dict(
        version="v0321-shadow-local-run/1",
        source_bundle_sha256=sha(args.source_bundle),
        runner_sha256=sha(SRC),
        aggregator_sha256=sha(AGG),
        base_runner_sha256=sha(BASE),
        protocol_sha256=sha(PROTO),
        runtime_zip_sha256=sha(RUNTIME),
        max_budget=args.max_budget,
        workers=args.workers,
        created_at=now()
    )
    manifest_path=run/"manifest.json"
    if manifest_path.exists():
        original=json.loads(manifest_path.read_text(encoding="utf-8"))
        for k in ("source_bundle_sha256","runner_sha256","aggregator_sha256",
                  "base_runner_sha256","protocol_sha256","runtime_zip_sha256","max_budget"):
            if original.get(k)!=manifest.get(k):
                raise SystemExit(f"Provenance mismatch: {k}; use a new --run-dir")
        manifest["created_at"]=original["created_at"]
    atomic(manifest_path,manifest)

    env=os.environ.copy()
    for k in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS",
              "NUMEXPR_NUM_THREADS","VECLIB_MAXIMUM_THREADS"):
        env[k]="1"

    mutex=threading.Lock()
    status={f"{s}_seat{i}":{"state":"PENDING"} for s,i in source_sha}
    def update(name, state, **details):
        with mutex:
            status[name].update(state=state, **details)
            atomic(run/"status.json", {"updated_at":now(),"jobs":status})

    for season,seat in source_sha:
        out=run/"seat_results"/f"{season}_seat{seat}.json"
        if valid_result(out,season,seat,source_sha[(season,seat)],args.max_budget):
            status[f"{season}_seat{seat}"]["state"]="COMPLETE"
    atomic(run/"status.json",{"updated_at":now(),"jobs":status})

    def job(season,seat):
        name=f"{season}_seat{seat}"
        out=run/"seat_results"/f"{name}.json"
        if valid_result(out,season,seat,source_sha[(season,seat)],args.max_budget):
            return name,0
        update(name,"RUNNING",started_at=now())
        cmd=[
            sys.executable,str(SRC),
            "--source-bundle",str(args.source_bundle.resolve()),
            "--out-dir",str(run/"seat_results"),
            "--season",season,"--seat",str(seat),
            "--max-budget",str(args.max_budget)
        ]
        log=run/"logs"/f"{name}.log"
        log.parent.mkdir(parents=True,exist_ok=True)
        with log.open("a",encoding="utf-8") as f:
            f.write(f"\n=== {now()} ===\n")
            f.flush()
            proc=subprocess.run(cmd,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT)
        passed=proc.returncode==0 and valid_result(out,season,seat,source_sha[(season,seat)],args.max_budget)
        update(name,"COMPLETE" if passed else "FAILED",finished_at=now(),return_code=proc.returncode)
        return name,0 if passed else 1

    bad=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(job,s,i):f"{s}_seat{i}" for s,i in source_sha}
        for fut in concurrent.futures.as_completed(futures):
            try:
                name,ret=fut.result()
                if ret:bad.append(name)
            except Exception as exc:
                bad.append(futures[fut])
                update(futures[fut],"FAILED",error=str(exc))

    if bad:
        raise SystemExit("Failed seats: "+", ".join(sorted(bad)))
    proc=subprocess.run([sys.executable,str(AGG),"--run-dir",str(run)],cwd=ROOT,env=env)
    if proc.returncode:
        raise SystemExit(proc.returncode)
    atomic(run/"COMPLETE.json",{
        "completed_at":now(),"run_type":"DEVELOPMENT_SHADOW_ONLY","seats":12
    })
    print("Shadow study finished at",run,flush=True)

if __name__=="__main__":
    main()
