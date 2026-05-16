"""Live Kalshi sports markets — narrow filter for the Macro-tab sidebar.

Pulls the public Kalshi API (no auth) via the events endpoint with
``with_nested_markets=true``, surfacing only currently-live games.

The complete, authoritative spec for this module — filter chain,
live-detection model, sport labeling, Cricket/IPL priority, URL slug
rule, rate-limiting and the kalshi.json shape — is KALSHI_SPEC.md at
the repo root. Read it before changing anything here.

Quick orientation (see KALSHI_SPEC.md for the full rules):
- Live iff  (occurrence_datetime - pre_game_buffer) <= now <=
  (occurrence_datetime + sport_duration).  `occurrence_datetime` is
  Kalshi's *scheduled* start (often wrong by hours); the sport-specific
  PRE_GAME_BUFFER_MINUTES (tennis 150m, default 60m) absorbs the slop on
  the start side, SPORT_DURATION_MINUTES bounds the end side. See
  `_is_live_now`.
- 2-market events emit both sides ("91% Sinner / 9% Medvedev");
  multi-outcome events emit only the favorite.
- Cricket/IPL: always shown when live (bypass the [83%,98%] gate),
  sorted to the top. All other sports need the favorite in [83%,98%].
- Each pill links to the canonical event page built by
  `_build_event_url` (/markets/{series}/{slug}/{event}); falls back to
  the series page only when the series title is unknown.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone


API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
# Use a browser-like UA — Kalshi's edge appears to drop requests with
# non-browser User-Agents from cloud-IP origins (we see 0-byte responses
# from Railway with "Indicators-Dashboard/1.0" but the same code works
# fine from a laptop).
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FETCHER_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_DIR = os.path.dirname(FETCHER_DIR)
OUTPUT_PATH = os.path.join(PROJECT_DIR, "public", "kalshi.json")

# Cache of series_ticker -> series_title. Titles rarely change so we cache
# for 7 days; the cache is populated lazily as we encounter new series.
# This lets us build correct event-page URLs (which include a slugified
# series title as the middle path segment — see _build_event_url).
SERIES_TITLE_CACHE_PATH = os.path.join(FETCHER_DIR, ".kalshi_series_titles.json")
SERIES_TITLE_CACHE_TTL_SECONDS = 7 * 24 * 3600

# Committed warm-start seed of {series_ticker: title}. The runtime JSON
# cache is gitignored and Railway's FS is ephemeral, so without this every
# deploy cold-fetched ~70 /series titles in a burst and tripped Kalshi's
# rate limit (the post-deploy 429s). With this, _ensure_titles only fetches
# tickers NOT already seeded (e.g. newly added series). Titles below were
# verified live against the Kalshi API on 2026-05-16; the on-disk cache
# (when fresh) still overrides any entry whose title later changes.
SEED_SERIES_TITLES = {
    'KXABAGAME': 'ABA League Game',
    'KXAFCACGAME': 'AFC Asia Cup Game',
    'KXALEAGUEGAME': 'Australian A League Game',
    'KXARGLNBGAME': 'Liga Nacional de Basquetbol Game',
    'KXATPCHALLENGERMATCH': 'Challenger ATP ',
    'KXATPMATCH': 'ATP Tennis Match',
    'KXBELGIANPLGAME': 'Belgian Pro League Game',
    'KXBOXINGFIGHT': 'Boxing fight winner',
    'KXBRASILEIROGAME': 'Brasileiro Serie A Game',
    'KXBUNDESLIGAGAME': 'Bundesliga Game',
    'KXCODGAME': 'Call of Duty Games',
    'KXCONMEBOLLIBGAME': 'CONMEBOL Libertadores Game',
    'KXCOUNTYCHAMPMATCH': 'County Championship Cricket Match',
    'KXCRICKETT20IMATCH': 'Cricket T20I Match',
    'KXCRICKETWOMENT20IMATCH': 'Cricket Women T20I Match',
    'KXCS2GAME': 'Counter-Strike 2 Game',
    'KXDFBPOKALGAME': 'DFB Pokal Game',
    'KXDOTA2GAME': 'Dota 2 Game',
    'KXEFLL1GAME': 'EFL League One Game',
    'KXELITESERIENGAME': 'Eliteserien Game',
    'KXEPLGAME': 'English Premier League Game',
    'KXEREDIVISIEGAME': 'Eredivisie Game',
    'KXEUROCUPGAME': 'EuroCup Basketball Game',
    'KXEUROLEAGUEGAME': 'Euroleague Game',
    'KXF1RACE': 'F1 Race',
    'KXFIBAGAME': 'FIBA Game',
    'KXGBLGAME': 'GBL Basketball Game',
    'KXINDYCARRACE': 'IndyCar Race',
    'KXINTLFRIENDLYGAME': 'International Friendly Game',
    'KXIPLGAME': 'Indian Premier League Cricket Game',
    'KXITFMATCH': "ITF Men's Match",
    'KXLALIGAGAME': 'La Liga Game',
    'KXLIGAMXGAME': 'Liga MX Game',
    'KXLIGAPORTUGALGAME': 'Liga Portugal Game',
    'KXLIGUE1GAME': 'Ligue 1 Game',
    'KXLNBELITEGAME': 'LNB Elite Game',
    'KXLOLGAME': 'League of Legends Game',
    'KXMLBGAME': 'Professional Baseball Game',
    'KXMLSGAME': 'Major League Soccer Game',
    'KXMOTOGPRACE': 'Moto GP Race',
    'KXNASCARRACE': 'NASCAR Race',
    'KXNBAGAME': 'Professional Basketball Game',
    'KXNBLGAME': 'NBL Basketball Game',
    'KXNCAABBGAME': 'College Baseball Game',
    'KXNCAAFGAME': 'College Football Game',
    'KXNCAAMBGAME': "Men's College Basketball Men's Game",
    'KXNCAAWBGAME': "College Basketball Women's Game",
    'KXNHLGAME': 'NHL Game',
    'KXNWSLGAME': 'NWSL Game',
    'KXNZNBLGAME': 'New Zealand NBL Game',
    'KXPSLGAME': 'Pakistan Super League Cricket Game',
    'KXR6GAME': 'R6 Game',
    'KXROCKETLEAGUEGAME': 'Rocket League Game',
    'KXRUGBYESLMATCH': 'England Super League Rugby Match',
    'KXRUGBYFRA14MATCH': 'Rugby French 14 Match',
    'KXSAILGPRACE': 'Sail GP Race',
    'KXSAUDIPLGAME': 'Saudi Pro League Game',
    'KXSERIEAGAME': 'Serie A Game',
    'KXSUPERLIGGAME': 'Turkish Super Lig Game',
    'KXT20MATCH': "Men's T20 Cricket Match",
    'KXUAEPLGAME': 'UAE Pro League',
    'KXUECLGAME': 'UEFA Conference League Game',
    'KXUFCFIGHT': 'UFC Fight',
    'KXVALORANTGAME': 'Valorant game winner',
    'KXVTBGAME': 'VTB United League Game',
    'KXWNBAGAME': "Women's Pro Basketball Game",
    'KXWT20MATCH': "Women's T20 Match",
    'KXWTAGAME': 'WTA Tennis Winner',
}

# Filter knobs
MIN_FAVORITE_PCT = 0.83
MAX_FAVORITE_PCT = 0.98
MAX_OUTPUT_ITEMS = 15

# Game-outcome series suffixes (not prop bets / awards / drafts).
LIVE_GAME_SUFFIXES = ("GAME", "MATCH", "FIGHT", "RACE")

# Curated list of Kalshi sports series that publish per-game matchup markets.
# Querying these directly (instead of scanning all ~7000 events) keeps each
# refresh under ~5 seconds — fast enough for 1-min cadence. The list is
# inclusive; series with no current games just return empty in <500ms.
# If new sports series appear on Kalshi we can add them here.
ACTIVE_LIVE_SPORTS_SERIES = [
    # Tennis
    "KXATPMATCH", "KXATPCHALLENGERMATCH", "KXITFMATCH",
    "KXWTAGAME",
    # Cricket (priority per user). All end in GAME/MATCH so they pass the
    # game-outcome suffix rule, and all resolve to a "Cricket"/"IPL" label
    # (see SPORT_LABEL_RULES) so they bypass the 83-98% odds gate.
    # KXT20MATCH/KXWT20MATCH/KXCRICKETWOMENT20IMATCH = associate/regional
    # men's & women's T20 circuits (verified live on the Kalshi API).
    # NOTE: Big Bash League (Australian T20, ~Dec-Feb) has NO game-outcome
    # series on Kalshi as of 2026-05-16 (off-season). Re-add its series here
    # when it appears. Do NOT use "KXBBLGAME" — that ticker is Bundesliga
    # *Basketball*, not Big Bash cricket.
    "KXIPLGAME", "KXCRICKETT20IMATCH", "KXPSLGAME", "KXCOUNTYCHAMPMATCH",
    "KXT20MATCH", "KXWT20MATCH", "KXCRICKETWOMENT20IMATCH",
    # Soccer
    "KXEPLGAME", "KXBUNDESLIGAGAME", "KXLIGUE1GAME", "KXLALIGAGAME",
    "KXSERIEAGAME", "KXMLSGAME", "KXBRASILEIROGAME", "KXSAUDIPLGAME",
    "KXEREDIVISIEGAME", "KXNWSLGAME", "KXUECLGAME", "KXEUROCUPGAME",
    "KXUAEPLGAME", "KXCONMEBOLLIBGAME", "KXLIGAPORTUGALGAME",
    "KXELITESERIENGAME", "KXSUPERLIGGAME", "KXBELGIANPLGAME",
    "KXALEAGUEGAME", "KXLIGAMXGAME", "KXEFLL1GAME", "KXDFBPOKALGAME",
    "KXAFCACGAME", "KXINTLFRIENDLYGAME",
    # MLB / NBA / NHL / NFL
    "KXMLBGAME", "KXNBAGAME", "KXNHLGAME", "KXWNBAGAME",
    # UFC / Boxing
    "KXUFCFIGHT", "KXBOXINGFIGHT",
    # Esports
    "KXR6GAME", "KXDOTA2GAME", "KXLOLGAME", "KXCS2GAME",
    "KXVALORANTGAME", "KXCODGAME", "KXROCKETLEAGUEGAME",
    # Racing
    "KXMOTOGPRACE", "KXNASCARRACE", "KXF1RACE", "KXSAILGPRACE",
    "KXINDYCARRACE",
    # College
    "KXNCAAFGAME", "KXNCAAMBGAME", "KXNCAAWBGAME", "KXNCAABBGAME",
    # Rugby
    "KXNRLMATCH", "KXRUGBYFRA14MATCH", "KXRUGBYESLMATCH",
    # Basketball international
    "KXNBLGAME", "KXFIBAGAME", "KXEUROLEAGUEGAME", "KXABAGAME",
    "KXARGLNBGAME", "KXVTBGAME", "KXLNBELITEGAME",
    "KXNZNBLGAME", "KXGBLGAME",
    # (Removed KXSUMOWIN / KXLPGATOUR / KXATPSETWINNER / KXNTLFRIENDLY:
    # no head-to-head game-outcome series exists for these on Kalshi —
    # they were tournament/set/futures markets that can never pass the
    # GAME/MATCH/FIGHT/RACE suffix rule. Verified live 2026-05-16.)
]

# Estimated typical game/match duration in minutes — used to estimate
# when a live game should be over. Generous on the upper end so we don't
# drop matches that go long.
SPORT_DURATION_MINUTES = {
    "Tennis": 240,            # 5-set matches can run 4h+
    "Cricket": 300,           # IPL T20 ~3.5h, but allow 5h for delays
    "IPL": 300,
    "Soccer": 150,            # 90 + injury + halftime
    "MLB": 240,
    "NBA": 180,
    "WNBA": 180,
    "Basketball": 180,
    "NHL": 200,
    "Hockey": 200,
    "NFL": 240,
    "NCAA": 200,
    "NCAA Basketball": 180,
    "NCAA Football": 240,
    "NCAA Baseball": 240,
    "UFC": 120,               # full card spans hours
    "Boxing": 120,
    "Esports": 180,
    "PGA": 360,               # round can be 5-6h
    "LPGA": 360,
    "Golf": 360,
    "MotoGP": 180,
    "NASCAR": 300,
    "F1": 180,
    "IndyCar": 240,
    "SailGP": 180,
    "Sumo": 60,
    "Rugby": 130,
    "Australian Rules": 150,
    "Lacrosse": 150,
    "Field Hockey": 130,
    "Cycling": 360,
}
DEFAULT_DURATION_MINUTES = 180

# Series-ticker prefix -> human sport label. Order matters: more-specific
# prefixes first (e.g. KXIPLGAME before KX).
SPORT_LABEL_RULES = [
    # --- Cricket / IPL labels. Order: most-specific first. These only
    # set the sport label/badge. Priority (gate-exempt + pinned top) is
    # IPL-ONLY as of 2026-05-16; non-IPL cricket obeys the 83-98% gate
    # like every other sport (see _evaluate_event / KALSHI_SPEC.md §6). ---
    ("KXIPL",             "IPL"),       # special-cased for priority
    ("KXT20",             "Cricket"),   # KXT20MATCH (men's assoc. T20)
    ("KXWT20",            "Cricket"),   # KXWT20MATCH (women's T20)
    ("KXPSL",             "Cricket"),
    ("KXCRICKET",         "Cricket"),   # KXCRICKETT20IMATCH, KXCRICKETWOMEN*
    ("KXCOUNTYCHAMP",     "Cricket"),   # KXCOUNTYCHAMPMATCH (was unlabeled)

    ("KXATPMATCH",        "Tennis"),
    ("KXATPCHALLENGER",   "Tennis"),
    ("KXATPSET",          "Tennis"),
    ("KXATP",             "Tennis"),
    ("KXWTAMATCH",        "Tennis"),
    ("KXWTAGAME",         "Tennis"),
    ("KXWTA",             "Tennis"),
    ("KXITF",             "Tennis"),
    ("KXGRANDSLAM",       "Tennis"),
    ("KXTENNIS",          "Tennis"),

    ("KXMLB",             "MLB"),
    ("KXNBA",             "NBA"),
    ("KXWNBA",            "WNBA"),
    ("KXNHL",             "NHL"),
    ("KXNFL",             "NFL"),

    ("KXPGA",             "PGA"),
    ("KXLPGA",            "LPGA"),
    ("KXGOLF",            "Golf"),
    ("KXKFTOUR",          "Golf"),

    ("KXEPL",             "Soccer"),
    ("KXBUNDESLIGA",      "Soccer"),
    ("KXLIGUE1",          "Soccer"),
    ("KXLALIGA",          "Soccer"),
    ("KXSERIEA",          "Soccer"),
    ("KXMLS",             "Soccer"),
    ("KXNWSL",            "Soccer"),
    ("KXBRASILEIRO",      "Soccer"),
    ("KXSAUDIPL",         "Soccer"),
    ("KXEREDIVISIE",      "Soccer"),
    ("KXLIGAPORTUGAL",    "Soccer"),
    ("KXELITESERIEN",     "Soccer"),
    ("KXUCL",             "Soccer"),
    ("KXUEL",             "Soccer"),
    ("KXUECL",            "Soccer"),
    ("KXWC",              "Soccer"),
    ("KXSUPERLIG",        "Soccer"),
    ("KXBELGIANPL",       "Soccer"),
    ("KXALEAGUE",         "Soccer"),
    ("KXCONMEBOL",        "Soccer"),
    ("KXLIGAMX",          "Soccer"),
    ("KXUAEPL",           "Soccer"),
    ("KXEFLL1",           "Soccer"),   # EFL League One Game
    ("KXDFBPOKAL",        "Soccer"),   # DFB Pokal Game
    ("KXAFCAC",           "Soccer"),   # AFC Asia Cup Game
    ("KXINTLFRIENDLY",    "Soccer"),   # International Friendly Game

    ("KXUFC",             "UFC"),
    ("KXBOXING",          "Boxing"),

    ("KXR6GAME",          "Esports"),
    ("KXDOTA2",           "Esports"),
    ("KXLOL",             "Esports"),
    ("KXCS2",             "Esports"),
    ("KXVALORANT",        "Esports"),
    ("KXROCKETLEAGUE",    "Esports"),
    ("KXCOD",             "Esports"),

    ("KXMOTOGP",          "MotoGP"),
    ("KXNASCAR",          "NASCAR"),
    ("KXF1",              "F1"),
    ("KXINDYCAR",         "IndyCar"),
    ("KXSAILGP",          "SailGP"),
    ("KXCYCLING",         "Cycling"),

    ("KXBBL",             "Basketball"),
    ("KXNZNBL",           "Basketball"),
    ("KXGBL",             "Basketball"),
    ("KXEUROLEAGUE",      "Basketball"),
    ("KXEUROCUP",         "Basketball"),
    ("KXABA",             "Basketball"),
    ("KXLNB",             "Basketball"),
    ("KXNBL",             "Basketball"),   # NBL Basketball Game (AU)
    ("KXFIBA",            "Basketball"),   # FIBA Game
    ("KXARGLNB",          "Basketball"),   # Liga Nacional de Basquetbol
    ("KXVTB",             "Basketball"),   # VTB United League Game

    ("KXNCAAMB",          "NCAA Basketball"),
    ("KXNCAAWB",          "NCAA Basketball"),
    ("KXNCAAF",           "NCAA Football"),
    ("KXNCAAB",           "NCAA Baseball"),
    ("KXMARMAD",          "NCAA Basketball"),
    ("KXWMARMAD",         "NCAA Basketball"),

    ("KXNRL",             "Rugby"),
    ("KXRUGBY",           "Rugby"),
    ("KXFRA14",           "Rugby"),

    ("KXSUMO",            "Sumo"),
    ("KXLACR",            "Lacrosse"),
    ("KXLAX",             "Lacrosse"),
    ("KXPLL",             "Lacrosse"),
    ("KXAFL",             "Australian Rules"),
    ("KXHNL",             "Hockey"),
    ("KXSWISSLEAGUE",     "Hockey"),
    ("KXKLEAGUE",         "Hockey"),
    ("KXCZEFL",           "Hockey"),
    ("KXECUL",            "Hockey"),
    ("KXCHLLD",           "Hockey"),
]


def _http_get_json(url: str, timeout: int = 25, retries: int = 3) -> dict:
    """GET JSON with retry/backoff on 429. Kalshi's edge is aggressive
    about rate-limiting bursty traffic — small backoffs let us coexist
    cleanly with the 1-minute refresh loop."""
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://kalshi.com",
                "Referer": "https://kalshi.com/",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 and attempt < retries:
                # Exponential backoff: 0.8s, 1.6s, 3.2s
                time.sleep(0.8 * (2 ** attempt))
                continue
            raise
        except Exception as e:
            last_err = e
            raise
    if last_err:
        raise last_err
    return {}


def _is_game_outcome_series(series_ticker: str) -> bool:
    """True if the series looks like a per-game/per-match outcome market."""
    if not series_ticker:
        return False
    s = series_ticker.upper()
    return any(s.endswith(suf) for suf in LIVE_GAME_SUFFIXES)


def _sport_label_for_series(series_ticker: str) -> str:
    if not series_ticker:
        return ""
    for prefix, label in SPORT_LABEL_RULES:
        if series_ticker.startswith(prefix):
            return label
    return ""


def _parse_iso(iso_ts: str | None):
    if not iso_ts:
        return None
    try:
        return datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _parse_dollars(s) -> float:
    try:
        return float(s or 0)
    except (TypeError, ValueError):
        return 0.0


def _load_series_title_cache() -> dict:
    """Return {series_ticker: title}, always seeded with the committed
    SEED_SERIES_TITLES so a fresh Railway container does not cold-fetch
    ~70 titles in a burst. A fresh on-disk cache overrides the seed for
    any title that has since changed."""
    seed = dict(SEED_SERIES_TITLES)
    try:
        if not os.path.exists(SERIES_TITLE_CACHE_PATH):
            return seed
        age = time.time() - os.path.getmtime(SERIES_TITLE_CACHE_PATH)
        if age > SERIES_TITLE_CACHE_TTL_SECONDS:
            return seed
        with open(SERIES_TITLE_CACHE_PATH) as f:
            disk = json.load(f) or {}
        seed.update(disk)
        return seed
    except Exception:
        return seed


def _save_series_title_cache(titles: dict) -> None:
    try:
        with open(SERIES_TITLE_CACHE_PATH, "w") as f:
            json.dump(titles, f, separators=(",", ":"), sort_keys=True)
    except Exception as e:
        print(f"[kalshi] title cache save error: {e}")


def _fetch_series_title(series_ticker: str) -> str:
    """Fetch one series's title. Returns "" on error."""
    try:
        d = _http_get_json(f"{API_BASE}/series/{series_ticker}", timeout=10)
        return ((d.get("series") or {}).get("title") or "")
    except Exception:
        return ""


