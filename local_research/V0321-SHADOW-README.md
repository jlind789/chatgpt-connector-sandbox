# v0.3.21 shadow development runner

**This is a development-only convergence experiment, not a model release or unseen validation.**

Original 12-seat v0.3.20 result: 95 confirmed / 80 practical ties / 53 unconfirmed.
The study replays those 53 original unconfirmed decisions through a new **1024-rollout diagnostic checkpoint**.

## Before starting

- Use the GitHub branch **`dev/v0321-shadow-research`**, not the default main branch.
- Keep your original `v0320-development-results-bundle.zip`.
- Work on your local Windows computer. Do not dispatch any GitHub Actions research jobs.
- Do not edit or read 2024-25 validation / 2025-26 holdout inputs.

## One-click start

Download/extract this branch, then double-click:

`RUN-V0321-SHADOW.cmd`

The launcher installs/verifies the same Python environment and prerequisites as v0.3.20, replays the frozen v0.3.19 equivalence check, and then starts the shadow jobs.

It searches for your v0.3.20 bundle in:
- this repo's root directory;
- your Windows Downloads folder.

If it can't find the ZIP, it asks you to type or paste its full path. The path can be copied in Windows File Explorer: navigate to the old extracted repository, open `historical\\local_runs\\v0320-development`, right-click `v0320-development-results-bundle.zip` and select **Copy as path**; paste the resulting quoted path at the prompt.

If the launcher fails with `Test-Path : Cannot bind argument to parameter 'Path' because it is an empty string`, the downloaded launcher predates commit `28a8d0f4aca20f0cd6a686387bba6e38ee84c7ea`. Replace only `local_research\\run-v0321-shadow.ps1` with the latest file from this branch, or download the current branch ZIP. Previously installed Python and uv do not need to be installed again.

It starts at most **4 local seat workers** by default. Individual completed decisions are checkpointed, so you may rerun the launcher to resume after interruption.

## Test before full run (optional)

From the established virtual environment:

```powershell
.\.venv-local-research\Scripts\python.exe scripts\test_v0321_shadow_contract.py
```

These are small synthetic code/integrity tests, not a completed historical equivalence replay. Source checkpoint reproduction is checked again for every real decision before extension.

## Progress

`historical\local_runs\v0321-shadow-1024\status.json`

Per-seat progress:

`historical\local_runs\v0321-shadow-1024\logs\`

Individual completed decision checkpoints:

`historical\local_runs\v0321-shadow-1024\seat_results\`

At completion, automatic aggregation produces:

- `summary.json`
- `decision_comparison.csv`
- `COMPLETE.json`
- `v0321-shadow-diagnostic-results.zip`

Upload the final ZIP to the Decision Engine development chat for independent auditing.

## What it cannot tell us

This replay holds the previous v0.3.20 draft trajectory fixed. It evaluates 1024-rollout behavior **only at the 53 original unconfirmed decision states**.

It does not recompute all 228 decisions, existing practical ties, or subsequent draft states after hypothetical changed choices.

A promising result must be followed by a newly frozen **full 12-seat development rerun** before the original development gate can be applied. The existing 15% threshold is unchanged.

## Scientific safeguards

The script:
- checks every listed SHA-256 source-bundle checksum;
- checks the source experiment, protocol SHA, base-runner SHA, seat and team identity;
- reproduces the complete saved 1->512 stage path before extending;
- stops on any reproduction discrepancy;
- does not load realized outcomes;
- writes checkpointed results atomically;
- excludes all unseen seasons.
