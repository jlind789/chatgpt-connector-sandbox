# v0.3.21 — Full Historical Development Rerun (Local Only)

This is the 12-seat **2022–23 and 2023–24 development** evaluation of the new frozen maximum 1,024-rollout convergence rule. It does **not** open 2024–25 validation or 2025–26 holdout.

The fixed decision method remains M0.3, 0.10 utility-point practical threshold, B3 continuation and **two consecutive resolved checkpoints with the same leader**. The acceptance criteria (including <=15% unconfirmed) remain unchanged.

## Start

1. On GitHub, choose branch **dev/v0321-full-development**.
2. Select **Code → Download ZIP**.
3. Extract the entire repository somewhere permanent on your Windows computer.
4. Double-click **RUN-V0321-FULL.cmd**.

No previous v0.3.20 results ZIP is required for this full development rerun. Do **not** enter a ZIP path; the command does not ask for one.

The launcher automatically verifies/installs uv, Python 3.13.15 and the frozen dependencies, runs syntax checks and contract tests, then performs the frozen local v0.3.19 development-only equivalence certification before starting the long job.

It uses four seat workers by default where hardware permits. For a custom worker limit, run:

\`\`\`powershell
.\local_research\run-v0321-full.ps1 -Workers 2
\`\`\`

## Stages

1. **Phase A: outcome-blind commitments** — every B1/B2/B3/Engine draft across all 12 seats is generated without reading realized player-game outcomes. Phase A records full pick traces and frozen code/protocol/world hashes; checkpoints after completed Engine picks.
2. **Restart-certification barrier** — resumes the 2022–23 seat-1 draft from checkpoint 18 (of 19 Engine picks) and requires **byte-identical** final commitment versus the uninterrupted Phase A output. If it differs, the run stops before any outcome scoring.
3. **Phase B: realized-outcome evaluation** — uses new Python processes to load historical realized data and score the immutable committed rosters.
4. **Full development aggregation and the unchanged pre-registered v0.3.20 development gate** — all 12 seats / 228 decisions, status rates and B1/B2/B3 comparisons.
5. **Results bundle** — a verified ZIP of all 12 pre-outcome commitments, all 12 scored seat reports, gate results, progress/logs, restart certification and SHA-256 checksums.

The Phase A → Phase B barrier is enforced in the coordinator. Do not bypass it by running seat scripts manually.

## Progress / recovery

The main command may be quiet for an hour or more while individual jobs write logs. View:

\`historical\local_runs\v0321-full-development\status.json\`

Each seat has a \`COMMIT\` and \`EVALUATE\` status. Draft checkpoint files appear under:

\`historical\local_runs\v0321-full-development\checkpoints\`

The per-seat logs appear under:

\`historical\local_runs\v0321-full-development\logs\`

An interrupted run can be restarted by double-clicking **the same existing folder's** \`RUN-V0321-FULL.cmd\`. Do **not** download a fresh repo while a run is in progress or copy saved checkpoints to a different code version. Provenance mismatches fail closed.

The full run may take many hours (and could be slower than v0.3.20); it depends heavily on CPU and the number of difficult decisions. Avoid setting more workers than your memory/CPU can sustain.

## Upload back into ChatGPT

After successful completion, upload exactly:

\`historical\local_runs\v0321-full-development\v0321-full-development-results-bundle.zip\`

This is **not** the GitHub source download.

The output will be independently audited before any decision about opening validation. The programmatic gate report is a development check, **not** permission to open 2024–25 by itself.

## GitHub usage policy

Large Monte Carlo grids, long backtests and the full development run must execute on your PC. GitHub remains for source control and modest tests, not heavy parallel compute.

## Frozen governance links

- \`historical/protocol-v0.3.21-development-freeze.json\`
- \`docs/decision-engine/PROMOTION-GATE-v0.3.20.md\`
- \`docs/decision-engine/V0321-SHADOW-INDEPENDENT-AUDIT.md\`
- \`docs/decision-engine/V0321-FULL-RUN-PREREQUISITES.md\`
