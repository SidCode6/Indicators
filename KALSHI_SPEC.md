# KALSHI LIVE-SPORTS SIDEBAR — COMPLETE SPEC

This documents the Kalshi live-sports feature in the Indicators dashboard (`/Users/sudhamshreddy/Indicators`). The implementation is **`fetcher/sources/kalshi.py`** (single file, ~640 lines). Read this fully before changing anything Kalshi-related.

## 1. What the feature is

A fixed-position sidebar on the **Macro Intelligence tab** showing currently-live sports betting markets from Kalshi. Each "pill" is one live game with both sides' odds. Clicking a pill opens that exact Kalshi event page in a new tab.

- **Visible only** when: the Macro tab is active AND the viewport is ≥ 1300px wide. Hidden otherwise (does not disturb the rest of the dashboard / mobile).
- **Server side:** `fetcher/sources/kalshi.py` runs every **1 minute** via an independent loop in `start.sh`, writes `public/kalshi.json`.
- **Client side:** `public/app.js` polls `kalshi.json` every 1 minute and renders the sidebar; `public/index.html` has the `<aside id="kalshiSidebar">`; `public/styles.css` has all `.kalshi-*` styles.
- Failures are swallowed — a Kalshi outage never breaks the main dashboard.

## 2. Data source

- **API base:** `https://api.elections.kalshi.com/trade-api/v2` — public, **no auth required**.
- **Primary endpoint:** `/events?status=open&with_nested_markets=true&series_ticker={TICKER}&limit=200` — returns events + their nested markets (with prices).
- **Secondary endpoint:** `/series/{TICKER}` — used only to get a series's human title (for URL building).
- **Never** scrape Kalshi web pages (`kalshi.com/...`). They are Vercel-anti-bot protected and **always return HTTP 429** from any server (curl, WebFetch, Railway). Only real browsers pass. Don't waste time trying.

## 3. Which markets get shown — the filter chain

An event must pass ALL of these to appear:

1. **Series is in the curated list** `ACTIVE_LIVE_SPORTS_SERIES` (~164 series tickers as of 2026-05-16 — maximal coverage of every real game-outcome Sports series, e.g. `KXATPMATCH`, `KXIPLGAME`, `KXFACUPGAME`, `KXLALIGA2GAME`, `KXWTAMATCH`). We query these directly in parallel rather than scanning all ~7000 Kalshi events.
2. **`category == "Sports"`** (defensive re-check; Kalshi's `category=` query param is *ignored* by the API so we filter in code).
3. **Series ticker ends in `GAME` / `MATCH` / `FIGHT` / `RACE`** — this excludes prop bets, awards, drafts, season-long markets. Only head-to-head game outcomes.
4. **Currently live** (see §4 — the critical, subtle part).
5. **Has tradeable sides** — at least one market in the event with `yes_bid_dollars > 0`.
6. **Odds window:** the favorite's YES bid is in **[83%, 98%]** — **EXCEPT** Cricket/IPL which bypass this entirely (see §6).

## 4. "Currently live" detection (the part that causes confusion)

```
live  iff   (occurrence_datetime − pre_game_buffer) ≤ now ≤ (occurrence_datetime + sport_duration)
        OR   market_activity ≥ KALSHI_LIVE_MIN_ACTIVITY  AND  occ present AND |now−occ| ≤ 8h
```

**Two independent paths (2026-05-18).** The time-window path alone kept
missing live games because `occurrence_datetime` is routinely wrong by
*hours* (live ITF matches observed with occ 4.6h in the future; live IPL
~3.5h off). Per-sport buffer bumps were whack-a-mole. The robust path:
**market activity**. An actually-in-play market is heavily traded —
`open_interest_fp`/`volume_fp` in the tens-to-hundreds of THOUSANDS for
live matches, vs `0` for the ~146 not-yet-live scheduled ones (noise
≤ ~600). `_is_live_by_activity` treats `max(open_interest_fp,
volume_fp, volume_24h_fp) ≥ KALSHI_LIVE_MIN_ACTIVITY` (5000) as live —
but ONLY if `occurrence_datetime` is **present and within ±8h** of now
(`KALSHI_LIVE_SANITY_HOURS`). A heavily-traded market with a null or
far-future occ is a **futures/long-dated** market (e.g. World Cup
games 27 days out accumulate ~45k pre-bet OI) and is NOT live — these
must be excluded (explicit user rule: if liveness can't be confirmed,
do not show). Observed live-occ drift maxes ~4.6h (ITF) / ~5h (IPL),
so 8h covers real drift with margin while excluding futures. It is
**additive** — the OR never hides a previously-shown event, only
surfaces genuinely-live ones the timestamp
missed. The 83-98% favorite gate / IPL-priority still apply on top.

**Why the timestamp is tricky:** Kalshi's API fields are unreliable for "is it live":

- `expected_expiration_time` is **NOT the end time** — for most sports it's the *scheduled start*. Never use it for live detection.
- `occurrence_datetime` is the **originally-scheduled start**, NOT when the match actually started. Real matches drift ±2-3 hours: they start *early* when a prior court match ends quickly, *late* on delays. We've seen tennis matches clearly playing on Kalshi's UI (live scores visible) while the API's `occurrence_datetime` is still 50+ minutes in the future.

**The fix — sport-specific pre-game buffer** (`PRE_GAME_BUFFER_MINUTES` in kalshi.py):

| Sport | Pre-game buffer |
|---|---|
| **Tennis** | **150 min** (2.5h — tennis has the worst schedule slop) |
| Cricket (non-IPL) | 150 min |
| **IPL** | **300 min** (5h — IPL must never be hidden while live; see below) |
| everything else | **150 min** (`DEFAULT_PRE_GAME_BUFFER`, raised 60→150 on 2026-05-16) |

So a tennis match scheduled to start in ≤150 min is treated as live (it's usually actually playing). Genuinely-pre-game matches further out (e.g. Sinner-Medvedev 158 min away) are still excluded.

**IPL's 300-min buffer (and 360-min duration)** is deliberately huge because IPL must never be hidden while live (explicit user requirement) and Kalshi's `occurrence_datetime` drifts badly. Real case 2026-05-16: `KXIPLGAME-26MAY16GTKKR` was live at the innings break while its `occurrence_datetime` was still ~1.5h in the future (actual start ≈ 3.5h before nominal occ); a 60-min buffer hid it. It is bounded safely: single IPL games are ~24h apart so the wide window can't surface the next day's game. The one accepted trade-off: same-day **double-headers** (observed minimum gap 4h) will have overlapping windows, so both IPL games show at once — acceptable, both are IPL and both pin to the very top; showing an upcoming IPL slightly early is far better than hiding a live one. The real end signal is Kalshi *settling* the finished match (it then leaves `status=open` and drops); the 360-min duration is just a backstop.

**`sport_duration`** = `SPORT_DURATION_MINUTES` table (Tennis 240, Cricket 300, **IPL 360**, Soccer 150, NBA 180, MLB 240, NFL 240, UFC 120, Esports 180, NASCAR 300, etc.; default 180). After `occurrence + duration` elapses, the game is assumed over and drops off.

If you ever see "a live match isn't showing": first check market activity (`open_interest_fp`/`volume_fp`) — the activity path (§ above) should catch it regardless of the stale timestamp. Do **not** just keep widening `PRE_GAME_BUFFER_MINUTES` (that was the old whack-a-mole; the activity path replaced it). Buffers now only matter for genuinely low-volume live events with an accurate occ.

## 5. Sport labeling

`SPORT_LABEL_RULES` is an ordered list of `(series_prefix, label)`. First matching prefix wins, so **more-specific prefixes must come first** (e.g. `KXIPL → "IPL"` is listed before generic cricket prefixes). The label drives the UI badge and the priority logic.

## 6. IPL special rule (explicit user requirement — revised 2026-05-16)

**Only IPL is special.** `is_priority = (sport_label == "IPL")`.

For a live **IPL** match (`sport_label == "IPL"`):
- **Always shown when live** — the [83%, 98%] odds filter does NOT apply.
- **Pinned to the very top** of the sidebar (sort tier 0), above
  everything else, regardless of its odds or any other match's
  odds/ranking. The MAX_OUTPUT_ITEMS cap can never truncate it.
- Rendered with a **red-accent border + "LIVE" badge** in the UI.

**Everything else — including all non-IPL cricket (KXT20MATCH,
KXCRICKETT20IMATCH, KXCOUNTYCHAMPMATCH, KXPSLGAME, etc.) — must satisfy
the 83-98% favorite-odds window** and sorts normally by favorite %.

> History: this **supersedes** the earlier rule where *all* Cricket/IPL
> bypassed the gate and sorted to the top. As of 2026-05-16 the user
> narrowed it: only IPL is exempt/pinned; all other cricket is treated
> like any other sport. Cricket series still resolve to a "Cricket"
> label (badge text) — they just no longer get priority/gate-exemption.

## 7. Display / output shape

Per live event, `kalshi.json` emits:

- **2-market events** (head-to-head, e.g. tennis/most games): BOTH sides, e.g. `91% Sinner / 9% Medvedev`.
- **Multi-outcome events** (NASCAR race, tournament winner, many markets): ONLY the single highest-YES favorite (showing 40 "driver loses" rows would be noise).

Sort order: (1) tier — IPL (0) then everything else (1); (2) favorite % descending; (3) earliest-ending first. Capped at **15** pills (`MAX_OUTPUT_ITEMS`); IPL is tier 0 so it can never be truncated out.

## 8. URL construction — the canonical event-page link

This is the rule that was confusing the sidebar earlier. Confirmed from real user-shared URLs:

```
https://kalshi.com/markets/{series-lower}/{slugified-series-title}/{event-ticker-lower}
```

- `series-lower` = `series_ticker.lower()` → e.g. `kxatpmatch`
- `slugified-series-title` = `series_title.lower().replace(" ", "-")` — **trailing spaces become trailing dashes** (Kalshi preserves them). Example: series title `"Challenger ATP "` → slug `challenger-atp-`. Series title `"ATP Tennis Match"` → `atp-tennis-match`.
- `event-ticker-lower` = `event_ticker.lower()` → e.g. `kxatpmatch-26may15sinmed`

Full example: `https://kalshi.com/markets/kxatpmatch/atp-tennis-match/kxatpmatch-26may15sinmed`

**Series titles are not in the events API response.** They come from `/series/{TICKER}` and are cached on disk at `fetcher/.kalshi_series_titles.json` (gitignored), 7-day TTL, populated lazily/in-parallel on first run (`_ensure_titles`). If a title is missing for a series, `_build_event_url` falls back to the series-page URL `https://kalshi.com/markets/{series-lower}` (works but lands on the series list, not the specific event — this was the old behavior that caused "tennis links go to the wrong match" because tennis has many concurrent matches while cricket has one).

## 9. Fetch architecture & rate limiting

- **Parallel fetch:** `ThreadPoolExecutor(max_workers=4)` over the ~164 curated series (expanded 2026-05-16). Still 4 workers — do NOT raise concurrency (12 workers caused widespread 429s). The larger list raises per-cycle load; this is mitigated by the committed `SEED_SERIES_TITLES` (no cold title burst) and the `kalshi.json` stale-value fallback (a 429-storm cycle keeps the previous good data instead of blanking). Accepted tradeoff of the user's maximal-coverage choice.
- **Retry/backoff:** `_http_get_json` retries on HTTP 429 with exponential backoff 0.8s → 1.6s → 3.2s (3 retries).
- **Browser headers required:** Kalshi's edge drops requests with non-browser User-Agents from cloud IPs. We send a Mac-Chrome UA + `Accept` + `Origin: https://kalshi.com` + `Referer: https://kalshi.com/`. Without these, Railway gets empty/blocked responses (laptop sometimes works without — don't be fooled by local testing).
- **Error reporting:** failures are counted; `kalshi.json` includes an `errors: {count, sample}` field (null if none). Expect ~0-1 / 71 transient failures per run; that's fine.

## 10. kalshi.json shape

```json
{
  "fetched_at": "2026-05-15T19:01:52Z",
  "fetch_duration_seconds": 3.4,
  "series_queried": 71,
  "events_seen": 576,
  "live_sports_count": 2,
  "errors": null,
  "events": [
    {
      "event_ticker": "KXATPMATCH-26MAY15SINMED",
      "series_ticker": "KXATPMATCH",
      "sport_label": "Tennis",
      "is_priority": false,
      "event_title": "Sinner vs Medvedev",
      "competition": "ATP Rome",
      "sides": [
        {"name": "Jannik Sinner", "yes_pct": 86, "market_ticker": "..."},
        {"name": "Daniil Medvedev", "yes_pct": 13, "market_ticker": "..."}
      ],
      "favorite_pct": 86,
      "ends_in_minutes": 303,
      "url": "https://kalshi.com/markets/kxatpmatch/atp-tennis-match/kxatpmatch-26may15sinmed"
    }
  ]
}
```

## 11. Common "bugs" that are actually correct behavior

- **Sidebar empty:** No live game is in the 83-98% window AND no IPL is live. Correct. Near end-of-match favorites spike to 99-100% and correctly fall out of the window (non-IPL cricket included, as of the 2026-05-16 rule change).
- **A live match missing:** Almost always Kalshi's stale `occurrence_datetime` putting it outside that sport's pre-game buffer. Fix = widen `PRE_GAME_BUFFER_MINUTES[sport]`.
- **`live_sports_count: 0` with `events_seen: 500+`:** Working — lots of events fetched, none currently live in window.
- **`events_seen: 0`, fast duration, errors populated:** Kalshi rate-limited Railway. Check the `errors.sample` field; the browser UA + retry usually recovers next cycle.

## 12. How to add a new sport / series

1. Add the series ticker to `ACTIVE_LIVE_SPORTS_SERIES`.
2. Add a `(prefix, label)` rule to `SPORT_LABEL_RULES` (more-specific prefix first).
3. Add the sport's typical duration to `SPORT_DURATION_MINUTES` (and a buffer to `PRE_GAME_BUFFER_MINUTES` if its schedule is unreliable like tennis).
4. The series title auto-caches on next run; URL building just works.

## 13. Testing convention before any push

Run `python3 fetcher/sources/kalshi.py` locally (it works from a laptop). Verify: live events look right, both sides shown for head-to-heads, cricket/IPL on top with no odds filter, URLs match the §8 pattern. For logic changes, write synthetic-data unit tests (mock `_fetch_series_events`, fabricate events with controlled occurrence times / odds, assert `_is_live_now` and the filter chain). Then commit with a "why" message, push, wait ~2 min for Railway auto-deploy, verify `kalshi.json` live.

---

That's every rule and mechanism in the Kalshi feature.