def _ensure_titles(cache: dict, series_tickers: list[str]) -> dict:
    """Ensure every ticker in `series_tickers` has a title in `cache`.
    Lazily fetches missing entries in parallel and persists the updated cache."""
    missing = [s for s in series_tickers if s not in cache]
    if not missing:
        return cache
    print(f"[kalshi] fetching titles for {len(missing)} new series…")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for ticker, title in zip(missing, ex.map(_fetch_series_title, missing)):
            cache[ticker] = title  # store even if "" so we don't retry every run
    _save_series_title_cache(cache)
    return cache


def _slugify_series_title(title: str) -> str:
    """Kalshi's URL slug rule (spec KALSHI_SPEC.md §8), derived from real
    URLs: title.lower() with spaces -> '-'. Trailing spaces become
    trailing dashes (Kalshi preserves them).

    OPEN QUESTION (unverified): for a series whose title contains an
    apostrophe (e.g. "Men's T20 Cricket Match") this yields
    "men's-t20-cricket-match" with a literal apostrophe. Whether Kalshi's
    real web slug keeps or strips it is UNKNOWN — kalshi.com pages are
    Vercel-anti-bot (§2) so the rendered slug cannot be verified
    server-side. Do not add punctuation handling until confirmed against
    a real user-supplied Kalshi URL for a Men's/Women's series.
    """
    return title.lower().replace(" ", "-")


