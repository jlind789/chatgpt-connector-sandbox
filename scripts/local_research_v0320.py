#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ZIP = ROOT / "Fantasy-Decision-Engine-v0.3.17-Development-Backtest-Runtime.zip"
PROTOCOL = ROOT / "historical/protocol-v0.3.20-development-freeze.json"
RUNNER = ROOT / "scripts/run_historical_v0320_local_seat.py"
BASE_RUNNER = ROOT / "scripts/run_historical_v0320_seat.py"
AGGREGATOR = ROOT / "scripts/aggregate_historical_v0320.py"

SEASONS = ("2022-23", "2023-24")
SEATS = range(1, 7)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def ensure_runtime() -> None:
    target = ROOT / ".runtime_v0317"
    if (target / "decision_engine").exists():
        return
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(RUNTIME_ZIP) as z:
        z.extractall(target)


def git_head() -> str | None:
    try:
        p = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        )
        return p.stdout.strip()
    except Exception:
        return None


def valid_result(path: Path, season: str, seat: int) -> bool:
    if not path.exists():
        return False
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return (
            obj.get("version") == "v0.3.20-development-seat"
            and obj.get("season") == season
            and int(obj.get("seat")) == seat
            and obj.get("results", {}).get("DECISION_ENGINE", {}).get("draft", {}).get("status") == "COMPLETE"
        )
    except Exception:
        return False


def default_workers() -> int:
    cpu = os.cpu_count() or 2
    return max(1, min(4, cpu // 2 or 1))


class Status:
    def __init__(self, path: Path, jobs):
        self.path = path
        self.lock = threading.Lock()
        self.rows = {
            key: {
                "season": season,
                "seat": seat,
                "state": "PENDING",
                "started_at": None,
                "completed_at": None,
                "elapsed_seconds": None,
                "return_code": None,
            }
            for key, season, seat in jobs
        }

    def set(self, key, **changes):
        with self.lock:
            self.rows[key].update(changes)
            self.write()

    def write(self):
        counts = {}
        for row in self.rows.values():
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        obj = {
            "version": "local-research-status/0.1",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "counts": counts,
            "jobs": [self.rows[k] for k in sorted(self.rows)],
        }
        atomic_json(self.path, obj)


def run_job(
    python_exe: str,
    run_dir: Path,
    season: str,
    seat: int,
    status: Status,
    env: dict[str, str],
) -> tuple[str, int]:
    key = f"{season}-seat{seat}"
    result = run_dir / "seat_results" / f"{season}_seat{seat}.json"
    log = run_dir / "logs" / f"{season}_seat{seat}.log"
    checkpoints = run_dir / "checkpoints" / key

    if valid_result(result, season, seat):
        status.set(
            key,
            state="COMPLETE",
            completed_at=datetime.now(timezone.utc).isoformat(),
            return_code=0,
        )
        return key, 0

    status.set(key, state="RUNNING", started_at=datetime.now(timezone.utc).isoformat())
    started = time.perf_counter()
    cmd = [
        python_exe,
        str(RUNNER),
        season,
        str(seat),
        "--output-dir",
        str(run_dir / "seat_results"),
        "--checkpoint-dir",
        str(checkpoints),
    ]
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write("\n=== START " + datetime.now(timezone.utc).isoformat() + " ===\n")
        f.write("COMMAND: " + " ".join(cmd) + "\n")
        f.flush()
        p = subprocess.run(
            cmd,
            cwd=ROOT,
            stdout=f,
            stderr=subprocess.STDOUT,
            env=env,
        )
    elapsed = time.perf_counter() - started
    ok = p.returncode == 0 and valid_result(result, season, seat)
    status.set(
        key,
        state="COMPLETE" if ok else "FAILED",
        completed_at=datetime.now(timezone.utc).isoformat(),
        elapsed_seconds=elapsed,
        return_code=p.returncode,
    )
    return key, 0 if ok else (p.returncode or 1)


def aggregate(run_dir: Path) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import aggregate_historical_v0320 as agg

    agg.IN = run_dir / "seat_results"
    agg.OUT = run_dir / "results"
    agg.main()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--run-dir",
        type=Path,
        default=ROOT / "historical/local_runs/v0320-development",
        help="Stable run directory. Re-running with the same path resumes completed jobs/checkpoints.",
    )
    ap.add_argument("--workers", type=int, default=default_workers())
    ap.add_argument("--no-aggregate", action="store_true")
    args = ap.parse_args()

    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")

    ensure_runtime()

    run_dir = args.run_dir.resolve()
    for d in ("seat_results", "checkpoints", "logs", "results"):
        (run_dir / d).mkdir(parents=True, exist_ok=True)

    manifest_path = run_dir / "manifest.json"
    manifest = {
        "version": "local-v0320-research-manifest/0.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository_head": git_head(),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "platform": platform.platform(),
        "worker_count": args.workers,
        "protocol_path": str(PROTOCOL.relative_to(ROOT)),
        "protocol_sha256": sha256_file(PROTOCOL),
        "local_runner_sha256": sha256_file(RUNNER),
        "base_runner_sha256": sha256_file(BASE_RUNNER),
        "aggregator_sha256": sha256_file(AGGREGATOR),
        "runtime_zip_sha256": sha256_file(RUNTIME_ZIP),
        "thread_policy": {
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
        },
        "seasons": list(SEASONS),
        "seats": list(SEATS),
    }
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        immutable = [
            "protocol_sha256",
            "local_runner_sha256",
            "base_runner_sha256",
            "aggregator_sha256",
            "runtime_zip_sha256",
        ]
        for key in immutable:
            if old.get(key) != manifest.get(key):
                raise SystemExit(
                    f"Existing run manifest mismatch for {key}. Use a new --run-dir instead of mixing versions."
                )
        manifest["created_at"] = old.get("created_at", manifest["created_at"])
    atomic_json(manifest_path, manifest)

    env = os.environ.copy()
    for k, v in manifest["thread_policy"].items():
        env[k] = v

    jobs = [
        (f"{season}-seat{seat}", season, seat)
        for season in SEASONS
        for seat in SEATS
    ]
    status = Status(run_dir / "status.json", jobs)
    for key, season, seat in jobs:
        if valid_result(run_dir / "seat_results" / f"{season}_seat{seat}.json", season, seat):
            status.rows[key]["state"] = "COMPLETE"
    status.write()

    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {
            ex.submit(
                run_job,
                sys.executable,
                run_dir,
                season,
                seat,
                status,
                env,
            ): key
            for key, season, seat in jobs
        }
        for fut in concurrent.futures.as_completed(futures):
            key = futures[fut]
            try:
                _, code = fut.result()
                if code:
                    failures.append(key)
            except Exception as exc:
                failures.append(key)
                status.set(
                    key,
                    state="FAILED",
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    return_code=-1,
                )
                print(f"{key}: {exc}", file=sys.stderr, flush=True)

    if failures:
        print("FAILED JOBS:", ", ".join(sorted(failures)), file=sys.stderr)
        raise SystemExit(1)

    if not args.no_aggregate:
        aggregate(run_dir)

    complete = {
        "version": "local-v0320-research-complete/0.1",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "seat_results": 12,
        "aggregated": not args.no_aggregate,
        "results_dir": str((run_dir / "results").resolve()),
    }
    atomic_json(run_dir / "COMPLETE.json", complete)
    print(json.dumps(complete, indent=2))


if __name__ == "__main__":
    main()
