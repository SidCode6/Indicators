# Indicators dashboard — handoff / architecture doc

> **This is the source-of-truth doc for continuing work on this project.**
> If you're a new Claude session: read this top-to-bottom before making changes.

**Project:** Personal finance/market dashboard
**Local path:** `/Users/sudhamshreddy/Indicators`
**GitHub:** `git@github.com:SidCode6/Indicators.git` (default branch `main`, public repo)
**Live URL:** `https://indicators-production-5eab.up.railway.app/`
**Hosting:** Railway (auto-deploys on every push to `main`, ~1-2 min lead time)

## Architecture
- Single Python HTTP server (`server.py`) serving static files from `public/`
- One in-container fetcher loop (`fetcher/main.py`, every 10 min) for the main data
- One in-container Kalshi loop (`fetcher/sources/kalshi.py`, every 1 min) for live sports
- One GitHub Action (daily 16:30 UTC Mon-Fri) for NODE ETF snapshots — commits new snapshot files which auto-deploys
- All data files in `public/*.json`; frontend polls and renders client-side

## File layout
```
Indicators/
├── server.py                       # 30-line HTTP server (Cache-Control: no-cache for everything)
├── start.sh                        # boots both fetcher loops in background + serves
├── railway.toml, Procfile          # Railway config (don't touch)
├── requirements.txt                # requests, yfinance, beautifulsoup4, lxml (Railway installs)
├── HANDOFF.md                      # this file
├── fetcher/
│   ├── main.py                     # 10-min orchestrator: 8 source modules → public/data.json
│   ├── previous_data.json          # gitignored; stale-value fallback baseline
│   ├── sources/
│   │   ├── coingecko.py            # BTC + stablecoins
│   │   ├── blockchain.py           # BTC block height
│   │   ├── fear_greed.py           # F&G index (still fetched, not rendered)
│   │   ├── fred.py                 # Treasury yields (2Y/10Y/30Y/3M), Fed funds, CPI, debt, deficit
│   │   ├── yahoo.py                # 7 tickers: SP500/NASDAQ100/Gold/Oil/DXY/USDINR/BTC + asset_returns
│   │   ├── tickers.py              # MSTR/ASST/STRC/SATA (Yahoo)
│   │   ├── etf_flows.py            # Farside scraper (currently 403'd, kept harmless)
│   │   ├── btc_chart.py            # 7d hourly + 10y daily BTC bars → public/charts/btc.json
│   │   └── kalshi.py               # Live sports — see "Kalshi" section below
│   └── node_etf/                   # NODE ETF tracker subpackage
│       ├── fetch.py                # XLSX download + parse from vaneck.com
│       ├── analyze.py              # signal computation (flow-adjusted, etc.)
│       └── run.py                  # entry point for the daily GitHub Action
├── .github/workflows/
│   └── node-daily.yml              # daily cron, runs fetcher/node_etf/run.py, commits snapshot
└── public/
    ├── index.html                  # 3 SPA tabs: macro / assets / node
    ├── styles.css                  # ~1600 lines, dark theme with --bg-card/--green/--red etc.
    ├── app.js                      # ~1800 lines: tabs, renders, Quick Compare, Kalshi sidebar
    ├── data.json                   # written by fetcher/main.py every 10 min
    ├── kalshi.json                 # written by fetcher/sources/kalshi.py every 1 min
    ├── charts/btc.json             # written by fetcher/sources/btc_chart.py every 10 min
    └── node/
        ├── latest.json             # rebuilt by daily GH Action
        └── snapshots/YYYY-MM-DD.json   # immutable per-day snapshots, committed to git
```

## The three SPA tabs

### 1. Macro Intelligence (default)
- BTC price hero (just price + 24h % + 24h $ change; no block height)
- BTC chart (Chart.js v4, 8 timeframes: 24H/1W/1M/3M/1Y/3Y/5Y/10Y)
- 4 ticker cards: MSTR, ASST (Strive Inc), STRC, SATA
- 6 market metric cards: S&P 500, NASDAQ 100, Gold, Oil, DXY, USD/INR
- Debt & Credit: 3 treasury cards in one compact row — 3M T-Bill, 10Y, 30Y
- Fixed-position Kalshi live-sports sidebar on the right (only on screens ≥1300px wide)

