#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import re
import statistics
import time
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from decision_engine.validation import validate, canonical_hash, EngineError
from decision_engine.scoring import IntrinsicModel
from decision_engine.fast_assignment import MatchingRosterSolver
from research.baselines import ReplacementAnchor, choose
from research.drafts import DraftContext, play_draft, snapshot_for
from research.independent import lineup_plan, score_matchup
from research.incremental_runner import ExactCalendarIncrementalRunner
from research.convergence import paired_rows, race_survivors, resolution
from research.historical_protocol import historical_action, RESOLVED, UNRESOLVED

RUNTIME_VERSION = "historical-development/0.3.17"
EPSILON = 0.10
MIN_RESOLUTION_BUDGET = 8
TEAMS = [f"team{i+1}" for i in range(6)]
TEAM_ALIASES = {
    "LA": "LAK", "LOS": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL",
    "WAS": "WSH", "PHX": "ARI", "MON": "MTL", "NAS": "NSH",
}
STAT_KEYS = [
    "games", "goals", "assists", "faceoff_wins", "shots", "hits", "blocks",
    "defense_points", "wins", "saves", "shutouts", "shots_against",
]

SEASON_META = {
    "20222023": {
        "label": "2022-23", "cutoff": "2022-09-28T12:00:00Z",
        "preseason": "historical-backtest-inputs-v0.3.17/2022-23/preseason_players.csv",
    },
    "20232024": {
        "label": "2023-24", "cutoff": "2023-10-07T12:00:00Z",
        "preseason": "historical-backtest-inputs-v0.3.17/2023-24/preseason_players.csv",
    },
}


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    s = s.replace("’", "'").replace("-", " ")
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_team(s: str) -> str:
    x = (s or "").strip().upper()
    return TEAM_ALIASES.get(x, x)


def fnum(v, default=None):
    try:
        if v is None or str(v).strip() == "":
            return default
        return float(v)
    except Exception:
        return default


def parse_positions(raw: str, ptype: str) -> list[str]:
    if ptype == "goalie":
        return ["G"]
    out = []
    for x in re.split(r"[,/ ]+", raw or ""):
        x = x.strip().upper()
        if not x:
            continue
        x = {"L": "LW", "R": "RW"}.get(x, x)
        if x in {"C", "LW", "RW", "D"} and x not in out:
            out.append(x)
    return out


def load_schedule(path: Path):
    rows = []
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({
                "game_id": int(r["game_id"]),
                "game_date": r["game_date"],
                "away_team": norm_team(r["away_team"]),
                "home_team": norm_team(r["home_team"]),
            })
    rows.sort(key=lambda x: (x["game_date"], x["game_id"]))
    return rows