def _build_event_url(event_ticker: str | None,
                     series_ticker: str | None,
                     series_title: str | None) -> str:
    """Build Kalshi's canonical event-page URL.

    Pattern (confirmed from a user-shared example):
       /markets/{series-lower}/{slugified-series-title}/{event-ticker-lower}
       e.g. /markets/kxatpmatch/atp-tennis-match/kxatpmatch-26may15sinmed

    Falls back to the series page if we don't have a title for this series
    yet (the title cache populates on the next refresh)."""
    if not series_ticker:
        return "https://kalshi.com/calendar"
    series_lower = series_ticker.lower()
    if event_ticker and series_title:
        slug = _slugify_series_title(series_title)
        event_lower = event_ticker.lower()
        return f"https://kalshi.com/markets/{series_lower}/{slug}/{event_lower}"
    # Fallback: series page works (we just lose the specific event drill-down)
    return f"https://kalshi.com/markets/{series_lower}"


def _ends_in_minutes(occurrence_dt, sport_label: str) -> int | None:
    """Estimate remaining game time in minutes (negative if already past)."""
    if not occurrence_dt:
        return None
    duration = SPORT_DURATION_MINUTES.get(sport_label, DEFAULT_DURATION_MINUTES)
    end_dt = occurrence_dt + _timedelta_minutes(duration)
    diff = (end_dt - datetime.now(timezone.utc)).total_seconds() / 60
    return int(round(diff))


