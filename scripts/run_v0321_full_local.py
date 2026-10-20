#!/usr/bin/env python3
"""Local-only two-phase v0.3.21 12-seat development research coordinator."""
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
SCRIPTS=ROOT/"scripts"
PROTOCOL=ROOT/"historical/protocol-v0.3.21-development-freeze.json"
PHASE_A=SCRIPTS/"run_v0321_commit_seat.py"
PHASE_B=SCRIPTS/"run_v0321_evaluate_seat.py"
AGG=SCRIPTS/"aggregate_v0321_full.py"
BUNDLE=SCRIPTS/"bundle_v0321_full.py"
CERT=SCRIPTS/"test_v0321_resume_equivalence.py"
SOURCE_FILES=[
    PROTOCOL, PHASE_A, PHASE_B, AGG, BUNDLE,
    SCRIPTS/"run_historical_v0320_seat.py",
    SCRIPTS/"run_historical_v0320_local_seat.py",
    ROOT/"Fantasy-Decision-Engine-v0.3.17-Development-Backtest-Runtime.zip",
    ROOT/"historical/generated_v0318/2022-23/world.json.gz",
    ROOT/"historical/generated_v0318/2023-24/world.json.gz",
    CERT,
    SCRIPTS/"run_v0321_full_local.py",
    ROOT/"runtime_v0317/requirements-tested.txt",
]
JOBS=[(s,i) for s in ("2022-23","2023-24") for i in range(1,7)]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+".tmp")
    tmp.write_text(json.dumps(data,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    os.replace(tmp,path)


def completed_record(path, phase, season, seat):
    if not path.is_file():return False
    try:
        r=json.loads(path.read_text(encoding="utf-8"))
        if (r["season"],r["seat"])!=(season,seat):return False
        if phase=="COMMIT":
            return (r["version"]=="v0.3.21-development-draft-commitment/1"
                    and all(q["status"]=="COMPLETE" for q in r["drafts"].values()))
        return (r["version"]=="v0.3.21-development-seat"
                and r["draft_commitment_precedes_outcome_load"])
    except (ValueError,KeyError,TypeError):return False


def phase_run(phase, script, run, workers, env, status, lock):
    def update(key,state,**kwargs):
        with lock:
            status["jobs"][key].update(state=state,**kwargs)
            status["updated_at"]=now()
            write(run/"status.json",status)
    def work(season,seat):
        key=f"{phase}:{season}_seat{seat}"
        folder="commitments" if phase=="COMMIT" else "seat_results"
        path=run/folder/f"{season}_seat{seat}.json"
        if completed_record(path,phase,season,seat):
            update(key,"COMPLETE",verified_at=now())
            return True
        cmd=[sys.executable,str(script),season,str(seat)]
        if phase=="COMMIT":
            cmd+=["--output-dir",str(run/"commitments"),
                  "--checkpoint-dir",str(run/"checkpoints"/f"{season}_seat{seat}")]
        else:
            cmd+=["--commit-dir",str(run/"commitments"),
                  "--output-dir",str(run/"seat_results")]
        log=run/"logs"/f"{phase.lower()}-{season}_seat{seat}.log"
        log.parent.mkdir(parents=True,exist_ok=True)
        update(key,"RUNNING",started_at=now())
        with log.open("a",encoding="utf-8") as f:
            f.write("\nRUN "+now()+"\n")
            f.flush()
            proc=subprocess.run(cmd,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT)
        passed=(proc.returncode==0 and completed_record(path,phase,season,seat))
        update(key,"COMPLETE" if passed else "FAILED",ended_at=now(),return_code=proc.returncode)
        return passed

    failed=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures={ex.submit(work,s,i):(s,i) for s,i in JOBS}
        for future in concurrent.futures.as_completed(futures):
            s,i=futures[future]
            try:
                if not future.result():failed.append((s,i))
            except Exception as exc:
                failed.append((s,i))
                update(f"{phase}:{s}_seat{i}","FAILED",error=str(exc))
    if failed:
        raise RuntimeError(f"{phase} phase failed for seats {failed}; rerun the same launcher to resume")


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir",type=Path,
                    default=ROOT/"historical/local_runs/v0321-full-development")
    ap.add_argument("--workers",type=int,default=max(1,min(4,(os.cpu_count() or 2)//2)))
    args=ap.parse_args()
    if args.workers<1:raise SystemExit("--workers must be positive")
    run=args.run_dir.resolve()
    runtime=ROOT/".runtime_v0317"
    if not (runtime/"decision_engine").exists():
        runtime.mkdir(exist_ok=True)
        with zipfile.ZipFile(SOURCE_FILES[7]) as z:z.extractall(runtime)

    manifest_path=run/"manifest.json"
    proofs={str(p.relative_to(ROOT)):sha(p) for p in SOURCE_FILES}
    provenance={
        "version":"v0.3.21-full-development-local/1",
        "created_at":now(),
        "files_sha256":proofs,
        "python_version":".".join(map(str,sys.version_info[:3])),
        "protocol_max_rollouts":1024,
        "seasons":["2022-23","2023-24"],
        "seats_per_season":6,
        "phase_boundary":"ALL_DRAFT_COMMITMENTS_THEN_SEPARATE_PROCESS_REALIZED_EVALUATION",
        "python_hash_seed_policy":"PYTHONHASHSEED=0",
    }
    if manifest_path.exists():
        old=json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("version","files_sha256","python_version","protocol_max_rollouts","seasons",
                    "seats_per_season","phase_boundary","python_hash_seed_policy"):
            if old.get(key)!=provenance[key]:
                raise RuntimeError(f"Stale run provenance: {key}; choose a NEW --run-dir")
        provenance["created_at"]=old["created_at"]
    write(manifest_path,provenance)

    env=os.environ.copy()
    env["PYTHONHASHSEED"]="0"
    for key in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS","VECLIB_MAXIMUM_THREADS"):
        env[key]="1"

    lock=threading.Lock()
    status_path=run/"status.json"
    if status_path.exists():
        status=json.loads(status_path.read_text(encoding="utf-8"))
    else:
        status={
            "version":"v0.3.21-local-status/1",
            "updated_at":now(),
            "jobs":{f"{phase}:{s}_seat{i}":{"state":"PENDING"}
                    for phase in ("COMMIT","EVALUATE") for s,i in JOBS},
        }
        write(status_path,status)

    print("PHASE A: outcome-blind drafting and immutable commitments",flush=True)
    phase_run("COMMIT",PHASE_A,run,args.workers,env,status,lock)

    index={}
    for s,i in JOBS:
        path=run/"commitments"/f"{s}_seat{i}.json"
        if not completed_record(path,"COMMIT",s,i):
            raise RuntimeError("Missing verified Phase A commitment")
        index[path.name]=sha(path)
    index_path=run/"commitment_manifest.json"
    if index_path.exists():
        old=json.loads(index_path.read_text(encoding="utf-8"))
        if old.get("sha256")!=index:
            raise RuntimeError("Committed draft bytes changed after the Phase A boundary")
    write(index_path,{"phase":"A_FINISHED_BEFORE_OUTCOME_EVALUATION",
                      "created_at":now(),"sha256":index})

    print("CERTIFICATION: comparing uninterrupted and resumed final draft decisions",flush=True)
    cert=subprocess.run(
        [sys.executable,str(CERT),"--run-dir",str(run)],
        cwd=ROOT,env=env
    )
    if cert.returncode:
        raise RuntimeError("Restart equivalence failed; outcome scoring is blocked")

    print("PHASE B: evaluating committed drafts in new Python processes",flush=True)
    phase_run("EVALUATE",PHASE_B,run,args.workers,env,status,lock)

    print("Applying unchanged v0.3.20 development gate",flush=True)
    for script,extra in ((AGG,["--run-dir",str(run)]),
                          (BUNDLE,["--run-dir",str(run)])):
        proc=subprocess.run([sys.executable,str(script)]+extra,cwd=ROOT,env=env)
        if proc.returncode:
            raise RuntimeError(f"Post-run script failed: {script.name}")
    print("Full development run complete, bundled for independent review",flush=True)


if __name__=="__main__":
    main()
