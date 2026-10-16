# Decision Engine — Tie Presentation Contract v0.1

Status: pre-registered design decision made before 2024-25 validation or 2025-26 holdout is opened.

## Principle

If the Decision Engine cannot establish a practically meaningful difference between two or more players under the frozen methodology, it must present them as tied.

The product must not invent a subjective tiebreaker merely to force a single recommendation.

## User-facing behavior

### Confirmed edge

When one player satisfies the frozen `CONFIRMED_EDGE` rule, the engine may present that player as the preferred recommendation and may show the strength/evidence for the edge.

### Practical tie

When the terminal state is `PRACTICAL_TIE_MAX_BUDGET`:

- all players that remain inside the declared practical-equivalence band should be presented as co-equal options;
- no rank order, winner badge, "best" label, or hidden preference is assigned within the tie group;
- the interface should state that the model does not find a meaningful difference between them at the current decision state;
- user-visible ordering must be non-evaluative, such as alphabetical/display-name order;
- positional flexibility, scarcity, category profile, age, personal preference, roster fit, or any other factor may be shown descriptively, but none may be silently converted into a tiebreaker unless that factor is explicitly part of a separately frozen methodology.

Recommended wording:

> **Practical tie:** The model does not find a meaningful difference between these options.

### Unconfirmed edge

For `UNCONFIRMED_EDGE_MAX_BUDGET`, the engine may show the current mean leader but must not present it as confirmed. The interface should distinguish "current estimate" from "confirmed edge."

Recommended wording:

> **Edge not confirmed:** One option currently leads, but the evidence is not strong enough to call the difference reliable.

## Counterfactual/backtest continuation

Historical simulation sometimes requires exactly one player to be selected so the counterfactual draft can continue.

That mechanical continuation is not a user-facing tiebreaker.

For a practical tie or unconfirmed edge:

- choose the frozen deterministic continuation rule already defined by the protocol;
- retain the true confidence/status label;
- record that the selected player was a simulation continuation action rather than a confirmed recommendation;
- never use the continuation choice to infer that the engine preferred that player in a practical tie.

## Multi-player ties

The implementation should not assume ties can only contain two players.

A practical-equivalence set should contain every surviving candidate whose terminal modeled difference from the best current mean is within the frozen practical threshold, subject to the exact statistical/action definition frozen for that version.

The public surface may summarize a large equivalence set, but it must not create a ranking inside it.

## Governance

Changing this contract requires a new methodology/product-contract version.

Validation or holdout outcomes may not be used to invent a tiebreaker after the fact.