def _timedelta_minutes(m: int):
    from datetime import timedelta
    return timedelta(minutes=m)


# How far before the scheduled start to consider a match "live". Kalshi's
# UI marks matches LIVE on its calendar significantly before they
# technically start, and `occurrence_datetime` from the API is the
# originally-scheduled time (not actual start). Tennis is especially
# noisy: tournaments with multiple courts run matches sequentially, so a
# match scheduled for 4pm can actually start at 2:30pm if the prior
# court match ends quickly — and vice versa.
#
# These thresholds match what Kalshi shows on their live calendar.
PRE_GAME_BUFFER_MINUTES = {
    "Tennis":   150,  # 2.5h — covers ATP/WTA/ITF/Challenger schedule slop
    "Cricket":   60,
    "IPL":       60,
}
DEFAULT_PRE_GAME_BUFFER = 60


def _is_live_now(occurrence_dt, sport_label: str) -> bool:
    """A game is live iff (occurrence - pre_game_buffer) <= now <= (occurrence + duration)."""
    if not occurrence_dt:
        return False
    now = datetime.now(timezone.utc)
    buffer_min = PRE_GAME_BUFFER_MINUTES.get(sport_label, DEFAULT_PRE_GAME_BUFFER)
    duration_min = SPORT_DURATION_MINUTES.get(sport_label, DEFAULT_DURATION_MINUTES)
    start_window = occurrence_dt - _timedelta_minutes(buffer_min)
    end_window = occurrence_dt + _timedelta_minutes(duration_min)
    return start_window <= now <= end_window


