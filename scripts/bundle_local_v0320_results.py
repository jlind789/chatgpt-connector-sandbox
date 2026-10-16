#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--run-dir",
        type=Path,
        default=ROOT / "historical/local_runs/v0320-development",
    )
    args = ap.parse_args()
    run_dir = args.run_dir.resolve()

    required = [
        run_dir / "manifest.json",
        run_dir / "status.json",
        run_dir / "COMPLETE.json",
        run_dir / "results/summary.json",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise SystemExit("Cannot bundle incomplete run; missing: " + ", ".join(missing))

    seat_files = sorted((run_dir / "seat_results").glob("*.json"))
    if len(seat_files) != 12:
        raise SystemExit(f"Expected 12 seat results, found {len(seat_files)}")

    include = []
    for p in required:
        include.append(p)
    include.extend(seat_files)
    include.extend(sorted((run_dir / "results").glob("*")))
    include.extend(sorted((run_dir / "logs").glob("*.log")))
    include = [p for p in include if p.is_file()]

    checksums = {
        str(p.relative_to(run_dir)).replace("\\", "/"): sha256_file(p)
        for p in include
    }
    checksum_path = run_dir / "BUNDLE-CHECKSUMS.json"
    checksum_path.write_text(json.dumps(checksums, indent=2) + "\n", encoding="utf-8")
    include.append(checksum_path)

    bundle = run_dir / "v0320-development-results-bundle.zip"
    if bundle.exists():
        bundle.unlink()

    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in include:
            z.write(p, p.relative_to(run_dir))

    print(bundle)


if __name__ == "__main__":
    main()
