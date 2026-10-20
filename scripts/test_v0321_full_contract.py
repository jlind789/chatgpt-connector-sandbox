#!/usr/bin/env python3
"""Fast structural/regression checks; real scientific parity requires the local run."""
from __future__ import annotations
import json
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/"scripts"

class V0321FullDevelopmentContract(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.proto=json.loads((ROOT/"historical/protocol-v0.3.21-development-freeze.json").read_text())
        cls.old=json.loads((ROOT/"historical/protocol-v0.3.20-development-freeze.json").read_text())

    def test_frozen_change_is_ceiling_only(self):
        p=self.proto["decision_engine"]["adaptive_convergence"]
        old=self.old["decision_engine"]["adaptive_convergence"]
        self.assertEqual(p["maximum_rollouts"],1024)
        self.assertEqual(old["maximum_rollouts"],512)
        self.assertEqual(p["doubling_budgets"],old["doubling_budgets"]+[1024])
        for k in old:
            if k not in ("maximum_rollouts","doubling_budgets"):
                self.assertEqual(p[k],old[k],k)
        self.assertEqual(self.proto["decision_engine"]["timing_value"],0)
        self.assertEqual(self.proto["decision_engine"]["continuation_policy"],"B3")

    def test_no_unseen_seasons_or_outcome_read_during_commit(self):
        self.assertEqual(self.proto["development_seasons"],["2022-23","2023-24"])
        self.assertEqual(self.proto["season_roles"]["2024-25"],"VALIDATION_UNTOUCHED")
        self.assertEqual(self.proto["season_roles"]["2025-26"],"HOLDOUT_UNTOUCHED")
        text=(SCRIPTS/"run_v0321_commit_seat.py").read_text()
        self.assertNotIn("base.load_outcomes(",text)
        self.assertNotIn("base.realized_focus_score(",text)
        self.assertNotIn('outcome_id_map.json").read_text',text)
        self.assertIn("checkpointed.engine_draft_checkpointed(",text)

    def test_phase_b_has_only_commitment_scoring(self):
        text=(SCRIPTS/"run_v0321_evaluate_seat.py").read_text()
        self.assertIn("source_commitment_sha256",text)
        self.assertIn("base.load_outcomes(",text)
        self.assertIn("base.realized_focus_score(",text)
        self.assertNotIn("base.baseline_draft(",text)
        self.assertNotIn("base.adaptive_pick(",text)
        self.assertNotIn("base.engine_draft(",text)
        self.assertIn('Phase A draft commitment',text)

    def test_full_seat_count_and_phase_barrier(self):
        script=(SCRIPTS/"run_v0321_full_local.py").read_text()
        self.assertIn('JOBS=[(s,i) for s in ("2022-23","2023-24") for i in range(1,7)]',script)
        p1=script.index('phase_run("COMMIT"')
        p2=script.index('write(index_path,')
        p3=script.index('phase_run("EVALUATE"')
        self.assertLess(p1,p2)
        self.assertLess(p2,p3)
        self.assertIn("if old.get(key)!=provenance[key]",script)

    def test_original_gate_retained(self):
        script=(SCRIPTS/"aggregate_v0321_full.py").read_text()
        self.assertIn("rate<=0.15",script)
        self.assertIn("sum(x>0 for x in deltas[\"B2\"])>=7",script)
        self.assertIn("statistics.median(deltas[\"B2\"])>0",script)
        self.assertIn('abs(scores[p]-reference["scores"][p])<=1e-12',script)
        self.assertIn("development_season_below_B2",script)
        self.assertIn("gate_report.json",script)

if __name__=="__main__":
    unittest.main()