def _select_event_sides(event_markets: list[dict]) -> list[dict]:
    """Return the matchup sides to display for one event.

    - 2-market events (head-to-head): both sides, sorted by YES bid desc.
    - >2-market events (race, tournament): only the highest-YES side.
    Skips markets without bid data.
    """
    candidates = []
    for m in event_markets:
        yb = _parse_dollars(m.get("yes_bid_dollars"))
        if yb <= 0:
            continue  # market has no YES side liquidity / not active
        candidates.append({
            "name": (m.get("yes_sub_title") or "").strip(),
            "yes_pct": int(round(yb * 100)),
            "market_ticker": m.get("ticker"),
        })
    candidates.sort(key=lambda x: -x["yes_pct"])
    if len(candidates) == 2:
        return candidates
    if candidates:
        return candidates[:1]
    return []


# Track most recent error so the orchestrator can surface it once per run.
# Mutated from parallel worker threads -> guard with a lock so the count
# is accurate (the bare += was a lossy read-modify-write under contention).
_LAST_ERROR: dict = {"count": 0, "sample": ""}
_LAST_ERROR_LOCK = threading.Lock()


def _fetch_series_events(series_ticker: str) -> list[dict]:
    """Fetch all open events for a single series. Returns [] on any error.

    Each call is small (one HTTP round-trip, typically <1s). Designed to
    be called concurrently for all sports series in parallel.
    """
    try:
        url = (
            f"{API_BASE}/events?status=open&with_nested_markets=true"
            f"&series_ticker={series_ticker}&limit=200"
        )
        d = _http_get_json(url, timeout=10)
        return d.get("events") or []
    except Exception as e:
        # Record one sample error per run so the operator can debug
        # without flooding logs from 70 parallel failures.
        with _LAST_ERROR_LOCK:
            _LAST_ERROR["count"] += 1
            if not _LAST_ERROR["sample"]:
                _LAST_ERROR["sample"] = f"{type(e).__name__}: {e}"
        return []


