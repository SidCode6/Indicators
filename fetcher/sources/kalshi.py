"""Live Kalshi sports markets — narrow filter for the Macro-tab sidebar.

Pulls the public Kalshi API (no auth required), via the events endpoint
with ``with_nested_markets=true`` (single ~20s scan returns everything we
need). Filters to:
  - events.category == "Sports"
  - market series_ticker ending in GAME/MATCH/FIGHT/RACE (game-outcome
    markets only — excludes prop bets, awards, drafts, season-long picks)
  - expected_expiration_time within the next 12 hours, or within the last
    2 hours (covers live games in progress or just-completed but
    still-open markets)
  - YES bid between 83% and 98% (we only surface markets where ONE side
    is heavily favored — naturally handles multi-outcome races by picking
    out the dominant pick rather than 40 NO-side near-certainties)

Sorts by YES bid descending. One entry per event_ticker (so a tennis
match contributes one pill, not two). Output written to
``public/kalshi.json`` and consumed by the Macro-tab sidebar.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone


API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = "Indicators-Dashboard/1.0 (personal use)"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FETCHER_DIR = os.path.dirname(SCRIPT_DIR)
PROJECT_DIR = os.path.dirname(FETCHER_DIR)
OUTPUT_PATH = os.path.join(PROJECT_DIR, "public", "kalshi.json")

# Filter knobs (user-spec)
MIN_FAVORITE_PCT = 0.83
MAX_FAVORITE_PCT = 0.98
WINDOW_MIN_MINUTES = -120        # accept games that ended in the last 2h
WINDOW_MAX_MINUTES = 12 * 60     # …or are expected to end within 12h
MAX_OUTPUT_ITEMS = 15

# Series suffixes that indicate a head-to-head GAME-level outcome (vs prop
# bets, awards, season-long picks, drafts, etc.).
LIVE_GAME_SUFFIXES = ("GAME", "MATCH", "FIGHT", "RACE")

# Series-ticker prefix -> human sport label for the UI badge.
# Order matters: longer/more-specific prefixes first.
SPORT_LABEL_RULES = [
    ("KXATPMATCH",        "Tennis"),
    ("KXATP",             "Tennis"),
    ("KXWTAMATCH",        "Tennis"),
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

    ("KXIPL",             "Cricket"),
    ("KXT20",             "Cricket"),
    ("KXWT20",            "Cricket"),
    ("KXPSL",             "Cricket"),

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

    ("KXUFC",             "UFC"),
    ("KXBOXING",          "Boxing"),
    ("KXWBC",             "Boxing"),
    ("KXMCGREGOR",        "Boxing"),

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
    ("KXHEISMAN",         "NCAA Football"),

    ("KXNRL",             "Rugby"),
    ("KXRUGBY",           "Rugby"),
    ("KXFRA14",           "Rugby"),

    ("KXSUMO",            "Sumo"),
    ("KXSB",              "NFL"),
    ("KXLACR",            "Lacrosse"),
    ("KXLAX",             "Lacrosse"),
    ("KXPLL",             "Lacrosse"),
    ("KXNCAA",            "NCAA"),
    ("KXHNL",             "Hockey"),
    ("KXSWISSLEAGUE",     "Hockey"),
    ("KXKLEAGUE",         "Hockey"),
    ("KXCZEFL",           "Hockey"),
    ("KXECUL",            "Hockey"),
    ("KXCHLLD",           "Hockey"),
    ("KXNZNBL",           "Basketball"),

    ("KXCHESS",           "Chess"),
    ("KXIMO",             "Olympics"),
    ("KXAFL",             "Australian Rules"),
    ("KXSCOT",            "Curling"),
    ("KXPSA",             "Squash"),
    ("KXFO",              "Field Hockey"),
    ("KXFLOYD",           "Boxing"),
]


def _http_get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ----- Helpers ------------------------------------------------------------

def _is_game_outcome_series(series_ticker: str) -> bool:
    """True if the series looks like a per-game / per-match outcome market.

    Excludes season-long picks, awards, drafts, and most prop bets — which
    is the difference between 'live sports right now' and 'whoever wins
    MVP some day next year'."""
    if not series_ticker:
        return False
    s = series_ticker.upper()
    return any(s.endswith(suffix) for suffix in LIVE_GAME_SUFFIXES)


def _sport_label_for_series(series_ticker: str) -> str:
    """Return a short human sport label, or '' if no rule matches."""
    if not series_ticker:
        return ""
    for prefix, label in SPORT_LABEL_RULES:
        if series_ticker.startswith(prefix):
            return label
    return ""


