# v0.3.22 / 2048 shadow — Local Windows instructions

This is **development-only research**, not a model release. The full v0.3.21 development results failed the frozen 15% unconfirmed-edge criterion by one decision (35/228 = 15.35% unconfirmed). We are testing whether the next statistically consistent checkpoint offers meaningful improvements while leaving the gate unchanged.

## What you need

- The **original v0.3.21 full-development results ZIP**, named \`v0321-full-development-results-bundle.zip\`. This is the 12-seat results file your previous local run produced and you uploaded to ChatGPT.
- This GitHub development branch, \`dev/v0322-2048-shadow\`.
- Your local Windows computer for the heavy Monte Carlo work.

Do **not** use GitHub Actions for this research.

## One-click start

1. On GitHub, switch to branch **dev/v0322-2048-shadow**.
2. Click **Code → Download ZIP** and extract it to a **new folder**.
3. Double-click **RUN-V0322-SHADOW.cmd** in that folder.
4. If asked, navigate to your previous extracted v0.3.21 folder and locate:

\`historical\local_runs\v0321-full-development\v0321-full-development-results-bundle.zip\`

5. In File Explorer, right-click that file, choose **Copy as path**, and paste the whole path at the prompt.

**Do not enter the downloaded GitHub repository ZIP.** The launcher validates that the chosen ZIP includes all 12 results and required manifests before starting certification.

The launcher sets up/uses the same frozen Python version and dependencies, runs static/synthetic tests, performs the earlier frozen development-only calibration certification, then starts the 35-decision shadow study.

## Duration and progress

This is a targeted study of the 35 originally unconfirmed states, not all 228 decisions. Nevertheless, 2048 paired rollouts can take hours. The exact runtime depends on CPU, memory, and how many rows survive pruning.

Progress:

\`historical\local_runs\v0322-shadow-2048\status.json\`

Per-seat logs:

\`historical\local_runs\v0322-shadow-2048\logs\`

Completed decision checkpoints:

\`historical\local_runs\v0322-shadow-2048\seat_results\`

If interrupted, **run the same launcher in the same extracted folder again**. It reuses valid decision-level checkpoints. Avoid downloading a new branch ZIP while the same study is in progress.

## Output

After the study finishes successfully, upload:

\`historical\local_runs\v0322-shadow-2048\v0322-shadow-2048-results-bundle.zip\`

This ZIP contains:
- a provenance manifest and all exact source identifiers;
- outputs of all 12 local seat jobs;
- individual 2048 decision-stage diagnostics for all 35 source-unconfirmed decisions;
- \`summary.json\` and \`decision_comparison.csv\`;
- logs, checksums, and the completion marker.

## Scientific limitations

The 2048 shadow extends only the 35 unconfirmed decisions from the **previous v0.3.21 fixed draft paths**. Other picks are not recomputed. If a result appears good, we will still need a **separately frozen full 12-seat development rerun** of a potential v0.3.22 model with the same 15% threshold.

Neither 2024-25 validation nor 2025-26 holdout may be opened based on shadow outcomes.

Research protocol:
\`docs/decision-engine/V0322-2048-SHADOW-RESEARCH-PROTOCOL.md\`