def _evaluate_event(e: dict, title_cache: dict) -> dict | None:
    """Apply the live + odds filter to ONE event. Returns the output
    record, or None if the event should not be shown. Pure per-event
    logic so the caller can isolate a single bad event's failure."""
    # Defensive: re-check category (should always be Sports given our
    # series list, but the API could change).
    if e.get("category") != "Sports":
        return None
    series_ticker = e.get("series_ticker") or ""
    if not _is_game_outcome_series(series_ticker):
        return None
    sport_label = _sport_label_for_series(series_ticker)
    # Per revised user requirement (2026-05-16): ONLY IPL is exempt from
    # the 83-98% gate and pinned to the top. All other cricket now obeys
    # the same 83-98% window as every other sport (this supersedes the
    # earlier "all cricket always shows" rule — see KALSHI_SPEC.md §6).
    is_priority = sport_label == "IPL"

    markets = e.get("markets") or []
    # All markets in an event share occurrence/expiration; use the first
    # populated one as the event's start time.
    sample_market = next((m for m in markets if m.get("occurrence_datetime")), None)
    occ_dt = _parse_iso((sample_market or {}).get("occurrence_datetime"))
    if not _is_live_now(occ_dt, sport_label):
        return None

    sides = _select_event_sides(markets)
    if not sides:
        return None

    top_pct = sides[0]["yes_pct"]
    # Only IPL passes the odds gate unconditionally; everything else
    # (incl. non-IPL cricket) needs the favorite within the user's window.
    if not is_priority:
        if not (MIN_FAVORITE_PCT * 100 <= top_pct <= MAX_FAVORITE_PCT * 100):
            return None

    return {
        "event_ticker": e.get("event_ticker"),
        "series_ticker": series_ticker,
        "sport_label": sport_label,
        "is_priority": is_priority,
        # Title is the matchup. Fall back to building it from the sides
        # if the event doesn't have a title.
        "event_title": (e.get("title") or "").strip()
            or " vs ".join(s["name"] for s in sides if s["name"])
            or series_ticker,
        "competition": ((e.get("product_metadata") or {}).get("competition") or "").strip(),
        "sides": sides,
        "favorite_pct": top_pct,
        "ends_in_minutes": _ends_in_minutes(occ_dt, sport_label),
        "url": _build_event_url(
            e.get("event_ticker"),
            series_ticker,
            title_cache.get(series_ticker, ""),
        ),
    }


