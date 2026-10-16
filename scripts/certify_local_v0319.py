#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "historical/calibration_v0319/chunks/2022-23_seat1_chunk5.json"
CALIBRATOR = ROOT / "scripts/calibrate_v0319_convergence.py"
RUNTIME_ZIP = ROOT / "Fantasy-Decision-Engine-v0.3.17-Development-Backtest-Runtime.zip"
CERT_DIR = ROOT / "historical/local_certification/v0319-seat1-chunk5"
MARKER = CERT_DIR / "CERTIFIED.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def compare(a, b, path="$", tol=1e-12, diffs=None):
    if diffs is None:
        diffs = []
    if isinstance(a, dict) and isinstance(b, dict):
        ka = set(a) - {"elapsed_seconds"}
        kb = set(b) - {"elapsed_seconds"}
        if ka != kb:
            diffs.append(f"{path}: key mismatch {sorted(ka-kb)} / {sorted(kb-ka)}")
            return diffs
        for k in sorted(ka):
            compare(a[k], b[k], f"{path}.{k}", tol, diffs)
        return diffs
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{path}: list length {len(a)} != {len(b)}")
            return diffs
        for i, (x, y) in enumerate(zip(a, b)):
            compare(x, y, f"{path}[{i}]", tol, diffs)
        return diffs
    if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool):
        if not (math.isfinite(float(a)) and math.isfinite(float(b))):
            if a != b:
                diffs.append(f"{path}: non-finite mismatch {a!r} != {b!r}")
        elif abs(float(a) - float(b)) > tol:
            diffs.append(f"{path}: numeric mismatch {a!r} != {b!r}")
        return diffs
    if a != b:
        diffs.append(f"{path}: {a!r} != {b!r}")
    return diffs


def expected_marker():
    return {
        "version": "local-equivalence-certification/0.1",
        "reference_sha256": sha256_file(REFERENCE),
        "calibrator_sha256": sha256_file(CALIBRATOR),
        "runtime_zip_sha256": sha256_file(RUNTIME_ZIP),
        "ignored_fields": ["elapsed_seconds"],
        "numeric_tolerance": 1e-12,
    }


def run_calibration():
    sys.path.insert(0, str(ROOT / "scripts"))
    import calibrate_v0319_convergence as cal

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    cal.OUT = CERT_DIR
    old = list(sys.argv)
    try:
        sys.argv = [str(CALIBRATOR), "2022-23", "1", "5"]
        cal.main()
    finally:
        sys.argv = old


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    marker = expected_marker()
    if not args.force and MARKER.exists():
        try:
            existing = json.loads(MARKER.read_text(encoding="utf-8"))
            if all(existing.get(k) == v for k, v in marker.items()):
                print("Local equivalence certification already valid for current frozen inputs.")
                return
        except Exception:
            pass

    if CERT_DIR.exists():
        for p in CERT_DIR.glob("*.json"):
            if p.name != "CERTIFIED.json":
                p.unlink()

    run_calibration()
    candidate = CERT_DIR / "2022-23_seat1_chunk5.json"
    if not candidate.exists():
        raise SystemExit("Certification candidate was not produced.")

    ref = json.loads(REFERENCE.read_text(encoding="utf-8"))
    got = json.loads(candidate.read_text(encoding="utf-8"))
    diffs = compare(ref, got)
    if diffs:
        print("LOCAL EQUIVALENCE CERTIFICATION FAILED", file=sys.stderr)
        for line in diffs[:100]:
            print(line, file=sys.stderr)
        if len(diffs) > 100:
            print(f"... {len(diffs)-100} additional differences", file=sys.stderr)
        raise SystemExit(1)

    marker["certified_at_note"] = "Generated locally; wall-clock timing fields intentionally excluded."
    tmp = MARKER.with_suffix(".tmp")
    tmp.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, MARKER)
    print("LOCAL EQUIVALENCE CERTIFICATION PASSED")


if __name__ == "__main__":
    main()
