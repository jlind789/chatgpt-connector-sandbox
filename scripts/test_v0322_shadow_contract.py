#!/usr/bin/env python3
"""Fast integrity and protocol-boundary tests (not a scientific replay)."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/"scripts"

def import_module(name):
    spec=importlib.util.spec_from_file_location(name,SCRIPTS/(name+".py"))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class Shadow2048Tests(unittest.TestCase):

    def test_reproduction_fails_closed(self):
        m=import_module("diagnose_v0322_shadow_2048")
        stage={
            "budget":1024,"leader":"A","candidate_count":4,
            "separated":True,"stable_streak":5,
            "resolved_under_rule":True,"confirmed":False,
            "runner_up":"B","worst_ci95_high_points":-0.02,
            "runner_up_gap_points":0.15,
        }
        m.compare_stage(stage,dict(stage))
        with self.assertRaises(RuntimeError):
            m.compare_stage(stage,dict(stage,leader="C"))
        with self.assertRaises(RuntimeError):
            m.compare_stage(stage,dict(stage,runner_up_gap_points=0.151))
        with self.assertRaises(RuntimeError):
            m.compare_stage(stage,dict(stage,confirmed=True))

    def test_prefix_trace_identity(self):
        m=import_module("diagnose_v0322_shadow_2048")
        w={
            "team_ids":["team1","team2"],
            "initial_rosters":{"team1":[],"team2":[]},
            "draft_order":["team1","team2"],
        }
        trace=[
            {"pick_index":0,"team_id":"team1","player_id":"p1"},
            {"pick_index":1,"team_id":"team2","player_id":"p2"},
        ]
        self.assertEqual(m.prefix_rosters(w,trace,1),
                         {"team1":["p1"],"team2":[]})
        with self.assertRaises(RuntimeError):
            m.prefix_rosters(w,[dict(trace[0],team_id="team2"),trace[1]],1)

    def test_no_unseen_data_or_historical_outcome_load(self):
        proto=json.loads((ROOT/"historical/protocol-v0.3.21-development-freeze.json").read_text())
        self.assertEqual(proto["season_roles"]["2024-25"],"VALIDATION_UNTOUCHED")
        self.assertEqual(proto["season_roles"]["2025-26"],"HOLDOUT_UNTOUCHED")
        self.assertEqual(proto["decision_engine"]["adaptive_convergence"]["maximum_rollouts"],1024)
        runner=(SCRIPTS/"diagnose_v0322_shadow_2048.py").read_text()
        self.assertNotIn("load_outcomes(",runner)
        self.assertNotIn("realized_focus_score(",runner)
        self.assertIn("compare_stage(stage, expected_by_budget[budget])",runner)

    def test_exact_population_and_threshold_reporting(self):
        agg=(SCRIPTS/"aggregate_v0322_shadow.py").read_text()
        self.assertIn("if len(rows)!=35:",agg)
        self.assertIn('"original_full_development_decisions":228',agg)
        self.assertIn('DEVELOPMENT_DIAGNOSTIC_ONLY_NOT_MODEL_VALIDATION',agg)
        self.assertIn('shadow_remaining_unconfirmed',agg)

    def test_atomic_checkpoint_and_safety(self):
        m=import_module("diagnose_v0322_shadow_2048")
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"nested"/"test.json"
            m.write_json(p,{"hello":"world"})
            self.assertEqual(json.loads(p.read_text()),{"hello":"world"})
            self.assertFalse((p.with_name(p.name+".tmp")).exists())
        runner=(SCRIPTS/"run_v0322_shadow_local.py").read_text()
        self.assertIn('env["PYTHONHASHSEED"]="0"',runner)
        self.assertIn("Source checksum mismatch",runner)
        self.assertIn("Changed research provenance",runner)

if __name__=="__main__":
    unittest.main()