def _event_sort_key(x: dict):
    """Sidebar ordering (explicit user requirement, revised 2026-05-16):
      tier 0 = IPL — ALWAYS pinned to the very top whenever live,
               regardless of odds or anything else. Tier 0 also means
               the MAX_OUTPUT_ITEMS cap can never truncate it.
      tier 1 = everything else (all already passed the 83-98% gate;
               non-IPL cricket is no longer special).
    Within a tier: higher favorite % first, then ending soonest."""
    tier = 0 if x["sport_label"] == "IPL" else 1
    return (
        tier,
        -x["favorite_pct"],
        x["ends_in_minutes"] if x["ends_in_minutes"] is not None else 9999,
    )


def fetch() -> dict | None:
    """Pull events for the curated sports-series list (in parallel), then
    filter to currently-live matchups."""
    t0 = time.time()
    qualified: list[dict] = []

    # Reset error tracking for this run.
    _LAST_ERROR["count"] = 0
    _LAST_ERROR["sample"] = ""

    # Phase 0: ensure we have series titles cached for URL construction.
    # Free on subsequent runs (7-day cache); on first run after a deploy
    # this adds ~5-15s for ~70 series titles, then is cached.
    title_cache = _load_series_title_cache()
    title_cache = _ensure_titles(title_cache, ACTIVE_LIVE_SPORTS_SERIES)

    # Phase 1: parallel-fetch all candidate series with conservative
    # concurrency. ~70 series × ~0.3s each = ~5s with 4 workers, well
    # within Kalshi's rate limits (we observed 429s at 12 workers).
    # Each worker retries on 429 with exponential backoff (see _http_get_json).
    all_events: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for events in ex.map(_fetch_series_events, ACTIVE_LIVE_SPORTS_SERIES):
            all_events.extend(events)

    if _LAST_ERROR["count"]:
        print(f"[kalshi] {_LAST_ERROR['count']}/{len(ACTIVE_LIVE_SPORTS_SERIES)} "
              f"series fetches errored. sample: {_LAST_ERROR['sample']}")

    # Phase 2: filter to live + odds window + emit matchup sides.
    # Each event is isolated — a single malformed event must never abort
    # the whole sidebar for this cycle (mirrors _fetch_series_events).
    for e in all_events:
        try:
            rec = _evaluate_event(e, title_cache)
        except Exception as ex:
            with _LAST_ERROR_LOCK:
                if not _LAST_ERROR["sample"]:
                    _LAST_ERROR["sample"] = f"event {type(ex).__name__}: {ex}"
            continue
        if rec is not None:
            qualified.append(rec)

    qualified.sort(key=_event_sort_key)  # IPL pinned top — see _event_sort_key
    top = qualified[:MAX_OUTPUT_ITEMS]

    return {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fetch_duration_seconds": round(time.time() - t0, 2),
        "series_queried": len(ACTIVE_LIVE_SPORTS_SERIES),
        "events_seen": len(all_events),
        "live_sports_count": len(qualified),
        "errors": {
            "count": _LAST_ERROR["count"],
            "sample": _LAST_ERROR["sample"],
        } if _LAST_ERROR["count"] else None,
        "events": top,
    }


