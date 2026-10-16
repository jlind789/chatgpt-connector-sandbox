# Decision Engine — Validation & Holdout Reporting Specification v0.1

Status: pre-registered before 2024-25 validation and 2025-26 holdout are opened.

Purpose: define exactly what will be generated, inspected, and used for pass/fail decisions so that reporting choices are not selected after seeing unseen-season results.

## 1. Immutable evaluation population

Validation:
- Season: 2024-25
- Six snake-draft focus seats
- Same declared benchmark league structure and fixed B1/B2/B3 comparators
- Frozen Decision Engine methodology entering validation

Holdout:
- Season: 2025-26
- Six snake-draft focus seats
- Same frozen methodology used in validation, unchanged

No seat may be discarded because its result is inconvenient. Unsupported/error states are reported as failures, not silently omitted.

## 2. Primary outcome

Primary metric:

`MEAN_WEEKLY_H2H_CATEGORY_SHARE_VS_OTHER_FIVE_DRAFTED_TEAMS`

For each policy and each seat, report the full-season mean weekly category share produced under the common realized-outcome evaluation policy.

Primary comparator for pre-registered pass/fail tests: **B2**.

B1 and B3 remain mandatory secondary comparators and must also be beaten in aggregate where required by the promotion gate.

## 3. Required top-line table

For each unseen season, generate:

| Strategy | Mean category share | Median seat share | Min seat share | Max seat share |
|---|---:|---:|---:|---:|
| B1 | | | | |
| B2 | | | | |
| B3 | | | | |
| Decision Engine | | | | |

No ranking language beyond the raw values is required.

## 4. Required seat-level table

One row per seat:

| Seat | B1 | B2 | B3 | Engine | E-B1 pp | E-B2 pp | E-B3 pp | Complete? |
|---:|---:|---:|---:|---:|---:|---:|---:|---|

Report all six seats, including failures.

The pre-registered median and positive-seat tests use `E-B2 pp`.

## 5. Required aggregate comparator statistics

For each baseline B1/B2/B3, report:

- mean Engine-minus-baseline percentage-point difference;
- median difference;
- minimum and maximum difference;
- number of positive seats;
- number of negative seats;
- number of exact-zero seats.

For B2 specifically, also report:
- sign-test style count `positive / 6` for the season;
- combined validation+holdout `positive / 12` after holdout;
- mean and median across the combined 12 unseen seats.

These are descriptive/pre-registered gate statistics, not claims of formal population-level significance.

## 6. Decision-status reporting

Every Decision Engine decision must appear in exactly one status cohort:

- `CONFIRMED_EDGE`
- `PRACTICAL_TIE_MAX_BUDGET`
- `UNCONFIRMED_EDGE_MAX_BUDGET`

For each season report:

| Status | Count | Rate |
|---|---:|---:|
| Confirmed edge | | |
| Practical tie | | |
| Unconfirmed edge | | |

Also report:
- terminal budget histogram;
- confirmation-budget histogram for confirmed edges;
- number/rate reaching 512;
- number of practical ties involving 2, 3, 4, or more equivalent candidates if equivalence-set reporting is implemented.

A practical tie is not a failure.

## 7. Tie reporting

Practical ties must follow `TIE-PRESENTATION-CONTRACT-v0.1.md`.

For research reporting, record:
- all tied/equivalent player IDs;
- terminal modeled gaps to the current best mean;
- deterministic continuation player used for the counterfactual draft;
- explicit marker that the continuation action is not a preference inside the tie.

Do not score a deterministic continuation choice as though it were a uniquely recommended player.

## 8. Category diagnostics

For each strategy report aggregate realized credit for all frozen categories:

- G
- A
- FOW
- SOG
- HIT
- BLK
- DEF
- W
- SVS
- SO
- SV%

Also report Engine-minus-B2 category-credit differences.

These are diagnostics only. They cannot override a failed pre-registered primary gate.

## 9. Draft-completion and integrity diagnostics

Required counts:

- completed focus drafts;
- `NO_FEASIBLE_PICK`;
- unsupported-capability states;
- resource-limit states;
- invalid roster/completion states;
- missing/corrupt source data;
- outcome-identity coverage diagnostics;
- any mechanical retry/restart count;
- any seat whose canonical rerun differs from its first valid completed output.

Any integrity failure must be surfaced separately from performance.

## 10. Runtime and compute diagnostics

Report:

- elapsed time per seat;
- elapsed time per Engine draft;
- mean/median/p95 Engine decision runtime if available;
- total rollouts evaluated;
- terminal budget distribution;
- local worker count and runtime environment provenance.

Runtime diagnostics do not affect historical promotion unless a run cannot complete reproducibly.

## 11. Required pass/fail section

The final season report must mechanically apply the frozen promotion gate and print each condition as:

- `PASS`
- `FAIL`
- `NOT APPLICABLE`

No free-form overall verdict may replace the individual checks.

### 2024-25 validation checks

1. Six seats complete cleanly.
2. Engine aggregate mean > B1.
3. Engine aggregate mean > B2.
4. Engine aggregate mean > B3.
5. Median E-B2 > 0.
6. Positive E-B2 seats >= 4/6.
7. Unconfirmed-edge rate <= 15%.
8. No post-unblinding methodology change.

Only if every required validation condition passes may 2025-26 be opened.

### 2025-26 holdout checks

1. Six seats complete cleanly.
2. Engine aggregate mean > B1.
3. Engine aggregate mean > B2.
4. Engine aggregate mean > B3.
5. Median E-B2 > 0.
6. Positive E-B2 seats >= 4/6.
7. Combined unseen 12-seat mean E-B2 > 0.
8. Combined unseen 12-seat median E-B2 > 0.
9. Combined unconfirmed-edge rate <= 15%.
10. Methodology unchanged from validation.

## 12. Required machine-readable outputs

Each unseen season should produce:

- `seat_results/<season>_seat<1-6>.json`
- `summary.json`
- `seat_comparison.csv`
- `decision_status.csv`
- `category_diagnostics.csv`
- `integrity_report.json`
- `runtime_report.json`
- `gate_report.json`

After holdout, additionally produce:

- `combined_unseen_summary.json`
- `combined_unseen_seat_comparison.csv`
- `combined_gate_report.json`

## 13. Interpretation discipline

Passing the gate supports a narrow statement:

> The frozen Decision Engine methodology showed positive historical evidence against the declared fixed comparators in the specified benchmark across the pre-registered validation and holdout seasons.

It does not establish:
- universal superiority across fantasy platforms;
- superiority under different scoring categories;
- causal value for any single player choice;
- guaranteed future performance.

Failing a gate is reported as a failure of that frozen version. The unseen season may then be used diagnostically only after that version is retired; it cannot be tuned and re-used as unseen validation for the revised version.
