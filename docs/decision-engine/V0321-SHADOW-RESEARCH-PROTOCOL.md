# v0.3.21 development-only shadow extension — frozen experimental plan

Status: experiment protocol written before any 1,024-rollout shadow outcomes are observed.
Created after the independently audited v0.3.20 development result (53/228 unconfirmed edges).

## Why this study exists

v0.3.20 passed all four preregistered performance checks but failed the unconfirmed-edge ceiling: 53/228 = 23.25%, versus the existing maximum of 15%.

All 53 unconfirmed decisions stopped at 512 paired rollouts. Of these:
- 30 were statistically separated at the final checkpoint but not the preceding checkpoint;
- 23 remained statistically unseparated at the final checkpoint;
- 50 had the same leading player at 256 and 512, while 3 changed leader;
- among the 30 that were separated at 512, all retained the same leader since 256, but separation was insufficient at 256.

The frozen rule requires **two consecutive separated checkpoints with the same leader**.
It would be invalid to retrospectively call the 30 separated-at-512 decisions confirmed.

## Experimental population and design

Only development seasons 2022-23 and 2023-24.

Population: exactly the 53 v0.3.20 decisions classified `UNCONFIRMED_EDGE_MAX_BUDGET`.

For each:
1. Read its original 12-seat result from the supplied v0.3.20 bundle and verify bundle SHA-256 checksums.
2. Reconstruct the roster and draft state from the exact original trace prefix.
3. Re-run the unchanged frozen 1,2,4,...,512 rollout sequence, comparing every original stage, leader, candidate count, statistical-separation result, stability streak, confirmation state, runner-up identity and numerical gap.
4. Fail closed immediately if any reproduced source checkpoint differs.
5. Continue the same paired-runner and survivor-pruning state from 512 to **1024** rollouts.
6. Report whether the original decision would now show a confirmed edge, a practical tie, or remain unconfirmed; report any leader changes.

No realized player-game outcomes are loaded by the shadow replay. This is a convergence-policy study, not a new historical performance backtest.

## Fixed parameters

- Underlying M0.3 model unchanged.
- epsilon = 0.10 utility points.
- paired-row statistic, 95% CI, runner, seeds and candidate-pruning policy unchanged.
- two consecutive resolved checkpoints with the same leader remains the confirmation rule.
- broad-pool and survivor minimum/maximum unchanged.
- first pass full pool at budget 1 unchanged.
- only the new optional diagnostic checkpoint 1024 is studied.
- diagnostic output does **not** change what v0.3.20 recommended at its original draft state.

No subjective tie breaking. Practical ties remain co-equal conclusions. The original deterministic continuation action is NOT elevated into a confirmed recommendation.

## Measures reported

- 53/53 source-checkpoint reproducibility;
- after-1024 confirmed/practical-tie/unconfirmed counts;
- status changes separately for the 30 source-separated and 23 source-unseparated decisions;
- leading-player changes between 512 and 1024;
- time and provenance of each replay;
- possible unconfirmed-count reduction relative to source, for **shadow population only**.

At a fixed 228 decisions, getting below 15% would require reducing unconfirmed to at most 34, i.e., at least 19 reductions.

**Do not treat this shadow target as the promotion gate.** New picks can change later draft states; all 80 existing practical ties also need to be recomputed if a 1024 maximum becomes the methodology. A separate full 12-seat v0.3.21 development rerun would be required.

## Decision after the pilot

If 1024 gives materially useful improvement and remains computationally feasible, prepare and freeze a **new** v0.3.21 full-development protocol, with strict outcome-load-after-draft-commit separation, restart equivalence, and no changes to the prior promotion criteria. Then run a new 12-seat development study locally.

If 1024 is insufficient or reveals leader instability, continue research on development seasons only. 2048 is reserved as an explicitly separate experiment, not silently introduced.

The pilot itself cannot promote the model or unlock either unseen season.

## Provenance and safeguard

The GitHub branch is `dev/v0321-shadow-research`.

Research is local-only. Do not enable or dispatch an Actions simulation matrix.

Keep 2024-25 validation untouched and 2025-26 holdout untouched.

The pre-registered gate from `docs/decision-engine/PROMOTION-GATE-v0.3.20.md` remains unchanged.
