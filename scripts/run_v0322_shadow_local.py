#!/usr/bin/env python3
"""Local-only restartable 35-decision v0.3.22/2048 shadow study."""
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

ROOT=Path(__file__).resolve().parents[1]
RUNNER=ROOT/"scripts/diagnose_v0322_shadow_2048.py"
AGG=ROOT/"scripts/aggregate_v0322_shadow.py"
RUNTIME=ROOT/"Fantasy-Decision-Engine-v0.3.17-Development-Backtest-Runtime.zip"
PROTO=ROOT/"historical/protocol-v0.3.21-development-freeze.json"
BASE=ROOT/"scripts/run_historical_v0320_seat.py"
JOBS=[(season,seat) for season in ("2022-23","2023-24") for seat in range(1,7)]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+".tmp")
    tmp.write_text(json.dumps(data,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    os.replace(tmp,path)


def expected_source_shas(source):
    result={}
    with zipfile.ZipFile(source) as z:
        checks=json.loads(z.read("BUNDLE-CHECKSUMS.json"))
        if len(checks)!=55:
            raise RuntimeError("Not the completed v0.3.21 source bundle")
        for name,h in checks.items():
            if hashlib.sha256(z.read(name)).hexdigest()!=h:
                raise RuntimeError(f"Source checksum mismatch: {name}")
        summary=json.loads(z.read("results/summary.json"))
        if (summary.get("version")!="v0.3.21-historical-development"
                or summary["engine_decision_status"]["unconfirmed_edge"]!=35):
            raise RuntimeError("Wrong source experiment/decision population")
        for s,i in JOBS:
            result[(s,i)]=hashlib.sha256(z.read(f"seat_results/{s}_seat{i}.json")).hexdigest()
    return result


def complete(file,season,seat,source_sha):
    if not file.is_file():return False
    try:
        v=json.loads(file.read_text(encoding="utf-8"))
        return (
            v.get("complete") is True
            and v.get("version")=="v0.3.22-dev-shadow-2048-seat/1"
            and (v.get("season"),v.get("seat"))==(season,seat)
            and v.get("source_seat_sha256")==source_sha
            and v.get("target_budget")==2048
        )
    except (ValueError,KeyError,TypeError):return False


def bundle(run):
    required=["manifest.json","status.json","summary.json","decision_comparison.csv"]
    include=[run/name for name in required]
    for s,i in JOBS:
        include.append(run/"seat_results"/f"{s}_seat{i}.json")
    logs=sorted((run/"logs").glob("*.log"))
    if len(logs)!=12:
        raise RuntimeError(f"Expected 12 worker logs, got {len(logs)}")
    include.extend(logs)
    if any(not path.is_file() for path in include):
        raise RuntimeError("Missing file while bundling")
    hashes={p.relative_to(run).as_posix():sha(p) for p in include}
    hp=run/"BUNDLE-CHECKSUMS.json"
    atomic(hp,hashes)
    include.append(hp)
    done=run/"COMPLETE.json"
    atomic(done,{
        "version":"v0.3.22-2048-shadow-complete/1",
        "jobs":12,"scope":"DEVELOPMENT_FIXED_SOURCE_SHADOW_ONLY",
        "date_utc":now(),
        "validation_unopened":True,"holdout_unopened":True
    })
    include.append(done)
    out=run/"v0322-shadow-2048-results-bundle.zip"
    if out.exists():out.unlink()
    with zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED) as z:
        for p in include:z.write(p,p.relative_to(run))
    with zipfile.ZipFile(out) as z:
        for name,h in hashes.items():
            if hashlib.sha256(z.read(name)).hexdigest()!=h:
                raise RuntimeError(f"Output bundle verification failed: {name}")
    print("BUNDLE_READY",out,flush=True)


def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument("--source-bundle",type=Path,required=True)
    a.add_argument("--run-dir",type=Path,default=ROOT/"historical/local_runs/v0322-shadow-2048")
    a.add_argument("--workers",type=int,default=max(1,min(4,(os.cpu_count() or 2)//2)))
    args=a.parse_args()
    if args.workers<1:raise SystemExit("workers must be positive")
    source=args.source_bundle.resolve()
    if not source.is_file():raise SystemExit("Missing original v0.3.21 results ZIP")
    source_shas=expected_source_shas(source)
    run=args.run_dir.resolve()
    manifest={
        "version":"v0.3.22-local-development-shadow/1",
        "created_at":now(),
        "source_bundle_sha256":sha(source),
        "runner_sha256":sha(RUNNER),
        "aggregator_sha256":sha(AGG),
        "protocol_sha256":sha(PROTO),
        "base_runner_sha256":sha(BASE),
        "runtime_zip_sha256":sha(RUNTIME),
        "world_2022_23_sha256":sha(ROOT/"historical/generated_v0318/2022-23/world.json.gz"),
        "world_2023_24_sha256":sha(ROOT/"historical/generated_v0318/2023-24/world.json.gz"),
        "target_budget":2048,
        "source_protocol_ceiling":1024,
        "python_version":".".join(map(str,sys.version_info[:3])),
        "hash_seed":"0",
    }
    mp=run/"manifest.json"
    if mp.exists():
        old=json.loads(mp.read_text(encoding="utf-8"))
        for k,v in manifest.items():
            if k!="created_at" and old.get(k)!=v:
                raise RuntimeError(f"Changed research provenance: {k}; use a new run directory")
        manifest["created_at"]=old["created_at"]
    atomic(mp,manifest)
    env=os.environ.copy()
    env["PYTHONHASHSEED"]="0"
    for var in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS","VECLIB_MAXIMUM_THREADS"):
        env[var]="1"
    lock=threading.Lock()
    status={f"{s}_seat{i}":{"state":"PENDING"} for s,i in JOBS}
    for s,i in JOBS:
        if complete(run/"seat_results"/f"{s}_seat{i}.json",s,i,source_shas[(s,i)]):
            status[f"{s}_seat{i}"]["state"]="COMPLETE"
    def update(name,state,**extra):
        with lock:
            status[name].update(state=state,**extra)
            atomic(run/"status.json",{"updated_at":now(),"jobs":status})
    atomic(run/"status.json",{"updated_at":now(),"jobs":status})

    def job(s,i):
        name=f"{s}_seat{i}"
        out=run/"seat_results"/f"{name}.json"
        if complete(out,s,i,source_shas[(s,i)]):return True
        cmd=[sys.executable,str(RUNNER),
             "--source-bundle",str(source),
             "--output-dir",str(run/"seat_results"),
             "--season",s,"--seat",str(i)]
        log=run/"logs"/f"{name}.log"
        log.parent.mkdir(parents=True,exist_ok=True)
        update(name,"RUNNING",started_at=now())
        with log.open("a",encoding="utf-8") as f:
            f.write("\nRUN "+now()+"\n")
            f.flush()
            proc=subprocess.run(cmd,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,env=env)
        passed=proc.returncode==0 and complete(out,s,i,source_shas[(s,i)])
        update(name,"COMPLETE" if passed else "FAILED",ended_at=now(),return_code=proc.returncode)
        return passed

    failures=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures={ex.submit(job,s,i):(s,i) for s,i in JOBS}
        for f in concurrent.futures.as_completed(futures):
            s,i=futures[f]
            try:
                if not f.result():failures.append((s,i))
            except Exception as exc:
                failures.append((s,i))
                update(f"{s}_seat{i}","FAILED",error=str(exc))
    if failures:
        raise RuntimeError(f"Study stopped. Failed seats: {failures}. Rerun to resume.")

    result=subprocess.run([sys.executable,str(AGG),"--run-dir",str(run)],cwd=ROOT,env=env)
    if result.returncode:raise RuntimeError("Aggregation failed")
    bundle(run)


if __name__=="__main__":
    main()
