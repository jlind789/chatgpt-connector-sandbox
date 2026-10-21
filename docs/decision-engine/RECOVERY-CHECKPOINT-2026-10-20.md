# Decision Engine development handoff — 2026-10-20

## Full v0.3.21 development result

The owner's full local run is complete and independently audited, using the uploaded
\`v0321-full-development-results-bundle.zip\`, SHA-256:

\`a4e9f6e8fc2332c494ee0e5476782d16eed5a18145125afb934b8a694c090730\`.

All 12 focus-seat drafts / 228 Engine decisions completed in development years 2022-23 and 2023-24.

Aggregate mean category share:
- B1 0.4975649350649351
- B2 0.5057900432900433
- B3 0.4993777056277056
- Engine 0.5265151515151515

Engine vs B2 +2.0725108225 percentage points.
Positive B2 seats: 7 / 12. Median E−B2: +2.4675324675 pp.

By season: E−B2 +1.125541 pp (2022-23) and +3.019481 pp (2023-24).

Confidence states:
- confirmed 112
- practical ties 81
- **unconfirmed 35 / 228 = 15.35087719%**

**Development gate FAILURE:** 15% maximum permits at most 34 unconfirmed decisions. The model misses by exactly one, with no basis for relaxing the threshold or opening unseen data.

All other programmatically checked development gate conditions passed.

## Integrity

- 55/55 bundle SHA-256 checksums match; ZIP CRC passed.
- All 12 Phase A commitment artifacts and all 12 Phase B evaluation artifacts exist and have matching immutable hash links.
- Each of the four draft records per seat is identical between the commitment and evaluation.
- Recorded checkpoint-resume certification: byte-identical result from a resumed 2022-23 seat-1 decision (after Engine pick 18).
- All development baselines reproduce frozen v0.3.18 scores.
- The outcome-blind phase barrier is evidenced by separate commitment/scoring processes and phase records.
- No unseen-season execution is indicated by the bundle, but access outside this archive cannot be globally proven.

## Why v0.3.22 targeted shadow exists

Of 35 unconfirmed v0.3.21 decisions:
- **25** were separated at the 1024 checkpoint but did not receive two consecutive resolved checkpoints.
- **10** remained statistically unseparated at 1024.
- All 35 retained the same leader between 512 and 1024.

A new **development-only 2048 shadow pilot** is being prepared on
\`dev/v0322-2048-shadow\`. It replays all original 1→1024 checkpoints exactly, then extends the same decision to 2048. It does NOT change the draft trajectory or user-facing recommendation.

The experiment is defined by:
\`docs/decision-engine/V0322-2048-SHADOW-RESEARCH-PROTOCOL.md\`.

## Local run step

Download branch \`dev/v0322-2048-shadow\`, extract it on Windows, double-click
\`RUN-V0322-SHADOW.cmd\` and point the launcher to the original
\`v0321-full-development-results-bundle.zip\` (not the GitHub repository ZIP).

The launcher verifies the source manifest and all 12 seats, runs local tests and prior frozen equivalence certification, then replays the 35 decisions.

Expected output:
\`historical/local_runs/v0322-shadow-2048/v0322-shadow-2048-results-bundle.zip\`

Upload this ZIP to the Decision Engine development chat for audit.

## Unseen-season protection

- 2024-25: VALIDATION_UNTOUCHED
- 2025-26: HOLDOUT_UNTOUCHED

The 15% unconfirmed gate, paired comparison methodology, practical-tie treatment, and two-consecutive-confirmation requirement remain unchanged.

**No unseen season may be opened until a separately frozen full 12-seat development rerun passes all existing checks.**

Do not use GitHub Actions for Monte Carlo compute.
