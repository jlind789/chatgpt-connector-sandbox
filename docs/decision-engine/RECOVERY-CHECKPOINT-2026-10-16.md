# Decision Engine recovery checkpoint — 2026-10-16

## Current state

GitHub account access has been restored.

A safety branch was created before any recovery changes:

- `recovery-backup-2026-10-16`
- points to pre-recovery HEAD `e15d61809e94bd17a16c4fcfe4f2a2bca9b5ef61`

The interrupted v0.3.20 GitHub Actions run was inspected:

- workflow run: `36819132832`
- name: `Run v0.3.20 historical development backtests`
- conclusion: `cancelled`
- all 12 seat jobs were cancelled
- no workflow artifacts were retained
- no v0.3.20 aggregate-results commit exists

Therefore v0.3.20 development must be rerun.

## Authoritative completed evidence

- v0.3.18 historical development results:
  `6694f7c59883c8933ea613536af83dafc247eb43`
- v0.3.19 convergence calibration results:
  `de3a6351fd06ba60b9ab774f14c5584536c6ce86`
- v0.3.19 resolved-decision stability audit:
  `c61fc294447cbaee117a7d93b9b35ce03963ef55`
- v0.3.20 protocol freeze:
  `d732bbf3af9a2dab502628962431e918cd8a8b44`
- v0.3.20 seat runner:
  `9abe885a300da3765ad3544d7bb5c3a51677dd8d`
- v0.3.20 aggregator:
  `8760634ec7b2586d0383274d2c2f297f6ebc9bcd`
- cancelled v0.3.20 workflow launch:
  `e15d61809e94bd17a16c4fcfe4f2a2bca9b5ef61`

## Frozen v0.3.20 decision policy

Underlying M0.3 scoring/recommendation mathematics remain unchanged.

Evidence budgets:
`8 -> 16 -> 32 -> 64 -> 128 -> 256 -> 512`

Unique edge confirmation:
`TWO_CONSECUTIVE_RESOLVED_CHECKPOINTS_SAME_LEADER`

Terminal statuses:
- `CONFIRMED_EDGE`
- `PRACTICAL_TIE_MAX_BUDGET`
- `UNCONFIRMED_EDGE_MAX_BUDGET`

Practical threshold:
- 0.10 utility points

Practical ties are genuine co-equal model conclusions. No subjective or hidden tiebreaker is allowed.

A deterministic player may be used only to continue a historical counterfactual draft; that is not a unique recommendation.

## Unseen-season governance

2024-25 remains:
`VALIDATION_UNTOUCHED`

2025-26 remains:
`HOLDOUT_UNTOUCHED`

Do not open either season until:

1. local compute is integrated and certified against frozen development evidence;
2. v0.3.20 development rerun completes;
3. the pre-registered v0.3.20 development gate passes;
4. exact validation code/config/protocol hashes are frozen.

No methodology tuning is permitted after unseen-season results are opened.

## Compute policy after reinstatement

Do not use GitHub-hosted Actions as the main historical/Monte-Carlo compute cluster again.

GitHub should be used for:
- source control
- modest CI
- unit/syntax tests
- deterministic smoke tests
- release verification

Heavy development/validation/holdout runs should execute locally with:
- managed/pinned Python environment
- job-level parallelism
- single-threaded BLAS/OpenMP per worker
- resumable/atomic jobs
- per-decision checkpointing where supported
- provenance hashes
- deterministic aggregation

## Exact next step

Integrate the prepared local research harness/checkpoint/equivalence design into this repository using development data only.

Then certify local output against a known frozen development result before rerunning v0.3.20 locally.

Do not rerun the cancelled 12-seat v0.3.20 workload on GitHub-hosted Actions.