### 2. BTC vs Assets
- "Asset Performance" header
- Existing comparison card (BTC/SP500/Gold returns over 1M/3M/6M/1Y/3Y/5Y)
- Date range below timeframe buttons (e.g. "May 15, 2025 → May 15, 2026")
- "Compare Anything" card — input → StockAnalysis comparison URL (1+ tickers, ~142 company-name aliases including multi-word like "Coca-Cola"/"Bank of America", hyphen/apostrophe handling)

### 3. NODE ETF Analysis
- Fund snapshot strip (NAV, Total Assets with day-over-day Δ, YTD, Positions, History)
- Section subtitle includes as-of date + history length
- Watchlist of HELD-only tickers (filters out NOT_HELD): HODL, MSTR — auto-resurfaces ASST/STRC if VanEck adds them
- Today's Changes — English-language events (added/exited/increased/reduced), flow-adjusted share deltas
- Multi-Day Patterns — accumulating / distributing (activates after ≥4 snapshots)
- All Holdings table — 60 positions with 1d Δshares, FUND tag on VanEck-own funds, ★ on watchlist tickers

## Top-right "Updated" badge
- Reads "Updated 5m ago (10:30 AM EDT)" — auto-switches EST/EDT via Intl.DateTimeFormat with `America/New_York`
- Hover tooltip lists per-block freshness (Bitcoin / Equities / Treasuries / Tickers / Block height)
- Backed by `_data_freshness` field in data.json; preserved across the stale-value fallback so stale blocks show their last-successful-fetch time

## Stale-value fallback (fetcher/main.py)
- After assembling the new data.json, recursively replace any "all-null subtree" with the corresponding subtree from previous_data.json
- Preserves freshness timestamps per block
- `last_updated` is the only key never substituted

## Kalshi sidebar (current implementation)
> **Full spec:** see `KALSHI_SPEC.md` (root) — complete filter chain, live-detection, URL rule, rate-limiting. Read it before any Kalshi change.
- **Trigger:** Macro tab active AND viewport ≥ 1300px
- **Refresh:** 1 minute (both server and client)
- **Filter:**
  - Sports category (Kalshi taxonomy)
  - Series ticker ending in GAME/MATCH/FIGHT/RACE (excludes prop bets, awards, drafts)
  - Currently live: `(occurrence_datetime − pre_game_buffer) ≤ now ≤ occurrence + sport_duration`
    - **Pre-game buffer is sport-specific** (`PRE_GAME_BUFFER_MINUTES` in kalshi.py): **Tennis = 150 min**, Cricket/IPL = 60 min, default = 60 min. This absorbs Kalshi's stale `occurrence_datetime` (tennis especially — matches start ±2h off schedule because courts run matches sequentially).
    - Sport duration table (`SPORT_DURATION_MINUTES`): Tennis 4h, Cricket 5h, Soccer 2.5h, basketball 3h, MLB 4h, NFL 4h, UFC 2h, Esports 3h, etc.
  - YES bid ∈ [83%, 98%] **for non-priority sports only**. Cricket & IPL always pass (always shown when live, sorted to top, red "LIVE" badge).
- **Output shape:**
  - For 2-market events (head-to-head): both sides shown with both YES%s (e.g. `91% Sinner / 9% Medvedev`)
  - For multi-outcome events: only the favorite
- **Sort:** Cricket/IPL first, then favorite_pct desc, then earliest-ending
- **URL pattern:** `https://kalshi.com/markets/{series-lower}/{slugified-series-title}/{event-ticker-lower}`. The middle segment is `series_title.lower().replace(" ", "-")` — trailing spaces become trailing dashes (e.g. `"Challenger ATP "` → `challenger-atp-`). Series titles aren't in the events API response, so the fetcher maintains a 7-day disk cache of `{series_ticker: title}` at `fetcher/.kalshi_series_titles.json` (gitignored), populated lazily via the `/series/{ticker}` endpoint. Falls back to series-page URL if title is missing.
- **HTTP:** browser-like UA + Origin/Referer headers; 4-worker parallelism; retry on 429 with exponential backoff (0.8 → 1.6 → 3.2s, 3 retries)
- **Curated series list:** `ACTIVE_LIVE_SPORTS_SERIES` in kalshi.py — ~70 game-outcome series. Add new ones here when needed.

