"""Live Kalshi sports markets — narrow filter for the Macro-tab sidebar.

Pulls the public Kalshi API (no auth required), via the events endpoint
with ``with_nested_markets=true``. Surfaces only markets where the
underlying game is *currently being played*.

"Currently live" definition
---------------------------
A market is live iff:
    occurrence_datetime <= now <= occurrence_datetime + sport_duration

`occurrence_datetime` is Kalshi's scheduled start. We add a sport-specific
buffer (e.g. 2.5h tennis, 4h cricket) to estimate when the game should be
over. Games that haven't started yet are excluded; games whose buffer has
elapsed are excluded.

Display
-------
For each live event:
- If the event has exactly 2 markets (head-to-head matchup), we emit both
  sides so the sidebar pill shows e.g. "91% Sinner / 9% Medvedev".
- For multi-outcome events (race, tournament winner), we emit only the
  highest YES bid (the dominant favorite).

Special rules per user
----------------------
- Cricket / IPL live games are ALWAYS surfaced when live, regardless of
  the YES-bid range, and they're sorted at the top of the list.
- All other sports require favorite YES bid in [83%, 98%].

URL
---
We can't reliably verify Kalshi event-page URL slugs from server-side
(Vercel anti-bot blocks crawls). To avoid the user's earlier mismatch
problem (multiple pills resolving to the same wrong page), each pill
links to the series-page URL — guaranteed to land in the right series
where the user can see the live event listed and click through.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
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
    "KXATPMATCH", "KXATPCHALLENGERMATCH", "KXATPSETWINNER", "KXITFMATCH",
    "KXWTAGAME",
    # Cricket (priority per user)
    "KXIPLGAME", "KXCRICKETT20IMATCH", "KXPSLGAME", "KXCOUNTYCHAMPMATCH",
    "KXBBLCRICKET",
    # Soccer
    "KXEPLGAME", "KXBUNDESLIGAGAME", "KXLIGUE1GAME", "KXLALIGAGAME",
    "KXSERIEAGAME", "KXMLSGAME", "KXBRASILEIROGAME", "KXSAUDIPLGAME",
    "KXEREDIVISIEGAME", "KXNWSLGAME", "KXUECLGAME", "KXEUROCUPGAME",
    "KXUAEPLGAME", "KXCONMEBOLLIBGAME", "KXLIGAPORTUGALGAME",
    "KXELITESERIENGAME", "KXSUPERLIGGAME", "KXBELGIANPL",
    "KXALEAGUEGAME", "KXLIGAMXGAME", "KXEFLL1GAME", "KXDFBPOKALGAME",
    "KXAFCACGAME", "KXNTLFRIENDLY", "KXINTLFRIENDLYGAME",
    # MLB / NBA / NHL / NFL
    "KXMLBGAME", "KXNBAGAME", "KXNHLGAME", "KXWNBAGAME",
    # UFC / Boxing
    "KXUFCFIGHT", "KXBOXING",
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
    # Other
    "KXSUMOWIN", "KXLPGATOUR",
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
    ("KXIPL",             "IPL"),       # special-cased for priority
    ("KXT20",             "Cricket"),
    ("KXWT20",            "Cricket"),
    ("KXPSL",             "Cricket"),
    ("KXBBLCRICKET",      "Cricket"),

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


def _build_series_url(series_ticker: str | None) -> str:
    """Series-page URL — guaranteed to resolve correctly on Kalshi.

    We can't verify event-specific slugs from server-side (Vercel's
    anti-bot blocks our probes), so we link to the series page. Users
    land on a page listing every active event in that series, including
    the live one they clicked on — one extra click, but no mismatches.
    """
    if not series_ticker:
        return "https://kalshi.com/calendar"
    return f"https://kalshi.com/markets/{series_ticker.lower()}"


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


def _is_live_now(occurrence_dt, sport_label: str) -> bool:
    """A game is live iff (occurrence - pre_game_buffer) <= now <= (occurrence + duration).

    For most sports the pre-game buffer is 0 (strict "ball-rolling" filter
    per the user's spec). For Cricket/IPL it's 60 minutes: Kalshi marks
    these as "LIVE" up to an hour before the official start (to drum up
    pre-game trading), and the user wants the dashboard to match that.
    """
    if not occurrence_dt:
        return False
    now = datetime.now(timezone.utc)
    is_priority = sport_label in ("Cricket", "IPL")
    pre_buffer_min = 60 if is_priority else 0
    duration_min = SPORT_DURATION_MINUTES.get(sport_label, DEFAULT_DURATION_MINUTES)
    start_window = occurrence_dt - _timedelta_minutes(pre_buffer_min)
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
_LAST_ERROR: dict = {"count": 0, "sample": ""}


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
        _LAST_ERROR["count"] += 1
        if not _LAST_ERROR["sample"]:
            _LAST_ERROR["sample"] = f"{type(e).__name__}: {e}"
        return []


def fetch() -> dict | None:
    """Pull events for the curated sports-series list (in parallel), then
    filter to currently-live matchups."""
    t0 = time.time()
    qualified: list[dict] = []

    # Reset error tracking for this run.
    _LAST_ERROR["count"] = 0
    _LAST_ERROR["sample"] = ""

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
    for e in all_events:
        # Defensive: re-check category (should always be Sports given our
        # series list, but the API could change).
        if e.get("category") != "Sports":
            continue
        series_ticker = e.get("series_ticker") or ""
        if not _is_game_outcome_series(series_ticker):
            continue
        sport_label = _sport_label_for_series(series_ticker)
        is_priority = sport_label in ("Cricket", "IPL")

        markets = e.get("markets") or []
        # All markets in an event share occurrence/expiration; use the first
        # populated one as the event's start time.
        sample_market = next((m for m in markets if m.get("occurrence_datetime")), None)
        occ_dt = _parse_iso((sample_market or {}).get("occurrence_datetime"))
        if not _is_live_now(occ_dt, sport_label):
            continue

        sides = _select_event_sides(markets)
        if not sides:
            continue

        top_pct = sides[0]["yes_pct"]
        # Cricket / IPL pass the odds gate unconditionally; everything
        # else needs the favorite within the user's window.
        if not is_priority:
            if not (MIN_FAVORITE_PCT * 100 <= top_pct <= MAX_FAVORITE_PCT * 100):
                continue

        qualified.append({
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
            "url": _build_series_url(series_ticker),
        })

    # Sort: priority (cricket/IPL) first, then by favorite_pct desc, then
    # by earlier-ending first.
    qualified.sort(key=lambda x: (
        0 if x["is_priority"] else 1,
        -x["favorite_pct"],
        x["ends_in_minutes"] if x["ends_in_minutes"] is not None else 9999,
    ))
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


def write(payload: dict) -> bool:
    if not payload:
        return False
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
