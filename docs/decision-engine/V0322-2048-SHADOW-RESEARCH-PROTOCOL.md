# Frozen study plan — v0.3.22 2048-rollout development-only shadow

This plan was authored after the completed v0.3.21 full DEVELOPMENT run was audited and **before viewing any 2048-rollout results**.

## Source and motivation

Completed v0.3.21 full historical development:

- two development seasons, 2022-23 and 2023-24
- 12 focus-seat drafts, 228 Decision Engine decisions
- 112 CONFIRMED_EDGE, 81 PRACTICAL_TIE_MAX_BUDGET, **35 UNCONFIRMED_EDGE_MAX_BUDGET**
- unconfirmed rate 35/228 = 15.35%, exceeding the **unchanged 15% acceptance maximum** (at most 34)
- combined category share 0.526515 versus B2 0.505790: +2.0725 percentage points
- seven of 12 positive Engine-minus-B2 seats; positive median; both seasons outperformed B2
- all programmatically checked integrity and performance requirements passed except the confidence-rate limit
- pre-outcome commitments and post-commit evaluations separated by separate processes
- recorded full restart equivalence certification passed

The 35 unconfirmed cases reached the 1024 maximum; **25 were statistically separated at 1024** but lacked a consecutive separated checkpoint, while 10 were not separated at 1024. All 35 retained the same leading player at 512 and 1024, but same-leader stability alone does NOT establish statistical confirmation.

## Shadow population and procedure

Only the **35 source-unconfirmed decision states** from the v0.3.21 development bundle.

For each decision:

1. Verify the original ZIP contains all 12 source seats and all 55 SHA-256 checksums match.
2. Verify original 12 Phase A commitment SHA-256 values match both the Phase A commitment manifest and Phase B results.
3. Verify source frozen v0.3.21 protocol and base runner SHA-256 match the local research code.
4. Reconstruct the exact original draft state at the unconfirmed pick using the original draft trace and development-only preseason world snapshot.
5. Replay the full original run's budgets 1, 2, 4, ..., 1024 and verify exact identities, candidates, convergence states, separation, confidence status, and terminal numerical diagnostics at every checkpoint (tight tolerance 1e-10 for recorded float values only).
6. Extend the SAME paired-runner state to **2048 rollouts**. The unchanged two-consecutive-separated-same-leader rule determines confirmation.
7. Report terminal confirmed / practical tie / unconfirmed states and any leader changes.
8. Preserve the original v0.3.21 selected action and entire original draft trajectory; do not alter the original season-outcome scoring.
9. Write one atomic checkpoint after each finished shadow decision.

Neither realized game statistics nor 2024-25/2025-26 outcomes may be loaded by this experiment.

## Fixed methodology

- M0.3 recommendation/scoring mathematics unchanged.
- Original 0.10 utility-point practical-equivalence threshold unchanged.
- Original two-consecutive-resolved-checkpoint-same-leader confirmation unchanged.
- Broad-pool construction, survival/pruning, B3 continuation and baseline mix unchanged.
- Original v0.3.21 protocol remains frozen at **1024**. 2048 is introduced only for this **shadow diagnostic**.
- Do not assign subjective winners to practical ties.
- The original 15% maximum remains unchanged.

## Reporting

- Exact 35/35 reproduction coverage and fail-closed stage comparison.
- Confirmed/practical tie/unconfirmed counts at 2048.
- Breakdown for 25 source-separated and 10 source-unseparated cases.
- Number and identities of leader changes.
- Number of original unconfirmed states moved out of the unconfirmed category.
- Hypothetical *fixed-source* rate out of 228, clearly marked as not full-draft evidence.
- Per-decision elapsed time, all provenance hashes, all checksums and complete worker logs.

## Interpretation and decision rule

At least one of the 35 cases must move out of unconfirmed status to improve the fixed-source count to 34 or fewer. However, even a large reduction is NOT a pass of the existing development gate, because changing decisions elsewhere can change later draft states and all previously resolved/practically-tied cases have not been retested.

If 2048 offers meaningful, stable confirmation at acceptable compute cost, prepare a **new, separately frozen full 12-seat development protocol** with a 2048 maximum and rerun all drafts. Use the same frozen acceptance tests.

If the shadow reveals leader reversals or unsustainable compute, investigate on development data only before selecting a new method.

Keep both 2024-25 validation and 2025-26 holdout unopened.

## Compute and preservation

Local Windows research only. No GitHub Actions Monte Carlo execution.

GitHub branch: dev/v0322-2048-shadow

Source expected: v0321-full-development-results-bundle.zip

Output expected: historical/local_runs/v0322-shadow-2048/v0322-shadow-2048-results-bundle.zip

No source code or methodology in the existing v0.3.21 full-run branch is modified by this study.
