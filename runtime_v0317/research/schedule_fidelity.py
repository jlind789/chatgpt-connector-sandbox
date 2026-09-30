"""Schedule-source normalization and fidelity checks for v0.3.11.

This module deliberately separates three claims:
1) a source can be parsed;
2) a schedule is structurally complete;
3) a schedule has an explicit local NHL game date suitable for fantasy-day exposure.

A UTC timestamp alone is not accepted as a fantasy game date, because late games can
cross midnight UTC while remaining on the prior local NHL calendar date.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

NHL_TEAMS = (
    "ANA","BOS","BUF","CAR","CBJ","CGY","CHI","COL","DAL","DET","EDM","FLA",
    "LAK","MIN","MTL","NJD","NSH","NYI","NYR","OTT","PHI","PIT","SEA","SJS",
    "STL","TBL","TOR","UTA","VAN","VGK","WPG","WSH",
)

FULL_NAME_TO_ABBREV = {
    "Anaheim Ducks":"ANA", "Boston Bruins":"BOS", "Buffalo Sabres":"BUF",
    "Carolina Hurricanes":"CAR", "Columbus Blue Jackets":"CBJ", "Calgary Flames":"CGY",
    "Chicago Blackhawks":"CHI", "Colorado Avalanche":"COL", "Dallas Stars":"DAL",
    "Detroit Red Wings":"DET", "Edmonton Oilers":"EDM", "Florida Panthers":"FLA",
    "Los Angeles Kings":"LAK", "Minnesota Wild":"MIN", "Montreal Canadiens":"MTL",
    "Montréal Canadiens":"MTL", "New Jersey Devils":"NJD", "Nashville Predators":"NSH",
    "New York Islanders":"NYI", "New York Rangers":"NYR", "Ottawa Senators":"OTT",
    "Philadelphia Flyers":"PHI", "Pittsburgh Penguins":"PIT", "Seattle Kraken":"SEA",
    "San Jose Sharks":"SJS", "St. Louis Blues":"STL", "Tampa Bay Lightning":"TBL",
    "Toronto Maple Leafs":"TOR", "Utah Mammoth":"UTA", "Vancouver Canucks":"VAN",
    "Vegas Golden Knights":"VGK", "Winnipeg Jets":"WPG", "Washington Capitals":"WSH",
}

@dataclass(frozen=True)
class ScheduleGame:
    game_id: str
    game_date: str | None
    home_team: str
    away_team: str
    start_time_utc: str | None = None
    source_record_id: str | None = None
    date_basis: str = "EXPLICIT_LOCAL_GAME_DATE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _date_string(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("gameDate must be an ISO date string")
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid game date: {value!r}") from exc


def parse_nhl_api_payload(payload: Any) -> list[ScheduleGame]:
    """Parse NHL API-shaped objects that expose an explicit ``gameDate``.

    Supported shapes are a raw games list, ``{'games': [...]}``, and
    ``{'gameWeek': [{'games': [...]}]}``.  We intentionally key fantasy days to
    ``gameDate`` rather than UTC timestamp conversion.
    """
    if isinstance(payload, list):
        raw_games = payload
    elif isinstance(payload, dict) and isinstance(payload.get("games"), list):
        raw_games = payload["games"]
    elif isinstance(payload, dict) and isinstance(payload.get("gameWeek"), list):
        raw_games = [g for w in payload["gameWeek"] for g in w.get("games", [])]
    else:
        raise ValueError("unsupported NHL API payload shape")
    out: list[ScheduleGame] = []
    for raw in raw_games:
        gid = str(raw.get("id") or raw.get("gameId") or "")
        if not gid:
            raise ValueError("NHL API game is missing id")
        gd = _date_string(raw.get("gameDate"))
        home = raw.get("homeTeam") or {}
        away = raw.get("awayTeam") or {}
        h = home.get("abbrev") if isinstance(home, dict) else None
        a = away.get("abbrev") if isinstance(away, dict) else None
        if h not in NHL_TEAMS or a not in NHL_TEAMS or h == a:
            raise ValueError(f"invalid teams in NHL API game {gid}: {a} at {h}")
        start = raw.get("startTimeUTC") or raw.get("startTimeUtc")
        out.append(ScheduleGame(gid, gd, h, a, start, gid, "NHL_GAME_DATE"))
    return out


def parse_fixture_download_payload(payload: Any) -> list[ScheduleGame]:
    """Parse FixtureDownload JSON conservatively.

    Its feed supplies DateUtc but no explicit venue-local NHL ``gameDate``.  The
    parser therefore preserves identity/UTC time but leaves game_date unset.  A
    caller may use this feed for identity/count cross-checks, but it cannot pass
    ``require_local_game_date=True`` without an independently resolved local date.
    """
    if not isinstance(payload, list):
        raise ValueError("FixtureDownload payload must be a list")
    out = []
    for raw in payload:
        rid = str(raw.get("MatchNumber") or "")
        home = FULL_NAME_TO_ABBREV.get(raw.get("HomeTeam"))
        away = FULL_NAME_TO_ABBREV.get(raw.get("AwayTeam"))
        if not rid or not home or not away or home == away:
            raise ValueError(f"invalid FixtureDownload game record: {raw!r}")
        raw_utc = raw.get("DateUtc")
        if not isinstance(raw_utc, str) or not raw_utc:
            raise ValueError(f"FixtureDownload game {rid} missing DateUtc")
        start = raw_utc.replace(" ", "T")
        if start.endswith("Z") is False:
            start += "Z"
        out.append(ScheduleGame(f"fd-{rid}", None, home, away, start, rid, "UTC_ONLY_UNRESOLVED_LOCAL_DATE"))
    return out


def normalized_schedule_json(games: Iterable[ScheduleGame]) -> str:
    rows = [g.to_dict() for g in sorted(games, key=lambda x: (x.game_date or "9999", x.start_time_utc or "", x.game_id))]
    return json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def schedule_sha256(games: Iterable[ScheduleGame]) -> str:
    return hashlib.sha256(normalized_schedule_json(games).encode("utf-8")).hexdigest()


def validate_schedule(
    games: Iterable[ScheduleGame], *,
    expected_games: int | None = None,
    expected_team_games: int | None = None,
    expected_home_games: int | None = None,
    require_local_game_date: bool = True,
    date_min: str | None = None,
    date_max: str | None = None,
) -> dict[str, Any]:
    games = list(games)
    errors: list[str] = []
    ids = [g.game_id for g in games]
    if len(ids) != len(set(ids)):
        errors.append("DUPLICATE_GAME_ID")
    if expected_games is not None and len(games) != expected_games:
        errors.append(f"GAME_COUNT:{len(games)}!={expected_games}")
    team_counts = Counter(); home_counts = Counter(); away_counts = Counter(); date_team = defaultdict(set)
    dates: list[str] = []
    for g in games:
        if g.home_team not in NHL_TEAMS or g.away_team not in NHL_TEAMS or g.home_team == g.away_team:
            errors.append(f"INVALID_TEAMS:{g.game_id}")
            continue
        team_counts[g.home_team] += 1; team_counts[g.away_team] += 1
        home_counts[g.home_team] += 1; away_counts[g.away_team] += 1
        if g.game_date is None:
            if require_local_game_date:
                errors.append(f"MISSING_LOCAL_GAME_DATE:{g.game_id}")
        else:
            gd = _date_string(g.game_date); dates.append(gd)
            for t in (g.home_team, g.away_team):
                if t in date_team[gd]: errors.append(f"TEAM_DOUBLEBOOKED_DATE:{t}:{gd}")
                date_team[gd].add(t)
    if expected_team_games is not None:
        for t in NHL_TEAMS:
            if team_counts[t] != expected_team_games:
                errors.append(f"TEAM_GAME_COUNT:{t}:{team_counts[t]}!={expected_team_games}")
    if expected_home_games is not None:
        for t in NHL_TEAMS:
            if home_counts[t] != expected_home_games:
                errors.append(f"TEAM_HOME_COUNT:{t}:{home_counts[t]}!={expected_home_games}")
            if away_counts[t] != expected_home_games:
                errors.append(f"TEAM_AWAY_COUNT:{t}:{away_counts[t]}!={expected_home_games}")
    if dates:
        if date_min and min(dates) != date_min: errors.append(f"MIN_DATE:{min(dates)}!={date_min}")
        if date_max and max(dates) != date_max: errors.append(f"MAX_DATE:{max(dates)}!={date_max}")
    return {
        "valid": not errors,
        "errors": errors,
        "game_count": len(games),
        "teams_seen": len([t for t in NHL_TEAMS if team_counts[t]]),
        "team_game_counts": dict(sorted(team_counts.items())),
        "home_game_counts": dict(sorted(home_counts.items())),
        "away_game_counts": dict(sorted(away_counts.items())),
        "min_game_date": min(dates) if dates else None,
        "max_game_date": max(dates) if dates else None,
        "all_team_dates": sorted(d for d, ts in date_team.items() if len(ts) == 32),
        "schedule_sha256": schedule_sha256(games),
    }


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def build_fantasy_week_matrix(games: Iterable[ScheduleGame], *, season_anchor="2026-09-28") -> dict[str, Any]:
    games = list(games)
    if any(g.game_date is None for g in games):
        raise ValueError("explicit local game_date is required for fantasy-week exposure")
    anchor = date.fromisoformat(season_anchor)
    if anchor.weekday() != 0:
        raise ValueError("season_anchor must be a Monday")
    by_date = defaultdict(list)
    for g in games:
        by_date[date.fromisoformat(g.game_date)].append(g)
    max_date = max(by_date) if by_date else anchor
    weeks = []
    cursor = anchor
    index = 1
    while cursor <= max_date:
        days=[]
        for j in range(7):
            d=cursor+timedelta(days=j); gs=by_date.get(d,[])
            active=sorted({t for g in gs for t in (g.home_team,g.away_team)})
            days.append({"date":d.isoformat(),"game_count":len(gs),"active_team_count":len(active),"active_teams":active})
        weeks.append({"week_index":index,"week_start":cursor.isoformat(),"week_end":(cursor+timedelta(days=6)).isoformat(),"days":days,
                      "game_count":sum(x["game_count"] for x in days)})
        index += 1; cursor += timedelta(days=7)
    return {"week_count":len(weeks),"weeks":weeks}


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_normalized_schedule(games: Iterable[ScheduleGame], path: str | Path) -> None:
    Path(path).write_text(json.dumps([g.to_dict() for g in games], indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
