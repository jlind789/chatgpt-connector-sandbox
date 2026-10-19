#!/usr/bin/env python3
"""Synthetic contract tests only; do not interpret as a completed historical replay."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPTS=Path(__file__).resolve().parent

def module(name):
    spec=importlib.util.spec_from_file_location(name,SCRIPTS/(name+".py"))
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

class ShadowSafetyTests(unittest.TestCase):
    def test_stage_reproduction_fail_closed(self):
        m=module("diagnose_v0321_shadow")
        row=dict(budget=512,leader="A",candidate_count=3,separated=True,
                 stable_streak=2,resolved_under_rule=True,confirmed=False,
                 worst_ci95_high_points=-0.05,runner_up_gap_points=0.20,runner_up="B")
        m.stage_compare(row,dict(row))
        with self.assertRaises(RuntimeError):
            m.stage_compare(row,dict(row,leader="C"))
        with self.assertRaises(RuntimeError):
            m.stage_compare(row,dict(row,worst_ci95_high_points=0.03))

    def test_trace_validation(self):
        m=module("diagnose_v0321_shadow")
        world={"team_ids":["t1","t2"],"initial_rosters":{"t1":[],"t2":[]},
               "draft_order":["t1","t2"]}
        trace=[dict(pick_index=0,team_id="t1",player_id="A"),
               dict(pick_index=1,team_id="t2",player_id="B")]
        self.assertEqual(m.replay_rosters(world,trace,1),{"t1":["A"],"t2":[]})
        with self.assertRaises(RuntimeError):
            m.replay_rosters(world,[dict(trace[0],team_id="t2"),trace[1]],1)

    def test_checksum_integrity(self):
        m=module("diagnose_v0321_shadow")
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"test.zip"
            checks={f"file{i}":hashlib.sha256(str(i).encode()).hexdigest()
                    for i in range(29)}
            with zipfile.ZipFile(p,"w") as z:
                z.writestr("BUNDLE-CHECKSUMS.json",json.dumps(checks))
                for i in range(29):
                    z.writestr(f"file{i}",str(i))
            with zipfile.ZipFile(p) as z:
                m.check_bundle(z)
            checks["file0"]="tampered"
            with zipfile.ZipFile(p,"a") as z:
                z.writestr("BUNDLE-CHECKSUMS.json",json.dumps(checks))
            with zipfile.ZipFile(p) as z:
                with self.assertRaises(RuntimeError):m.check_bundle(z)

    def test_outcome_data_not_used(self):
        source=(SCRIPTS/"diagnose_v0321_shadow.py").read_text()
        self.assertNotIn("load_outcomes(",source)
        self.assertNotIn("realized_focus_score(",source)

if __name__ == "__main__":
    unittest.main()
