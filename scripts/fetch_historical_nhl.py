#!/usr/bin/env python3
import csv, json, sys, time, hashlib
from pathlib import Path
from datetime import date, timedelta
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = "https://api-web.nhle.com/v1"
UA = "Mozilla/5.0 historical-fantasy-backtest/0.3.17"
SEASONS = {
    "20222023": (date(2022,10,1), date(2023,4,30), 1312),
    "20232024": (date(2023,10,1), date(2024,4,30), 1312),
}

def get_json(url, attempts=7):
    last = None
    for i in range(attempts):
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept":"application/json"})
            with urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(min(8, 0.5 * (2 ** i)))
    raise RuntimeError(f"GET failed after {attempts}: {url}: {last}")

def collect_schedule(season):
    start, end, expected = SEASONS[season]
    games = {}
    d = start
    while d <= end:
        data = get_json(f"{BASE}/schedule/{d.isoformat()}")
        for week in data.get("gameWeek", []):
            for g in week.get("games", []):
                if int(g.get("season",0)) == int(season) and int(g.get("gameType",0)) == 2:
                    games[int(g["id"])] = {
                        "season": season,
                        "game_id": int(g["id"]),
                        "game_date": week.get("date") or g.get("gameDate"),
                        "away_team_id": g["awayTeam"].get("id"),
                        "away_team": g["awayTeam"].get("abbrev"),
                        "home_team_id": g["homeTeam"].get("id"),
                        "home_team": g["homeTeam"].get("abbrev"),
                    }
        d += timedelta(days=7)
    rows = sorted(games.values(), key=lambda x:(x["game_date"],x["game_id"]))
    if len(rows) != expected:
        raise RuntimeError(f"{season}: expected {expected} regular-season games, got {len(rows)}")
    return rows

def full_name(v):
    if isinstance(v, dict):
        return v.get("default") or v.get("en") or next((x for x in v.values() if x), "")
    return v or ""

def roster_name_map(pbp):
    out = {}
    for x in pbp.get("rosterSpots", []) or []:
        pid = x.get("playerId")
        if not pid:
            continue
        first = full_name(x.get("firstName")).strip()
        last = full_name(x.get("lastName")).strip()
        nm = (first + " " + last).strip()
        if nm:
            out[int(pid)] = nm
    return out

def parse_one(schedule_row):
    gid = int(schedule_row["game_id"])
    box = get_json(f"{BASE}/gamecenter/{gid}/boxscore")
    pbp = get_json(f"{BASE}/gamecenter/{gid}/play-by-play")
    if int(box.get("gameType",2)) != 2:
        return [], None
    names = roster_name_map(pbp)
    fow = Counter()
    for play in pbp.get("plays", []) or []:
        if play.get("typeDescKey") == "faceoff" or int(play.get("typeCode",0) or 0) == 502:
            pid = (play.get("details") or {}).get("winningPlayerId")
            if pid:
                fow[int(pid)] += 1
    rows = []
    team_ids = {
        "awayTeam": (box.get("awayTeam") or {}).get("id"),
        "homeTeam": (box.get("homeTeam") or {}).get("id"),
    }
    team_abbr = {
        "awayTeam": (box.get("awayTeam") or {}).get("abbrev"),
        "homeTeam": (box.get("homeTeam") or {}).get("abbrev"),
    }
    pbg = box.get("playerByGameStats") or {}
    for side in ("awayTeam","homeTeam"):
        team = (pbg.get(side) or {})
        for group, nhl_type in (("forwards","F"),("defense","D")):
            for p in team.get(group, []) or []:
                pid = int(p.get("playerId"))
                g = int(p.get("goals") or 0)
                a = int(p.get("assists") or 0)
                rows.append({
                    "season": schedule_row["season"],
                    "game_id": gid,
                    "game_date": box.get("gameDate") or schedule_row["game_date"],
                    "team": team_abbr[side],
                    "team_id": team_ids[side],
                    "player_id": pid,
                    "player": names.get(pid) or full_name(p.get("name")),
                    "nhl_position": p.get("position") or nhl_type,
                    "G": g,
                    "A": a,
                    "FOW": int(fow.get(pid,0)),
                    "SOG": int(p.get("sog") or 0),
                    "HIT": int(p.get("hits") or 0),
                    "BLK": int(p.get("blockedShots") or 0),
                    "DEF": (g+a) if group=="defense" else 0,
                    "W": 0,
                    "SVS": 0,
                    "SO": 0,
                    "SA": 0,
                })
        for p in team.get("goalies", []) or []:
            pid = int(p.get("playerId"))
            decision = str(p.get("decision") or "")
            ga = int(p.get("goalsAgainst") or 0)
            saves = int(p.get("saves") or 0)
            sa = int(p.get("shotsAgainst") or 0)
            rows.append({
                "season": schedule_row["season"],
                "game_id": gid,
                "game_date": box.get("gameDate") or schedule_row["game_date"],
                "team": team_abbr[side],
                "team_id": team_ids[side],
                "player_id": pid,
                "player": names.get(pid) or full_name(p.get("name")),
                "nhl_position": "G",
                "G": 0, "A": 0, "FOW": 0, "SOG": 0, "HIT": 0, "BLK": 0, "DEF": 0,
                "W": 1 if decision == "W" else 0,
                "SVS": saves,
                "SO": 1 if decision == "W" and ga == 0 else 0,
                "SA": sa,
            })
    return rows, {
        "game_id": gid,
        "faceoff_events": int(sum(fow.values())),
        "players": len(rows),
    }