def _build_event_url(event_ticker: str | None, series_ticker: str | None) -> str:
    """Best-effort Kalshi event-page URL.

    Pattern: https://kalshi.com/markets/{series-lowercase}/{event-suffix-lowercase}
    where event-suffix is everything after the first dash in event_ticker.
    Falls back to the series page if we don't have a clean event_ticker.
    """
    series = (series_ticker or "").lower()
    if not series and event_ticker:
        series = (event_ticker or "").split("-")[0].lower()
    suffix = ""
    if event_ticker and "-" in event_ticker:
        suffix = event_ticker.split("-", 1)[1].lower()
    if series and suffix:
        return f"https://kalshi.com/markets/{series}/{suffix}"
    if series:
        return f"https://kalshi.com/markets/{series}"
    return "https://kalshi.com/calendar"


def _minutes_until(iso_ts: str | None) -> int | None:
    """Minutes from now until iso_ts. None on parse failure."""
    if not iso_ts:
        return None
    try:
        t = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        delta = (t - datetime.now(timezone.utc)).total_seconds() / 60
        return int(round(delta))
    except Exception:
        return None


def _parse_dollars(s) -> float:
    try:
        return float(s or 0)
    except (TypeError, ValueError):
        return 0.0


# ----- Main fetch ---------------------------------------------------------

def fetch() -> dict | None:
    """Pull events, filter to live sports in the user's odds window.

    Uses /events?status=open&with_nested_markets=true which returns ~7k
    events in one paginated scan (~15-25s). Filters in-memory by category,
    series suffix, expected_expiration_time window, and favorite %.
    """
    t0 = time.time()
    cursor: str | None = None
    events_scanned = 0
    markets_examined = 0
    qualified: list[dict] = []

    for _ in range(50):  # safety cap on pages
        qs = "status=open&with_nested_markets=true&limit=200"
        if cursor:
            qs += f"&cursor={cursor}"
        try:
            d = _http_get_json(f"{API_BASE}/events?{qs}", timeout=25)
        except Exception as e:
            print(f"[kalshi] events page error: {e}")
            break
        events = d.get("events") or []
        events_scanned += len(events)

        for e in events:
            if e.get("category") != "Sports":
                continue
            series_ticker = e.get("series_ticker") or ""
            if not _is_game_outcome_series(series_ticker):
                continue
            for m in (e.get("markets") or []):
                markets_examined += 1
                yb = _parse_dollars(m.get("yes_bid_dollars"))
                # Filter on YES bid only — for 2-sided markets this picks
                # the leading side (the other side is automatically <50%);
                # for multi-outcome races/tournaments this picks only the
                # genuinely-favored entries (not the 40 near-certain losers).
                if not (MIN_FAVORITE_PCT <= yb <= MAX_FAVORITE_PCT):
                    continue
                ends_in = _minutes_until(m.get("expected_expiration_time"))
                if ends_in is None or not (WINDOW_MIN_MINUTES <= ends_in <= WINDOW_MAX_MINUTES):
                    continue
                qualified.append({
                    "market_ticker": m.get("ticker"),
                    "event_ticker": e.get("event_ticker"),
                    "series_ticker": series_ticker,
                    "sport_label": _sport_label_for_series(series_ticker),
                    "favorite_pct": int(round(yb * 100)),
                    "favorite_name": (m.get("yes_sub_title") or "").strip(),
                    "event_title": (e.get("title") or "").strip() or (m.get("title") or "").strip(),
                    "ends_in_minutes": ends_in,
                    "close_time": m.get("close_time"),
                    "volume_24h": _parse_dollars(m.get("volume_24h_fp")),
                    "url": _build_event_url(e.get("event_ticker"), series_ticker),
                })
        cursor = d.get("cursor")
        if not cursor or not events:
            break

    # Sort: favorite_pct desc, then earlier-ending first, then volume desc.
    qualified.sort(key=lambda x: (
        -x["favorite_pct"],
        x.get("ends_in_minutes") if x.get("ends_in_minutes") is not None else 9999,
        -(x.get("volume_24h") or 0),
    ))

    # Dedupe by event_ticker: keep only the strongest favorite per event.
    # (Avoids surfacing both "Ruud wins" and "Darderi loses" — same info.)
    seen_events: set = set()
    deduped: list[dict] = []
    for q in qualified:
        et = q["event_ticker"]
        if et in seen_events:
            continue
        seen_events.add(et)
        deduped.append(q)

    top = deduped[:MAX_OUTPUT_ITEMS]

    return {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fetch_duration_seconds": round(time.time() - t0, 2),
        "events_scanned": events_scanned,
        "markets_examined": markets_examined,
        "qualifying_count": len(qualified),
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
        print(f"  events_scanned:    {p['events_scanned']}")
        print(f"  markets_examined:  {p['markets_examined']}")
        print(f"  qualifying:        {p['qualifying_count']}")
        print(f"  output items:      {len(p['events'])}")
        print(f"  fetch duration:    {p['fetch_duration_seconds']}s")
        for e in p["events"][:15]:
            mins = e.get("ends_in_minutes")
            min_str = f"{mins:+}m" if mins is not None else "—"
            print(f"    {e['favorite_pct']:>3}% {e['sport_label']:<10} {e['favorite_name'][:30]:<30} ends_in={min_str:<7} url={e['url']}")
    else:
        print("FAILED")