def _is_total_fetch_failure(payload: dict) -> bool:
    """True iff this cycle fetched NOTHING because of errors (Kalshi
    rate-limited / blocked us), as opposed to the legitimate "fetched
    plenty, none currently live" case (events_seen > 0)."""
    errs = payload.get("errors") or {}
    return payload.get("events_seen", 0) == 0 and errs.get("count", 0) > 0


def write(payload: dict) -> bool:
    if not payload:
        return False

    # Stale-value fallback (mirrors data.json philosophy): if this cycle
    # got nothing due to fetch errors, keep the previous good events
    # instead of blanking the sidebar. A normal "no live games" cycle
    # (events_seen > 0, no errors) still writes through and clears it.
    if _is_total_fetch_failure(payload):
        try:
            with open(OUTPUT_PATH) as f:
                prev = json.load(f)
            if prev.get("events"):
                prev["errors"] = payload.get("errors")
                prev["stale"] = True
                prev["last_attempt_at"] = payload.get("fetched_at")
                payload = prev
        except Exception:
            pass  # no/!unreadable previous file -> fall through, write fresh

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    tmp = OUTPUT_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    os.replace(tmp, OUTPUT_PATH)
    return True


if __name__ == "__main__":
    p = fetch()
    if p:
        write(p)
        print(f"Wrote {OUTPUT_PATH}")
        print(f"  fetched_at:        {p['fetched_at']}")
        print(f"  series queried:    {p['series_queried']}")
        print(f"  events seen:       {p['events_seen']}")
        print(f"  live_sports_count: {p['live_sports_count']}")
        print(f"  output items:      {len(p['events'])}")
        print(f"  fetch duration:    {p['fetch_duration_seconds']}s")
        print()
        for e in p["events"]:
            prefix = "★ " if e["is_priority"] else "  "
            sides_str = "  /  ".join(f"{s['yes_pct']}% {s['name'][:18]}" for s in e["sides"])
            mins = e.get("ends_in_minutes")
            min_str = f"~{mins}m left" if (mins is not None and mins >= 0) else "ending"
            print(f"  {prefix}{e['sport_label']:<14}  {sides_str:<55}  ({min_str})")
            print(f"      title: {e['event_title']}")
            print(f"      url:   {e['url']}")
    else:
        print("FAILED")
