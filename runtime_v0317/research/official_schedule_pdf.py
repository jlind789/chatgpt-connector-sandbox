"""Parse the frozen official NHL 2026-27 by-club schedule PDF.

The source prints every game twice, once on each club page.  We parse only game
calendar dates and home/away/opponent identity; local start times are intentionally
not used for preseason exposure because fantasy-day assignment is keyed to the
printed NHL calendar date.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import date, timedelta
import calendar, hashlib, json, re
from pathlib import Path
from typing import Any

try:
    import fitz  # PyMuPDF, build-time parser dependency
except Exception:  # pragma: no cover
    fitz = None

TEAM_PAGE = {
    'Anaheim Ducks':'ANA','Boston Bruins':'BOS','Buffalo Sabres':'BUF','Calgary Flames':'CGY',
    'Carolina Hurricanes':'CAR','Chicago Blackhawks':'CHI','Colorado Avalanche':'COL',
    'Columbus Blue Jackets':'CBJ','Dallas Stars':'DAL','Detroit Red Wings':'DET',
    'Edmonton Oilers':'EDM','Florida Panthers':'FLA','Los Angeles Kings':'LAK','Minnesota Wild':'MIN',
    'Montreal Canadiens':'MTL','Nashville Predators':'NSH','New Jersey Devils':'NJD',
    'N.Y. Islanders':'NYI','N.Y. Rangers':'NYR','Ottawa Senators':'OTT','Philadelphia Flyers':'PHI',
    'Pittsburgh Penguins':'PIT','San Jose Sharks':'SJS','Seattle Kraken':'SEA','St. Louis Blues':'STL',
    'Tampa Bay Lightning':'TBL','Toronto Maple Leafs':'TOR','Utah Mammoth':'UTA','Vancouver Canucks':'VAN',
    'Vegas Golden Knights':'VGK','Washington Capitals':'WSH','Winnipeg Jets':'WPG',
}
OPPONENT = {
    'Anaheim':'ANA','Boston':'BOS','Buffalo':'BUF','Calgary':'CGY','Carolina':'CAR','Chicago':'CHI',
    'Colorado':'COL','Columbus':'CBJ','Dallas':'DAL','Detroit':'DET','Edmonton':'EDM','Florida':'FLA',
    'Los Angeles':'LAK','Minnesota':'MIN','Montreal':'MTL','Nashville':'NSH','New Jersey':'NJD',
    'N.Y. Islanders':'NYI','N.Y. Rangers':'NYR','Ottawa':'OTT','Philadelphia':'PHI','Pittsburgh':'PIT',
    'San Jose':'SJS','Seattle':'SEA','St. Louis':'STL','Tampa Bay':'TBL','Toronto':'TOR','Utah':'UTA',
    'Vancouver':'VAN','Vegas':'VGK','Washington':'WSH','Winnipeg':'WPG',
}
MONTH = {m[:3]:i for i,m in enumerate(calendar.month_abbr) if m}
NHL_TEAMS=tuple(sorted(set(TEAM_PAGE.values())))

@dataclass(frozen=True)
class ClubListing:
    team: str
    game_date: str
    opponent: str
    is_away: bool
    pdf_page: int
    row_y: float

@dataclass(frozen=True)
class CanonicalGame:
    game_id: str
    game_date: str
    away_team: str
    home_team: str
    source_pages: tuple[int,int]

    def to_dict(self): return asdict(self)


def sha256_file(path: str|Path) -> str:
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()


def _header_columns(words):
    headers=[]
    for w in words:
        x0,y0,x1,y1,txt,*_=w
        if txt in {'Date','Time','Opponent'} and y0<140: headers.append((x0,y0,txt))
    by_y=defaultdict(list)
    for h in headers: by_y[round(h[1])].append(h)
    clusters=[v for v in by_y.values() if all(sum(1 for x in v if x[2]==name)>=2 for name in ('Date','Time','Opponent'))]
    if not clusters: raise ValueError('schedule list page headers not found')
    v=sorted(clusters,key=lambda z:min(x[1] for x in z))[0]
    return ([x for x,y,t in sorted(v) if t=='Date'],[x for x,y,t in sorted(v) if t=='Time'],
            [x for x,y,t in sorted(v) if t=='Opponent'],min(y for x,y,t in v))


def _parse_half(words, half: int):
    dates,times,opps,header_y=_header_columns(words)
    date_lo=dates[half]-30; date_hi=times[half]-4; opp_lo=opps[half]-12
    opp_hi=((opps[0]+dates[1])/2+25) if half==0 else 10_000
    rows=defaultdict(list)
    for w in words:
        x0,y0,x1,y1,txt,*_=w
        if header_y+15 <= y0 <= 810 and ((date_lo<=x0<date_hi) or (opp_lo<=x0<opp_hi)):
            rows[round(y0*2)/2].append((x0,txt))
    out=[]
    for y,toks in sorted(rows.items()):
        toks=sorted(toks)
        dt=[t for x,t in toks if date_lo<=x<date_hi]
        ow=[t for x,t in toks if opp_lo<=x<opp_hi]
        mon=next((t for t in dt if t in MONTH),None)
        day=next((int(t) for t in dt if t.isdigit()),None)
        if mon is None or day is None or not ow: continue
        label=' '.join(ow).strip(); away=label.startswith('AT ')
        if away: label=label[3:].strip()
        if label not in OPPONENT: raise ValueError(f'unknown opponent label {label!r} at y={y}')
        month=MONTH[mon]; year=2026 if month>=9 else 2027
        out.append((date(year,month,day).isoformat(),OPPONENT[label],away,y))
    return out


def parse_official_by_club_pdf(path: str|Path) -> tuple[list[CanonicalGame],dict[str,Any]]:
    if fitz is None: raise RuntimeError('PyMuPDF is required to rebuild the frozen schedule fixture from PDF')
    path=Path(path); doc=fitz.open(path)
    if len(doc)!=64: raise ValueError(f'expected 64-page official by-club PDF, got {len(doc)}')
    listings=[]; team_pages={}
    # Even-numbered PDF pages (1-indexed) are the machine-readable club lists.
    for pno in range(1,len(doc),2):
        page=doc[pno]; text=page.get_text('text')
        m=re.search(r'2026-27 Schedule for the (.+)',text)
        if not m: raise ValueError(f'club title missing on PDF page {pno+1}')
        team_name=m.group(1).strip()
        if team_name not in TEAM_PAGE: raise ValueError(f'unknown club title {team_name!r} on page {pno+1}')
        team=TEAM_PAGE[team_name]; team_pages[team]=pno+1
        rows=_parse_half(page.get_text('words'),0)+_parse_half(page.get_text('words'),1)
        if len(rows)!=84: raise ValueError(f'{team} page has {len(rows)} parsed games, expected 84')
        for gd,opp,away,y in rows: listings.append(ClubListing(team,gd,opp,away,pno+1,y))
    if set(team_pages)!=set(NHL_TEAMS): raise ValueError('not all 32 NHL clubs were parsed')
    if len(listings)!=2688: raise ValueError(f'expected 2,688 club listings, got {len(listings)}')
    buckets=defaultdict(list)
    for r in listings:
        away=r.team if r.is_away else r.opponent
        home=r.opponent if r.is_away else r.team
        buckets[(r.game_date,away,home)].append(r)
    if len(buckets)!=1344: raise ValueError(f'expected 1,344 unique games, got {len(buckets)}')
    games=[]
    for i,(k,rows) in enumerate(sorted(buckets.items()),1):
        gd,away,home=k
        if len(rows)!=2: raise ValueError(f'{k} appears {len(rows)} times, expected reciprocal club listings')
        if {r.team for r in rows}!={away,home}: raise ValueError(f'club listing mismatch for {k}')
        # reciprocal home/away check
        if not any(r.team==away and r.is_away for r in rows) or not any(r.team==home and not r.is_away for r in rows):
            raise ValueError(f'home/away disagreement for {k}')
        away_row=next(r for r in rows if r.team==away)
        home_row=next(r for r in rows if r.team==home)
        games.append(CanonicalGame(f'NHL-2026-{i:04d}',gd,away,home,(away_row.pdf_page,home_row.pdf_page)))
    team_count=Counter();home_count=Counter();date_team=defaultdict(set)
    for g in games:
        team_count[g.away_team]+=1;team_count[g.home_team]+=1;home_count[g.home_team]+=1
        if g.away_team in date_team[g.game_date] or g.home_team in date_team[g.game_date]:
            raise ValueError(f'team doublebooked on {g.game_date}')
        date_team[g.game_date].update((g.away_team,g.home_team))
    if set(team_count.values())!={84} or set(home_count.values())!={42}: raise ValueError('84-game / 42-home structural check failed')
    audit={
        'source_file':path.name,'source_sha256':sha256_file(path),'pdf_pages':len(doc),'club_listings':len(listings),
        'unique_games':len(games),'teams':len(team_count),'team_game_count_min':min(team_count.values()),
        'team_game_count_max':max(team_count.values()),'home_game_count_min':min(home_count.values()),
        'home_game_count_max':max(home_count.values()),'min_game_date':min(g.game_date for g in games),
        'max_game_date':max(g.game_date for g in games),'all_32_team_dates':sorted(d for d,t in date_team.items() if len(t)==32),
        'team_pages':dict(sorted(team_pages.items())),
    }
    return games,audit


def fantasy_week(game_date: str, *, anchor='2026-09-28') -> int:
    d=date.fromisoformat(game_date); a=date.fromisoformat(anchor)
    return ((d-a).days//7)+1


def fantasy_calendar(games: list[CanonicalGame], *, anchor='2026-09-28', matchup_count=27) -> dict[str,Any]:
    a=date.fromisoformat(anchor); end=a+timedelta(days=7*matchup_count-1)
    included=[g for g in games if a<=date.fromisoformat(g.game_date)<=end]
    excluded=[g for g in games if date.fromisoformat(g.game_date)>end]
    team_counts=Counter()
    weeks=[]
    for wi in range(1,matchup_count+1):
        ws=a+timedelta(days=7*(wi-1));we=ws+timedelta(days=6)
        wg=[g for g in included if ws<=date.fromisoformat(g.game_date)<=we]
        daily=Counter(g.game_date for g in wg)
        weeks.append({'week_index':wi,'week_start':ws.isoformat(),'week_end':we.isoformat(),'game_count':len(wg),
                      'daily_game_counts':dict(sorted(daily.items()))})
        for g in wg: team_counts[g.away_team]+=1;team_counts[g.home_team]+=1
    return {'anchor_monday':a.isoformat(),'matchup_count':matchup_count,'fantasy_regular_season_end':end.isoformat(),
            'included_nhl_games':len(included),'excluded_after_fantasy_regular_season':len(excluded),
            'team_games_in_fantasy_horizon':dict(sorted(team_counts.items())),
            'team_games_min':min(team_counts.values()),'team_games_max':max(team_counts.values()),'weeks':weeks}
