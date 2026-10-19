# Full-development v0.3.21 implementation prerequisites (before any unseen season)

This checklist governs a potential future **full** v0.3.21 development rerun. It is not a claim that v0.3.21 has passed any gate.

## 1. Recommendation math

If the 1024 shadow pilot justifies proceeding:
- clone the frozen M0.3 v0.3.20 recommendation mathematics;
- introduce only the explicit higher evidence ceiling (1024) under a new frozen v0.3.21 protocol;
- keep epsilon 0.10 and two-consecutive-resolved-same-leader confirmation unchanged;
- keep B3 continuation, baseline comparators, league rules, exposure assumptions and pruning settings unchanged;
- treat genuine practical ties as co-equal, without an invented tiebreaker;
- record any extra compute cost before scheduling all 12 seats.

Do not selectively extend only promising decisions in a full backtest: run the same protocol for every draft state. A targeted shadow replay is purely diagnostic.

## 2. Strong outcome-blinding boundary

The v0.3.20 research runner loaded realized outcomes before drafting, although it did not feed them into the picking function.

The successor must enforce a stronger boundary through **separate processes**:

**Phase A — commit the drafts**
- may load only preseason projections, frozen schedule, league rules, draft state and historical world generation inputs;
- runs B1, B2, B3 and Decision Engine drafting;
- writes all final rosters, full pick traces, statuses, protocol hashes and provenance to an immutable commitments artifact;
- computes a SHA-256 of that artifact;
- must not call `load_outcomes`, `realized_focus_score`, or read realized `player_game_stats.csv`.

**Phase B — evaluate**
- starts in a fresh Python process after the Phase A commitment artifact exists on disk;
- verifies Phase A artifact hash and declared protocol/code/world identities;
- only now loads outcome IDs and realized player-game stats;
- evaluates all four committed rosters under the common frozen daily lineup manager;
- outputs realized category metrics and joins them to the committed traces without changing Phase A records.

An interrupted Phase A can resume from checkpoints; Phase B must refuse incomplete/invalid Phase A files. Phase B must not generate new draft picks.

## 3. Restart-equivalence certification

Before full 12-seat v0.3.21 research:
- run one representative development fixture uninterrupted;
- interrupt a second execution after at least one completed Engine decision;
- resume using checkpointing;
- compare all pick IDs, order, decisions, stages, values, statuses and final committed rosters under the established canonical numerical tolerance;
- ignore only execution timing fields;
- fail closed on changed runner/protocol/world/snapshot hashes.

## 4. Reproduction check

Confirm B1/B2/B3 results reproduce v0.3.18 and v0.3.20 for the identical development worlds.

Separately, reproduce a frozen v0.3.20 focus draft under the unchanged 512 ceiling before accepting the larger ceiling implementation.

## 5. Full development gate

Only after the 12-seat successor rerun completes may the unchanged development criteria be applied, including:

- 12/12 complete;
- all source/roster integrity requirements;
- decision-status accounting;
- no silent tie or uncertainty upgrades;
- `UNCONFIRMED_EDGE` <= 15%;
- aggregate Engine score > B2 and >= B1/B3;
- positive median E-B2;
- >= 7/12 E-B2 positive seats;
- neither development season trails B2 by >1.0 percentage point.

No 2024-25 validation or 2025-26 holdout input/outcome file may be opened until the development gate passes.

## 6. Data storage and local compute

Heavy workloads remain on the owner's Windows computer. Do not recreate GitHub Actions simulation matrices.

Store small, reviewable code, protocol, gates, summaries and reproducibility evidence in GitHub.

Keep bulk local checkpoints and large run artifacts on the local drive until independent audit.

## 7. Present state

As of this planning document:
- v0.3.20 is complete and failed its 15% confidence gate;
- v0.3.21 shadow replay code is staged on this development branch;
- no 1024 shadow results have been observed;
- no full v0.3.21 12-seat run has been launched;
- both unseen seasons remain untouched.