def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    seasons = sys.argv[1:] or ["20222023","20232024"]
    root = Path("data/historical")
    root.mkdir(parents=True, exist_ok=True)
    overall = {"source":"api-web.nhle.com", "seasons":{}}
    for season in seasons:
        if season not in SEASONS:
            raise SystemExit(f"Unsupported season {season}")
        print(f"Collecting {season} schedule...", flush=True)
        schedule = collect_schedule(season)
        outdir = root/season
        sf = outdir/"schedule.csv"
        write_csv(sf, schedule, ["season","game_id","game_date","away_team_id","away_team","home_team_id","home_team"])
        print(f"{season}: {len(schedule)} games; fetching boxscores + PBP...", flush=True)
        all_rows=[]; audits=[]; errors=[]
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs={ex.submit(parse_one,r):r for r in schedule}
            done=0
            for fut in as_completed(futs):
                r=futs[fut]
                try:
                    rows,audit=fut.result()
                    all_rows.extend(rows)
                    if audit: audits.append(audit)
                except Exception as e:
                    errors.append({"game_id":r["game_id"],"error":repr(e)})
                done += 1
                if done % 100 == 0:
                    print(f"{season}: {done}/{len(schedule)}", flush=True)
        if errors:
            (outdir/"errors.json").write_text(json.dumps(errors,indent=2))
            raise RuntimeError(f"{season}: {len(errors)} game fetch errors")
        all_rows.sort(key=lambda x:(x["game_date"],x["game_id"],x["team"] or "",x["player_id"]))
        pf=outdir/"player_game_stats.csv"
        fields=["season","game_id","game_date","team","team_id","player_id","player","nhl_position","G","A","FOW","SOG","HIT","BLK","DEF","W","SVS","SO","SA"]
        write_csv(pf, all_rows, fields)
        manifest={
            "season":season,
            "regular_games":len(schedule),
            "player_game_rows":len(all_rows),
            "faceoff_events":sum(x["faceoff_events"] for x in audits),
            "schedule_sha256":sha256(sf),
            "player_game_stats_sha256":sha256(pf),
            "source":"Official NHL Web API api-web.nhle.com",
            "endpoints":["/v1/schedule/{date}","/v1/gamecenter/{gameId}/boxscore","/v1/gamecenter/{gameId}/play-by-play"],
        }
        (outdir/"manifest.json").write_text(json.dumps(manifest,indent=2))
        overall["seasons"][season]=manifest
        print(json.dumps(manifest,indent=2), flush=True)
    (root/"manifest.json").write_text(json.dumps(overall,indent=2))
    print("DONE", flush=True)

if __name__ == "__main__":
    main()
