# Local Research Harness — v0.3.20

This harness replaces heavy GitHub-hosted Actions research with local Windows execution.

## Quick start

From a cloned/downloaded copy of the repository on Windows, double-click:

`RUN-RESEARCH.cmd`

or run:

```powershell
.\local_research\run-v0320.ps1
```

The first run will:

1. locate/install `uv`;
2. install the pinned Python 3.13.15 runtime;
3. create `.venv-local-research`;
4. install the frozen tested dependencies;
5. prepare `.runtime_v0317`;
6. run a frozen development-only equivalence certification;
7. only if certification passes, start/resume the 12-seat v0.3.20 development run;
8. aggregate the completed results;
9. create a review bundle ZIP.

## Safety

Heavy research is local-only. Do not re-enable the 12-seat GitHub Actions workflow as the primary compute path.

The default run directory is:

`historical/local_runs/v0320-development`

Re-running the same launcher resumes completed seat outputs and per-decision checkpoints.

## Parallelism

Default worker count is conservative and capped at 4.

To choose a worker count manually:

```powershell
.\local_research\run-v0320.ps1 -Workers 3
```

Each worker forces BLAS/OpenMP numerical libraries to one thread, so parallelism happens at the seat-job level instead of multiplying threads inside each simulation.

## Certification

Before the first heavy run, the harness replays the frozen v0.3.19 pilot:

- 2022-23
- seat 1
- convergence chunk 5

It compares the local result against the committed reference, ignoring only wall-clock `elapsed_seconds` fields and allowing numerical tolerance of `1e-12`.

If certification fails, v0.3.20 does not start.

## Checkpointing

The local seat runner writes an atomic checkpoint after every completed Decision Engine pick.

A checkpoint is bound to:

- the frozen v0.3.20 protocol;
- the canonical world/snapshot;
- focus team;
- base-runner SHA;
- protocol SHA;
- preceding full draft trace.

If the fingerprint does not match, reuse fails visibly.

## Outputs

Progress:

`historical/local_runs/v0320-development/status.json`

Manifest/provenance:

`historical/local_runs/v0320-development/manifest.json`

Per-seat logs:

`historical/local_runs/v0320-development/logs/`

Per-decision checkpoints:

`historical/local_runs/v0320-development/checkpoints/`

Seat results:

`historical/local_runs/v0320-development/seat_results/`

Aggregated development result:

`historical/local_runs/v0320-development/results/summary.json`

Automatic review bundle:

`historical/local_runs/v0320-development/v0320-development-results-bundle.zip`

The bundle includes the manifest, status, completion marker, all 12 seat results, aggregate results, logs, and SHA-256 checksums. It intentionally excludes bulky per-decision checkpoints.

Do not open 2024-25 validation based solely on run completion. Apply the pre-registered development gate first.