def build_calendar(schedule):
    first = date.fromisoformat(schedule[0]["game_date"])
    last = date.fromisoformat(schedule[-1]["game_date"])
    anchor = first - timedelta(days=first.weekday())
    end = last + timedelta(days=(6 - last.weekday()))
    dates = []
    d = anchor
    while d <= end:
        dates.append(d.isoformat())
        d += timedelta(days=1)
    date_team_games = defaultdict(int)
    team_game_count = defaultdict(int)
    for g in schedule:
        for t in (g["away_team"], g["home_team"]):
            date_team_games[(g["game_date"], t)] += 1
            team_game_count[t] += 1
    week_of = {ds: f"W{((date.fromisoformat(ds)-anchor).days//7)+1:02d}" for ds in dates}
    weeks = []
    for i in range(((end-anchor).days//7)+1):
        weeks.append(f"W{i+1:02d}")
    periods = [{
        "period_id": ds,
        "lock_group_id": ds,
        "starts_at": f"{ds}T12:00:00Z",
        "evaluation_group_id": week_of[ds],
    } for ds in dates]
    groups = [{"evaluation_group_id": w, "weight": 1.0} for w in weeks]
    return {
        "anchor": anchor, "end": end, "dates": dates, "weeks": weeks,
        "week_of": week_of, "periods": periods, "groups": groups,
        "date_team_games": date_team_games, "team_game_count": dict(team_game_count),
    }


def load_preseason(path: Path, calendar, season: str):
    raw = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    players = []
    used = set()
    excluded = []
    for i, r in enumerate(raw):
        name = (r.get("player") or "").strip()
        ptype = (r.get("type") or "").strip().lower()
        team = norm_team(r.get("team"))
        elig = parse_positions(r.get("espn_positions", ""), ptype)
        if not name or not elig:
            excluded.append({"player": name, "reason": "MISSING_NAME_OR_ELIGIBILITY"})
            continue
        gp = max(0.0, fnum(r.get("GP"), 0.0) or 0.0)
        if ptype == "goalie":
            vals = {
                "games": gp,
                "wins": max(0.0, fnum(r.get("W"), 0.0) or 0.0),
                "saves": max(0.0, fnum(r.get("SVS"), 0.0) or 0.0),
                "shutouts": max(0.0, fnum(r.get("SO"), 0.0) or 0.0),
            }
            svp = fnum(r.get("SV_pct"), 0.0) or 0.0
            vals["shots_against"] = vals["saves"] / svp if svp > 0 else vals["saves"]
            for k in ("goals", "assists", "faceoff_wins", "shots", "hits", "blocks", "defense_points"):
                vals[k] = 0.0
        else:
            vals = {
                "games": gp,
                "goals": max(0.0, fnum(r.get("G"), 0.0) or 0.0),
                "assists": max(0.0, fnum(r.get("A"), 0.0) or 0.0),
                "faceoff_wins": max(0.0, fnum(r.get("FOW"), 0.0) or 0.0),
                "shots": max(0.0, fnum(r.get("SOG"), 0.0) or 0.0),
                "hits": max(0.0, fnum(r.get("HIT"), 0.0) or 0.0),
                "blocks": max(0.0, fnum(r.get("BLK"), 0.0) or 0.0),
                "wins": 0.0, "saves": 0.0, "shutouts": 0.0, "shots_against": 0.0,
            }
            d = fnum(r.get("DEF"), None)
            vals["defense_points"] = max(0.0, d if d is not None else (vals["goals"] + vals["assists"] if "D" in elig else 0.0))
        if team not in calendar["team_game_count"]:
            excluded.append({"player": name, "team": team, "reason": "NO_SCHEDULE_TEAM"})
            continue
        # Stable internal ID is based only on pre-draft identity fields.
        stem = hashlib.sha256(f"{season}|{name}|{team}|{','.join(elig)}".encode()).hexdigest()[:14]
        pid = f"h_{stem}"
        if pid in used:
            raise RuntimeError(f"Internal ID collision: {name}")
        used.add(pid)
        players.append({
            "player_id": pid, "display_name": name, "team": team,
            "type": ptype, "eligibility": elig, "season_projection": vals,
            "name_key": norm_name(name),
        })
    return players, excluded


def categories():
    sk = ["FORWARD", "DEFENDER"]
    return [
        {"category_id":"G","display_name":"G","applicable_position_group_ids":sk,"direction":"HIGHER_IS_BETTER","weight":1,"precision":6,"calculation":{"type":"COUNT","stat_key":"goals"}},
        {"category_id":"A","display_name":"A","applicable_position_group_ids":sk,"direction":"HIGHER_IS_BETTER","weight":1,"precision":6,"calculation":{"type":"COUNT","stat_key":"assists"}},
        {"category_id":"FOW","display_name":"FOW","applicable_position_group_ids":sk,"direction":"HIGHER_IS_BETTER","weight":1,"precision":6,"calculation":{"type":"COUNT","stat_key":"faceoff_wins"}},
        {"category_id":"SOG","display_name":"SOG","applicable_position_group_ids":sk,"direction":"HIGHER_IS_BETTER","weight":1,"precision":6,"calculation":{"type":"COUNT","stat_key":"shots"}},
        {"category_id":"HIT","display_name":"HIT","applicable_position_group_ids":sk,"direction":"HIGHER_IS_BETTER","weight":1,"precision":6,"calculation":{"type":"COUNT","stat_key":"hits"}},
        {"category_id":"BLK","display_name":"BLK","applicable_position_group_ids":sk,"direction":"HIGHER_IS_BETTER","weight":1,"precision":6,"calculation":{"type":"COUNT","stat_key":"blocks"}},
        {"category_id":"DEF","display_name":"DEF","applicable_position_group_ids":["DEFENDER"],"direction":"HIGHER_IS_BETTER","weight":1,"precision":6,"calculation":{"type":"COUNT","stat_key":"defense_points"}},
        {"category_id":"W","display_name":"W","applicable_position_group_ids":["GOALIE"],"direction":"HIGHER_IS_BETTER","weight":1,"precision":6,"calculation":{"type":"COUNT","stat_key":"wins"}},
        {"category_id":"SVS","display_name":"SVS","applicable_position_group_ids":["GOALIE"],"direction":"HIGHER_IS_BETTER","weight":1,"precision":6,"calculation":{"type":"COUNT","stat_key":"saves"}},
        {"category_id":"SO","display_name":"SO","applicable_position_group_ids":["GOALIE"],"direction":"HIGHER_IS_BETTER","weight":1,"precision":6,"calculation":{"type":"COUNT","stat_key":"shutouts"}},
        {"category_id":"SVPCT","display_name":"SVPCT","applicable_position_group_ids":["GOALIE"],"direction":"HIGHER_IS_BETTER","weight":1,"precision":6,"calculation":{"type":"RATIO","numerator_stat_key":"saves","denominator_stat_key":"shots_against","multiplier":1}},
    ]


def roster_slots():
    out=[]
    def add(sid,cap,positions,cls="ACTIVE",required=True):
        out.append({
            "slot_definition_id":sid,"display_name":sid,"capacity":cap,"slot_class":cls,
            "eligibility":{"mode":"POSITION_SET","position_ids":positions} if positions else {"mode":"ANY_ROSTERABLE"},
            "required_status_tags_any_of":[],"counts_toward_roster_total":True,
            "required_at_completion":required if cls=="ACTIVE" else False,
        })
    add("C",2,["C"]); add("LW",2,["LW"]); add("RW",2,["RW"]); add("D",4,["D"])
    add("G",2,["G"]); add("UTIL",2,["C","LW","RW","D"]); add("BN",5,[],"BENCH",False)
    return out


def player_period_stats(players, calendar):
    stats = {}
    for p in players:
        s=p["season_projection"]
        denom=max(1, calendar["team_game_count"].get(p["team"],0))
        stats[p["player_id"]] = {}
        for ds in calendar["dates"]:
            n = calendar["date_team_games"].get((ds,p["team"]),0)
            stats[p["player_id"]][ds] = {k:(s[k]*n/denom) for k in STAT_KEYS}
    return stats


def zero_reference(calendar):
    z={k:0.0 for k in STAT_KEYS}
    return [{
        "reference_id":"placeholder","weight":1.0,"stat_values":dict(z),
        "evaluation_group_stat_values":{w:dict(z) for w in calendar["weeks"]},
    }]


def make_snapshot(season, players, calendar, cutoff, initial_rosters=None, reference_field=None, draft_order=None, seed=170317):
    initial_rosters=initial_rosters or {t:[] for t in TEAMS}
    draft_order=draft_order or []
    pstats=player_period_stats(players,calendar)
    pp=[{"player_id":p["player_id"],"stat_values":p["season_projection"]} for p in players]
    positions=[
        {"position_id":"C","display_name":"C","position_group_id":"FORWARD","rosterable":True},
        {"position_id":"LW","display_name":"LW","position_group_id":"FORWARD","rosterable":True},
        {"position_id":"RW","display_name":"RW","position_group_id":"FORWARD","rosterable":True},
        {"position_id":"D","display_name":"D","position_group_id":"DEFENDER","rosterable":True},
        {"position_id":"G","display_name":"G","position_group_id":"GOALIE","rosterable":True},
    ]
    agent={t:f"anon{i+1}" for i,t in enumerate(TEAMS[1:])}
    first_focus = draft_order[0] if draft_order else TEAMS[0]
    # Snapshot user team is orientable later by snapshot_for; the base only needs a valid first USER pick.
    focus=first_focus
    others=[t for t in TEAMS if t!=focus]
    amap={t:f"anon{i+1}" for i,t in enumerate(others)}
    actors=["USER" if t==focus else amap[t] for t in draft_order]
    team_objects=[{"team_id":t,"league_id":f"hist_{season}","display_name":t} for t in TEAMS]
    d={
        "schema_version":"decision-engine-input/0.3.1",
        "capability_profile":"HORIZON_SPLIT_EXACT_CALENDAR_V1",
        "snapshot_id":f"historical_{season}_base",
        "created_at":cutoff,
        "user_team_id":focus,
        "league":{
            "league_id":f"historical_{season}_hoh_shape","sport_code":"HOCKEY","season":season,
            "platform":"MANUAL","ownership_mode":"EXCLUSIVE","number_of_teams":6,
            "positions":positions,"roster_slots":roster_slots(),"categories":categories(),
            "league_constraints":[{"constraint_id":"terminal_roster_total","type":"TOTAL_ROSTER_COUNT","position_ids":[],"counting_basis":"ASSIGNED_POSITION","scope_slot_classes":["ACTIVE","BENCH","INJURED_RESERVE","RESERVE"],"operator":"EQ","limit":19,"evaluation_phase":"ROSTER_COMPLETION"}],
            "stat_definitions":[{"stat_key":k,"minimum":0} for k in STAT_KEYS],
            "stat_relationships":[{"relationship_id":"save_identity","left_stat_key":"saves","operator":"LTE","right_stat_key":"shots_against"}],
            "scoring_objective":{"type":"H2H_EACH_CATEGORY","category_tie_credit":0.5,"both_undefined_tie_credit":0.5,"rounding":"HALF_EVEN","horizon_id":f"historical_{season}_weekly","evaluation_period_count":len(calendar["weeks"])},
            "lineup_rules":{"lock_mode":"DAILY","information_policy":"STATIC_PRE_DRAFT","active_limits":[],"minimum_participation":[]},
            "draft_config":{"draft_type":"SNAKE"},
        },
        "manager_profile":{"manager_profile_id":"historical_standard","position_policies":[],"category_preferences":[],"strategy_settings":{"opponent_strategy_mode":"POOL_ONLY","timing_enabled":False}},
        "players":[{"player_id":p["player_id"],"sport_code":"HOCKEY","display_name":p["display_name"]} for p in players],
        "league_players":[{"player_id":p["player_id"],"league_id":f"hist_{season}","rosterable":True,"eligibility":{"position_ids":p["eligibility"],"source":"IMPORT","as_of":cutoff},"status_tags":[]} for p in players],
        "projection_set":{"projection_set_id":f"crome_{season}_preseason","sport_code":"HOCKEY","season":season,"horizon_id":f"season_{season}","source":"Frozen Crome aggregate preseason workbook","created_at":cutoff,"player_projections":pp},
        "teams":team_objects,
        "roster_state":{"as_of":cutoff,"teams":[{"team_id":t,"entries":[{"player_id":p,"acquisition_type":"KEEPER"} for p in initial_rosters[t]]} for t in TEAMS]},
        "draft_state":{"status":"IN_PROGRESS" if draft_order else "COMPLETE","remaining_picks":[{"pick_id":f"hist_pick_{i+1:03d}","actor_id":a} for i,a in enumerate(actors)],"anonymous_demands":[{"agent_id":amap[t],"initial_roster_count":len(initial_rosters[t])} for t in others],"manually_excluded_player_ids":[]},
        "exposure_model":{"exposure_model_id":f"historical_{season}_exact_schedule_mean","calendar_id":f"official_nhl_api_actual_schedule_{season}","horizon_id":f"historical_{season}_weekly","as_of":cutoff,"data_kind":"ARCHIVED","exposure_stat_key":"games","periods":calendar["periods"],"evaluation_groups":calendar["groups"],"projection_reconciliation_mode":"EXPOSURE_SUBSET_OF_PROJECTION_HORIZON","forecast_scenarios":[{"scenario_id":"preseason_mean_by_scheduled_date","probability":1.0,"player_period_stats":pstats}]},
        "reference_model":{"reference_model_id":f"historical_{season}_reference_placeholder","as_of":cutoff,"normalization_player_ids":[p["player_id"] for p in players],"field_id":"placeholder","generation_policy":"EXPLICIT_SYNTHETIC_FIXTURE","field":reference_field or zero_reference(calendar)},
        "methodology_config":{"methodology_id":"M0.3-ROSTER-RESEARCH-1","seed":seed,"rollouts":1,"soft_policy_lambda":0.005,"neutral_temperature":6,"max_search_nodes":500000,"max_lineup_plans":1000000,"continuation_policy":"B3"},
        "source_metadata":{"source_platform":"MANUAL","adapter_name":"historical_development_v0317","adapter_version":"0.3.17","imported_at":cutoff},
    }
    return validate(d)


def snake_order(rounds: int, teams=TEAMS):
    order=[]
    for r in range(rounds):
        order += list(teams) if r%2==0 else list(reversed(teams))
    return order


def generate_b1_keepers(base_snapshot, players):
    world={"snapshot":base_snapshot,"family":"HISTORICAL_KEEPER_SEED","world_seed":317,"team_ids":TEAMS,"initial_rosters":{t:[] for t in TEAMS},"draft_order":snake_order(19),"roster_size":19}
    ctx=DraftContext(world)
    rosters={t:[] for t in TEAMS};pool={p["player_id"] for p in players}
    trace=[]
    for i,t in enumerate(world["draft_order"]):
        later=world["draft_order"][i+1:].count(t)
        p=choose("B1",rosters[t],pool,later,ctx.solver,ctx.iv,ctx.replacement)
        if p is None: raise RuntimeError(f"B1 keeper seed failed at {i}/{t}")
        pool.remove(p);rosters[t].append(p);trace.append((i,t,p))
    keep={t:[] for t in TEAMS}
    for _,t,p in trace:
        if len(keep[t])<7: keep[t].append(p)
    return keep


def aggregate_forecast_by_week(data, plan):
    periods={p["period_id"]:p for p in data["exposure_model"]["periods"]}
    scenario=data["exposure_model"]["forecast_scenarios"][0]["player_period_stats"]
    grouped={g["evaluation_group_id"]:{k:0.0 for k in STAT_KEYS} for g in data["exposure_model"]["evaluation_groups"]}
    for lock, rows in plan.items():
        gid=periods[lock]["evaluation_group_id"]
        for row in rows:
            v=scenario[row["player_id"]][lock]
            for k in STAT_KEYS: grouped[gid][k]+=float(v.get(k,0.0))
    total={k:math.fsum(grouped[g][k] for g in grouped)/len(grouped) for k in STAT_KEYS}
    return total, grouped


def build_reference_field(world):
    rows=[];trace=[];data=world["snapshot"]
    for policy in ("B1","B2","B3"):
        draft=play_draft(world,TEAMS[0],policy,opponent_style=policy)
        if draft["status"]!="COMPLETE": raise RuntimeError(f"Reference {policy} failed")
        for team, roster in sorted(draft["rosters"].items()):
            plan=lineup_plan(data,roster)
            total,grouped=aggregate_forecast_by_week(data,plan)
            rows.append({"reference_id":f"{policy}_{team}","weight":1.0,"stat_values":total,"evaluation_group_stat_values":grouped})
            trace.append({"policy":policy,"team":team,"roster":sorted(roster)})
    return rows, trace


def orient_world(snapshot, keepers):
    return {"snapshot":snapshot,"family":"HISTORICAL_REAL_OUTCOME","world_seed":317,"team_ids":TEAMS,"initial_rosters":copy.deepcopy(keepers),"draft_order":snake_order(12),"roster_size":19}


def max_budget_for_ordinal(n):
    return 16 if n<=4 else 32 if n<=8 else 64


def broad_pool(old):
    rows=old["ranked_candidates"]
    score=[x["player_id"] for x in rows]
    intr=[x["player_id"] for x in sorted(rows,key=lambda x:(-x["intrinsic_value"],x["player_id"]))]
    pool=set(score[:24])|set(intr[:16])|{old["recommended_player_id"]}
    for b in ("B1","B3"):
        pid=old.get("baselines",{}).get(b,{}).get("player_id")
        if pid: pool.add(pid)
    for rank in (30,50,75,100,150,200,250,300):
        if rank<=len(score): pool.add(score[rank-1])
    return sorted(pool)


def adaptive_engine_pick(data, ordinal):
    max_budget=max_budget_for_ordinal(ordinal)
    one=copy.deepcopy(data);one["methodology_config"]["rollouts"]=1
    old=ExactCalendarIncrementalRunner(one,sweeps=1,maximum_storage_patterns=64).run()
    if old["run_status"]!="ROSTER_ONLY": raise RuntimeError(f"One-rollout engine state failed: {old['run_status']}")
    pool=broad_pool(old)
    race=copy.deepcopy(data);race["methodology_config"]["rollouts"]=max_budget
    runner=ExactCalendarIncrementalRunner(race,sweeps=1,maximum_storage_patterns=64)
    active=pool;prev=old["recommended_player_id"];stable=0;budget=2;final_rows=None;final_leader=None;status=UNRESOLVED
    stages={"1":{"leader":prev,"candidate_count":len(old["ranked_candidates"])}}
    while budget<=max_budget:
        leader,rows=paired_rows(runner,active,budget)
        stable = stable+1 if leader==prev else 0
        ok,worst=resolution(rows,EPSILON)
        stages[str(budget)]={"leader":leader,"candidate_count":len(active),"worst_ci95_high":worst,"resolved":ok,"stable_leader_streak":stable}
        final_rows,final_leader=rows,leader
        if budget>=MIN_RESOLUTION_BUDGET and ok and stable>=1:
            status=RESOLVED;break
        if budget==max_budget:break
        active=race_survivors(rows,EPSILON,min_keep=3,max_keep=12,force={leader,prev})
        prev=leader;budget*=2
    row={"status":status,"final_leader_id":final_leader,"final_budget":budget,"max_budget":max_budget,"stages":stages,"final_rows":final_rows,"broad_pool_count":len(pool)}
    action=historical_action(row)
    return action|{"diagnostic":row,"one_rollout_leader":old["recommended_player_id"]}


def adaptive_draft(world, focus):
    data=world["snapshot"]
    base_ctx=DraftContext(world)
    solver=base_ctx.solver;iv=base_ctx.iv;anchor=base_ctx.replacement
    rosters=copy.deepcopy(world["initial_rosters"]);taken={p for xs in rosters.values() for p in xs};pool={p["player_id"] for p in data["league_players"] if p["rosterable"]}-taken
    trace=[];engine_decisions=[];ordinal=0
    for index, team in enumerate(world["draft_order"]):
        remaining=world["draft_order"][index+1:].count(team)
        if team==focus:
            ordinal+=1
            snap=snapshot_for(world,rosters,index,focus)
            action=adaptive_engine_pick(snap,ordinal)
            selected=action["action_player_id"]
            engine_decisions.append({"ordinal":ordinal,"draft_index":index,"player_id":selected,"confidence_status":action["confidence_status"],"fallback_used":action["fallback_used"],"final_budget":action["diagnostic"]["final_budget"],"one_rollout_leader":action["one_rollout_leader"],"broad_pool_count":action["diagnostic"]["broad_pool_count"]})
            policy="B4_ADAPTIVE"
        else:
            pol=("B1","B3","B2")[index%3]
            selected=choose(pol,rosters[team],pool,remaining,solver,iv,anchor)
            policy=pol
        if selected is None: return {"status":"NO_FEASIBLE_PICK","trace":trace,"rosters":rosters,"engine_decisions":engine_decisions}
        reason=solver.candidate_reason(rosters[team],selected,pool,remaining)
        if reason: raise RuntimeError(f"Illegal historical draft pick {team}/{selected}: {reason}")
        pool.remove(selected);rosters[team].append(selected)
        trace.append({"draft_index":index,"team":team,"player_id":selected,"policy":policy})
    for t,ids in rosters.items():
        if not solver.best(ids,terminal=True): raise RuntimeError(f"Invalid completed roster {t}")
    return {"status":"COMPLETE","trace":trace,"rosters":rosters,"engine_decisions":engine_decisions}


def load_actual_outcomes(path: Path, players, calendar):
    by_name=defaultdict(list)
    for p in players: by_name[p["name_key"]].append(p)
    tape={p["player_id"]:{ds:{k:0.0 for k in STAT_KEYS} for ds in calendar["dates"]} for p in players}
    matched_ids=set();ambiguous=defaultdict(int);unmatched_names=defaultdict(int);rows=0
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows+=1
            ds=r["game_date"]
            if ds not in calendar["week_of"]: continue
            key=norm_name(r.get("player"))
            cand=by_name.get(key,[])
            if len(cand)>1:
                rt=norm_team(r.get("team")); same=[p for p in cand if p["team"]==rt]
                if len(same)==1:cand=same
            if len(cand)!=1:
                (ambiguous if len(cand)>1 else unmatched_names)[key]+=1
                continue
            p=cand[0];pid=p["player_id"];matched_ids.add(pid)
            v=tape[pid][ds]
            v["games"] += 1.0
            for src,dst in (("G","goals"),("A","assists"),("FOW","faceoff_wins"),("SOG","shots"),("HIT","hits"),("BLK","blocks"),("DEF","defense_points"),("W","wins"),("SVS","saves"),("SO","shutouts"),("SA","shots_against")):
                v[dst]+=fnum(r.get(src),0.0) or 0.0
    high_gp_unmatched=[p["display_name"] for p in players if p["player_id"] not in matched_ids and p["season_projection"]["games"]>=20]
    audit={"source_rows":rows,"preseason_players":len(players),"matched_preseason_players":len(matched_ids),"high_projected_gp_unmatched":high_gp_unmatched[:100],"high_projected_gp_unmatched_count":len(high_gp_unmatched),"ambiguous_outcome_name_keys":len(ambiguous),"unmatched_outcome_name_keys":len(unmatched_names)}
    return tape,audit


def realized_week_stats(data, plan, tape):
    period={p["period_id"]:p for p in data["exposure_model"]["periods"]}
    out={g["evaluation_group_id"]:{k:0.0 for k in STAT_KEYS} for g in data["exposure_model"]["evaluation_groups"]}
    for lock,rows in plan.items():
        gid=period[lock]["evaluation_group_id"]
        for row in rows:
            v=tape[row["player_id"]][lock]
            for k in STAT_KEYS:out[gid][k]+=v[k]
    return out


def score_rosters(data, rosters, tape):
    plans={t:lineup_plan(data,r) for t,r in rosters.items()}
    weekly={t:realized_week_stats(data,plans[t],tape) for t in rosters}
    shares={}
    for focus in rosters:
        vals=[]
        for gid in weekly[focus]:
            for opp in rosters:
                if opp==focus:continue
                vals.append(score_matchup(data["league"],weekly[focus][gid],weekly[opp][gid])[0])
        shares[focus]=statistics.fmean(vals) if vals else 0.0
    return shares,plans


def run(season, root: Path, output: Path, focus_limit=6):
    meta=SEASON_META[season]
    sched=load_schedule(root/f"data/historical/{season}/schedule.csv")
    cal=build_calendar(sched)
    players,excluded=load_preseason(root/meta["preseason"],cal,season)
    # Pre-outcome base world, full 19 rounds, only to derive a neutral shared keeper allocation.
    pre=make_snapshot(season,players,cal,meta["cutoff"],draft_order=snake_order(19))
    keepers=generate_b1_keepers(pre,players)
    live=make_snapshot(season,players,cal,meta["cutoff"],initial_rosters=keepers,draft_order=snake_order(12))
    world=orient_world(live,keepers)
    reference,ref_trace=build_reference_field(world)
    live=make_snapshot(season,players,cal,meta["cutoff"],initial_rosters=keepers,reference_field=reference,draft_order=snake_order(12))
    world=orient_world(live,keepers)
    commitments={"version":RUNTIME_VERSION,"season":season,"cutoff":meta["cutoff"],"snapshot_sha256":canonical_hash(live),"keeper_rosters":keepers,"reference_trace":ref_trace,"outcomes_loaded":False,"strategies":{}}
    # Commit all draft choices before reading realized player-game data.
    for focus in TEAMS[:focus_limit]:
        commitments["strategies"][focus]={}
        ctx=DraftContext(world)
        for pol in ("B1","B2","B3"):
            d=play_draft(world,focus,pol,ctx,"MIXED")
            if d["status"]!="COMPLETE": raise RuntimeError(f"{season}/{focus}/{pol} failed: {d['status']}")
            commitments["strategies"][focus][pol]={"status":"COMPLETE","rosters":d["rosters"],"trace":d["trace"]}
        d=adaptive_draft(world,focus)
        if d["status"]!="COMPLETE": raise RuntimeError(f"{season}/{focus}/ENGINE failed")
        commitments["strategies"][focus]["ENGINE"]={"status":"COMPLETE","rosters":d["rosters"],"trace":d["trace"],"engine_decisions":d["engine_decisions"]}
    output.mkdir(parents=True,exist_ok=True)
    commit_path=output/f"{season}_draft_commitments.json"
    commit_path.write_text(json.dumps(commitments,indent=2)+"\n")
    commitment_hash=hashlib.sha256(commit_path.read_bytes()).hexdigest()
    # Outcome layer begins only after commitments are serialized.
    tape,identity_audit=load_actual_outcomes(root/f"data/historical/{season}/player_game_stats.csv",players,cal)
    rows=[];decision_rows=[]
    for focus in TEAMS[:focus_limit]:
        for pol,entry in commitments["strategies"][focus].items():
            shares,plans=score_rosters(live,entry["rosters"],tape)
            rows.append({"season":season,"focus_team":focus,"strategy":pol,"category_share":shares[focus],"draft_commitment_sha256":commitment_hash})
            if pol=="ENGINE":
                for dec in entry["engine_decisions"]:
                    decision_rows.append({"season":season,"focus_team":focus,**dec})
    by={}
    for pol in ("B1","B2","B3","ENGINE"):
        vals=[r["category_share"] for r in rows if r["strategy"]==pol]
        by[pol]={"mean_category_share":statistics.fmean(vals),"seat_values":vals}
    deltas={p:100*(by["ENGINE"]["mean_category_share"]-by[p]["mean_category_share"]) for p in ("B1","B2","B3")}
    resolved=[d for d in decision_rows if d["confidence_status"]==RESOLVED];unresolved=[d for d in decision_rows if d["confidence_status"]==UNRESOLVED]
    summary={
        "version":RUNTIME_VERSION,"season":season,"status":"DEVELOPMENT_HISTORICAL_RECONSTRUCTION_NOT_PROMOTION",
        "focus_seats":focus_limit,"strategies":by,"engine_minus_baseline_pp":deltas,
        "engine_decisions":{"all":len(decision_rows),"resolved":len(resolved),"unresolved":len(unresolved),"unresolved_rate":len(unresolved)/len(decision_rows) if decision_rows else None},
        "identity_audit":identity_audit,"excluded_preseason_players":excluded,"draft_commitment_sha256":commitment_hash,
        "protocol_notes":[
            "Preseason Crome projections and ESPN eligibility drive all draft choices.",
            "Synthetic keepers are the first seven rounds of a shared legal B1 preseason draft; all strategies inherit the same keepers.",
            "Live draft is 12-round six-team snake with HoH-shaped 19-player roster and 11 categories.",
            "Engine uses frozen v0.3.15 adaptive race and v0.3.16 unresolved fallback; Timing Value is zero.",
            "Reference field is deterministic equal B1/B2/B3 baseline-roster mixture built before outcomes.",
            "Lineups are committed using preseason projections and schedule only; actual performance is never used to choose lineups.",
            "Schedule is the final official NHL API regular-season calendar and may include later reschedules; this is allowed only in DEVELOPMENT reconstruction and is not holdout-grade schedule provenance.",
            "No in-season adds, drops, trades, IR activation, or dynamic injury news are modeled.",
            "Full-draft outcome is reported overall; resolved/unresolved decision counts are reported separately. Decision-level causal outcome attribution is not yet implemented and no resolved-vs-unresolved performance claim is made.",
        ],
    }
    (output/f"{season}_summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    with (output/f"{season}_strategy_scores.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    with (output/f"{season}_engine_decisions.csv").open("w",newline="") as f:
        fields=list(decision_rows[0].keys());w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(decision_rows)
    print(json.dumps(summary,indent=2),flush=True)
    return summary


def main():
    ap=argparse.ArgumentParser();ap.add_argument("season",choices=sorted(SEASON_META));ap.add_argument("--root",default=".");ap.add_argument("--output",default="results/historical_development");ap.add_argument("--focus-limit",type=int,default=6)
    a=ap.parse_args();run(a.season,Path(a.root),Path(a.output),a.focus_limit)

if __name__=="__main__":main()
# workflow-trigger: smoke-v0317
