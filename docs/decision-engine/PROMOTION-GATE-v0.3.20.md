# Decision Engine v0.3.20 — Pre-registered Development / Validation / Holdout Gates

Status: draft frozen in conversation before v0.3.20 development results are available. Commit unchanged after repository access returns, unless a mechanical correction is required and documented before inspecting v0.3.20 outcome results.

## Primary comparator

B2 is the primary fixed baseline because it had the highest combined mean category share among B1/B2/B3 in the completed v0.3.18 development study. B1 and B3 remain mandatory secondary comparators.

## A. v0.3.20 development acceptance gate

The purpose is to decide whether the revised convergence/action policy is coherent enough to freeze before opening validation. Development seasons remain 2022-23 and 2023-24 only.

### Integrity requirements — all mandatory

1. All 12 Engine seat drafts complete without unsupported/error states.
2. B1/B2/B3 reproduce the v0.3.18 development baseline outputs for the same frozen worlds, subject only to canonical serialization differences.
3. Recommendation snapshots remain outcome-blind; realized outcomes are used only after draft commitments for evaluation.
4. Every Engine decision is reported as exactly one of `CONFIRMED_EDGE`, `PRACTICAL_TIE_MAX_BUDGET`, or `UNCONFIRMED_EDGE_MAX_BUDGET`.
5. 2024-25 validation and 2025-26 holdout remain unopened.

### Confidence/action requirements

6. `UNCONFIRMED_EDGE_MAX_BUDGET` must be <= 15% of all Engine decisions across the two development seasons combined.
7. Practical ties are not counted as failures. They are an explicit model conclusion that the observed difference is not practically material at the 0.10-point threshold. Tied options are presented as co-equal; no subjective or hidden tiebreaker is permitted.
8. No silent conversion of a practical tie or unconfirmed edge into a confidence claim is permitted. A deterministic continuation player may be used only to keep a counterfactual simulation executable; it is not a preference within a tie and must retain its true status.

### Outcome requirements

9. Across the 12 development seats combined, Decision Engine mean category share must exceed B2 and must not trail B1 or B3.
10. Median seat-level Decision Engine minus B2 category-share difference must be > 0.
11. At least 7 of 12 development seats must have a positive Decision Engine minus B2 difference.
12. Neither development season may show Decision Engine aggregate category share more than 1.0 percentage point below B2.

If any integrity requirement fails, do not open validation. If confidence/action requirements fail, continue convergence-policy development. If only outcome requirements fail, investigate whether the revised action policy harmed downstream drafting before deciding on another development iteration.

## B. 2024-25 validation gate

Freeze the entire recommendation/action methodology before loading 2024-25 outcomes. No methodology tuning is allowed after validation results are viewed.

All of the following are required to open the holdout:

1. All six validation seats complete cleanly.
2. Decision Engine aggregate mean category share exceeds B1, B2, and B3 for 2024-25.
3. Median seat-level Decision Engine minus B2 difference is > 0.
4. At least 4 of 6 seats have a positive Decision Engine minus B2 difference.
5. `UNCONFIRMED_EDGE_MAX_BUDGET` is <= 15% of Engine decisions.
6. No validation-specific exception, rule change, threshold change, player-data correction informed by outcomes, or fallback reinterpretation is introduced.

A failed validation gate ends promotion of that frozen methodology. Validation may be used diagnostically only if the version is retired; it cannot be tuned and then re-labelled as validation.

## C. 2025-26 holdout gate

The validation-frozen methodology must be applied unchanged to 2025-26.

Historical validation is considered successful only if all of the following hold:

1. All six holdout seats complete cleanly.
2. Decision Engine aggregate mean category share exceeds B1, B2, and B3 in 2025-26.
3. Median seat-level Decision Engine minus B2 difference is > 0.
4. At least 4 of 6 holdout seats have a positive Decision Engine minus B2 difference.
5. Across the combined 12 unseen validation+holdout seats, both mean and median Decision Engine minus B2 differences are > 0.
6. `UNCONFIRMED_EDGE_MAX_BUDGET` remains <= 15% across the combined unseen decisions.
7. No methodology change occurs between validation and holdout.

Passing this gate supports the claim that the frozen recommendation methodology has positive historical evidence against the fixed comparators in the declared benchmark. It does not imply universal superiority across all fantasy formats or future seasons.