## Important quirks
- **Railway IP gets 429'd by Kalshi** with bursty parallelism. Current settings (4 workers + browser UA + retry) work fine; expect ~1/71 transient failures per run, surfaced via `errors` field in kalshi.json
- **Kalshi events endpoint quirks:**
  - `category=Sports` query parameter is **ignored** — filter in code
  - `expected_expiration_time` for many sports is the SCHEDULED START, not the end. Don't use it. Use `occurrence_datetime` + sport_duration table.
  - **`occurrence_datetime` is also often wrong** — it's the originally-scheduled start, not actually-started time. Matches start earlier (when prior match ends quickly) and later (delays). The `_is_live_now` sport-specific pre-game buffer absorbs this slop (tennis needs 150 min).
  - List endpoint has a hidden `cursor` field that must be propagated; with `with_nested_markets=true` it returns full prices in `yes_bid_dollars` etc. (NOT `yes_bid` — that's deprecated and returns null)
  - Kalshi web pages are Vercel-anti-bot protected — you cannot curl/WebFetch them server-side (always 429). Can't verify URL slugs from the server; trust the documented slug rule above.
- **NODE ETF cookies:** VanEck redirects to /disabled-cookies without a disclaimer-accept cookie. Pre-set `ve-country-us=...disclaimer=true...` in the fetcher (already done).
- **FRED 2Y Treasury (DGS2) fails on Railway** but works locally (Railway IP blocked by FRED CSV endpoint). 2Y was dropped from UI anyway; can ignore.
- **Yahoo `period="5y"` underflows on equities** because 5 trading-year history is less than 5 calendar years (1825 days). Use `period="10y"` and a "find most recent close ≤ target date" lookup. Already fixed.

## What works well
- Auto-deploy from Railway is reliable
- Stale-value fallback prevents transient API failures from blanking the dashboard
- Quick Compare's company-name parser handles commas, "vs", spaces, hyphens, apostrophes, $-prefixes, dedupe
- Daily NODE GitHub Action has been running cleanly

## Known limitations / future work
- ETF Flows (Farside) has been 403'd for weeks; pre-existing, harmless
- 2Y Treasury data-layer fetcher fails on Railway only (works locally) — not rendered, so unfortunate but not blocking
- Per-block freshness tooltip is desktop-hover-only (no mobile equivalent)

## Working convention (the user expects this)
- Every change: read the relevant file → edit → run local regression (HTTP 200 on all routes, syntax check, structural greps, synthetic unit tests for logic) → commit with a detailed message explaining the "why" → push → wait for Railway auto-deploy → verify live
- Commit messages are the project changelog — explain reasoning, not just the diff
- `git log --oneline -20` shows recent context any time

## User preferences (always apply)
- Heavily BTC-focused; watchlist = MSTR, ASST (Strive Inc), STRC, SATA, HODL
- Wants clean English-language signals, not raw numbers ("they increased exposure to X", not "Δshares=+1247")
- Strict on regression: test thoroughly before pushing, verify live after
- Minimal/isolated changes; don't disturb unrelated parts of the dashboard
- Dashboard aesthetic must remain consistent (dark theme, IBM Plex, existing card style)

## Recent commits (most recent first)
- `da6a28a` Kalshi: link directly to the specific event page (canonical URL pattern)
- `1ac1699` Kalshi: 2.5h pre-game buffer for tennis specifically (was 60min)
- `e58fea7` Kalshi: extend 60-min pre-game buffer to all sports (Justo case)
- `47472cc` Kalshi: pre-game buffer for IPL/Cricket so live games surface on time
- `538db3f` Kalshi: rate-limit-friendly fetcher (browser UA + 4 workers + retry/backoff)
- `53b9957` Kalshi sidebar v2: live-only games, matchup format, cricket priority
- `e0c6042` Add live Kalshi sports sidebar to Macro tab
- `7c4a75d` Compare polish + Asset Performance header + timeframe date range
- `d711930` Remove BTC vs Assets header + add Quick Compare card (StockAnalysis)
- `37a97c7` Macro refinements: 3-card Treasuries + EST clock + per-block freshness
- `5ea604d` Phase 4: BTC price chart + ticker cards (MSTR / ASST / STRC / SATA)
- `b96071d` Phase 3: UI rebuild — 6 market metrics + new Debt & Credit
- `89905ea` Phase 2: data-layer expansion + stale-value fallback
- `addc0bc` Phase 1: Macro UI cleanup + 10-min refresh + ASST label fix
- `aa8e6d3` Remove Liquidity & Flows section
- `7175b2e` NODE section refinements (held-only watchlist, AUM delta, dividers)
- `15ea4c2` Add NODE ETF Analysis section
