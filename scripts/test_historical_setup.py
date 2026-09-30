#!/usr/bin/env python3
import json
from pathlib import Path
import run_historical_development as h
from decision_engine.validation import validate, canonical_hash

root=Path("runtime_v0317")
season="20222023"
meta=h.SEASON_META[season]
schedule=h.load_schedule(root/f"data/historical/{season}/schedule.csv")
calendar=h.build_calendar(schedule)
players,excluded=h.load_preseason(root/meta["preseason"],calendar,season)
pre=h.make_snapshot(season,players,calendar,meta["cutoff"],draft_order=h.snake_order(19))
validate(pre)
keepers=h.generate_b1_keepers(pre,players)
live=h.make_snapshot(season,players,calendar,meta["cutoff"],initial_rosters=keepers,draft_order=h.snake_order(12))
world=h.orient_world(live,keepers)
reference,trace=h.build_reference_field(world)
final=h.make_snapshot(season,players,calendar,meta["cutoff"],initial_rosters=keepers,reference_field=reference,draft_order=h.snake_order(12))
validate(final)
print(json.dumps({
 "status":"OK",
 "players":len(players),
 "excluded":excluded,
 "weeks":len(calendar["weeks"]),
 "keepers_per_team":{t:len(v) for t,v in keepers.items()},
 "reference_rows":len(reference),
 "reference_trace_rows":len(trace),
 "snapshot_sha256":canonical_hash(final)
},indent=2))
