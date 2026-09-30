from __future__ import annotations

"""Frozen pre-historical-backtest decision protocol for v0.3.16.

This module does not alter candidate utilities or convergence. It converts an
adaptive-race result into a deterministic action required by a complete draft
simulation while preserving the original numerical-confidence status.
"""

RESOLVED = "RESOLVED_WITHIN_ADAPTIVE_RACE"
UNRESOLVED = "UNRESOLVED_MAX_BUDGET"


def historical_action(row: dict) -> dict:
    """Return the action used by historical complete-draft simulations.

    Rules frozen before historical outcomes are viewed:
    * resolved -> final adaptive leader;
    * unresolved-at-ceiling -> final-budget mean leader, but confidence remains
      UNRESOLVED and the action is explicitly labelled a fallback;
    * any other status is not silently forced into an action.
    """
    status = row.get("status")
    leader = row.get("final_leader_id") or row.get("final_leader_player_id") or row.get("final_leader")
    name = row.get("final_leader_name")
    if status == RESOLVED:
        if not leader:
            raise ValueError("resolved row missing final leader id")
        return {
            "action_player_id": leader,
            "action_player_name": name,
            "action_basis": "RESOLVED_ADAPTIVE_LEADER",
            "confidence_status": RESOLVED,
            "fallback_used": False,
        }
    if status == UNRESOLVED:
        if not leader:
            raise ValueError("unresolved row missing final-budget mean leader id")
        return {
            "action_player_id": leader,
            "action_player_name": name,
            "action_basis": "UNRESOLVED_FINAL_BUDGET_MEAN_LEADER_FALLBACK",
            "confidence_status": UNRESOLVED,
            "fallback_used": True,
        }
    raise ValueError(f"unsupported convergence status: {status!r}")


def outcome_cohort(confidence_status: str) -> str:
    if confidence_status == RESOLVED:
        return "RESOLVED"
    if confidence_status == UNRESOLVED:
        return "UNRESOLVED"
    raise ValueError(f"unsupported confidence status: {confidence_status!r}")


def reporting_contract() -> dict:
    """Machine-readable minimum reporting contract for historical studies."""
    return {
        "required_cohorts": ["ALL", "RESOLVED", "UNRESOLVED"],
        "resolved_and_unresolved_must_never_be_pooled_without_all_cohort": True,
        "unresolved_label_must_survive_action_selection": True,
        "outcomes_may_not_change_status_or_fallback_action": True,
        "primary_comparisons_use_same_information_cutoff_and_legality_guard": True,
        "actual_human_pick_is_not_ground_truth": True,
        "legacy_lab_advice_is_separate_when_available": True,
    }
