#!/usr/bin/env python3
"""Bundle immutable v0.3.21 draft commitments, evaluations, gate and logs."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def sha(path: Path)->str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir",type=Path,required=True)
    args=ap.parse_args()
    run=args.run_dir.resolve()
    files=[]
    for name in ("manifest.json","status.json","commitment_manifest.json",
                 "results/summary.json","results/gate_report.json"):
        path=run/name
        if not path.is_file():raise RuntimeError(f"Missing {name}")
        files.append(path)
    for folder in ("commitments","seat_results"):
        rows=sorted((run/folder).glob("*.json"))
        if len(rows)!=12:
            raise RuntimeError(f"Expected 12 {folder} artifacts, got {len(rows)}")
        files.extend(rows)
    logs=sorted((run/"logs").glob("*.log"))
    if len(logs)<24:
        raise RuntimeError(f"Expected 24 phase logs, got {len(logs)}")
    files.extend(logs)
    checksum={str(p.relative_to(run)).replace("\\","/"):sha(p) for p in files}
    p=run/"BUNDLE-CHECKSUMS.json"
    p.write_text(json.dumps(checksum,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    files.append(p)
    completion={
        "version":"v0.3.21-full-development-complete/1",
        "date_utc":datetime.now(timezone.utc).isoformat(),
        "phase_a_commitments":12,
        "phase_b_evaluations":12,
        "scope":"DEVELOPMENT_ONLY",
        "validation_unopened":True,
        "holdout_unopened":True,
    }
    c=run/"COMPLETE.json"
    c.write_text(json.dumps(completion,indent=2)+"\n",encoding="utf-8")
    files.append(c)
    bundle=run/"v0321-full-development-results-bundle.zip"
    if bundle.exists():bundle.unlink()
    with zipfile.ZipFile(bundle,"w",compression=zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f,f.relative_to(run))
    with zipfile.ZipFile(bundle) as z:
        checks=json.loads(z.read("BUNDLE-CHECKSUMS.json"))
        for n,h in checks.items():
            if hashlib.sha256(z.read(n)).hexdigest()!=h:
                raise RuntimeError(f"Failed to verify output ZIP entry {n}")
    print(f"BUNDLED AND VERIFIED: {bundle}",flush=True)


if __name__=="__main__":
    main()
