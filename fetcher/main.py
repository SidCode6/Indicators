#!/usr/bin/env python3
"""
Main fetcher script for Bitcoin/Macro dashboard.
Fetches data from all sources and writes public/data.json.

Stale-value fallback: when a fetch fails entirely (or returns all-null leaves),
the prior values from previous_data.json are reused for that sub-block. This
prevents transient API failures (most often CoinGecko rate-limiting from
Railway's shared IPs) from blanking the dashboard. The fallback is applied at
the FINAL data assembly step, after all fetchers have run.
"""

import json
import os
import sys
from datetime import datetime, timezone

# Add parent directory to path so we can run from anywhere
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sources import coingecko, blockchain, fear_greed, fred, yahoo, etf_flows, tickers, btc_chart


# Paths relative to this script's location
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_JSON_PATH = os.path.join(PROJECT_DIR, "public", "data.json")
PREVIOUS_DATA_PATH = os.path.join(SCRIPT_DIR, "previous_data.json")


# ----- Stale-value fallback ------------------------------------------------

# Top-level keys that should NEVER be substituted from previous_data — they're
# either metadata about THIS run, or rebuilt every time.
NEVER_FALLBACK = {"last_updated"}


def _is_all_null(obj):
    """Recursively True if every leaf in obj is None (or obj itself is None).

    Empty containers are considered "all null" — if a fetcher returned an
    empty dict it almost certainly means the API failed.
    """
    if obj is None:
        return True
    if isinstance(obj, dict):
        return all(_is_all_null(v) for v in obj.values())
    if isinstance(obj, list):
        return all(_is_all_null(v) for v in obj)
    return False


def _fill_nulls_from_previous(new_data, prev_data, path=""):
    """Recursively merge prev_data into new_data wherever new_data has a
    null/empty subtree.

    - If new[k] is all-null and prev[k] has data, substitute the whole subtree.
    - If both are dicts, recurse.
    - Otherwise keep new[k].

    Returns a fresh dict (does not mutate new_data).
    """
    if not isinstance(new_data, dict) or not isinstance(prev_data, dict):
        return new_data

    out = {}
    for k, v in new_data.items():
        if k in NEVER_FALLBACK or k not in prev_data:
            out[k] = v
            continue
        prev_v = prev_data[k]
        if _is_all_null(v) and not _is_all_null(prev_v):
            out[k] = prev_v
            print(f"  [stale-fallback] {path}{k} reused from previous_data.json")
        elif isinstance(v, dict) and isinstance(prev_v, dict):
            out[k] = _fill_nulls_from_previous(v, prev_v, path=f"{path}{k}.")
        else:
            out[k] = v
    return out


def load_previous_data():
    """Load previously saved data for stale-value fallback."""
    try:
        if os.path.exists(PREVIOUS_DATA_PATH):
            with open(PREVIOUS_DATA_PATH, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_previous_data(data):
    """Save current data for the next run's stale-value fallback."""
    try:
        with open(PREVIOUS_DATA_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[main] Warning: Could not save previous data: {e}")


# ----- ETF Flows enrichment (unchanged) ------------------------------------

ETF_PROVIDERS = {
    "IBIT": "BlackRock", "FBTC": "Fidelity", "BITB": "Bitwise",
    "ARKB": "ARK/21Shares", "BTCO": "Invesco", "EZBC": "Franklin",
    "BRRR": "Valkyrie", "HODL": "VanEck", "BTCW": "WisdomTree",
    "GBTC": "Grayscale", "BTC": "Grayscale Mini",
}


def _enrich_etf_flows(etf_data):
    """Add provider names to ETF flow data."""
    if not etf_data:
        return {"date": None, "flows": {}, "total_daily_flow": None}
    enriched_flows = {}
    for ticker, value in etf_data.get("flows", {}).items():
        if isinstance(value, dict):
            enriched_flows[ticker] = value
        else:
            enriched_flows[ticker] = {
                "daily_flow_millions": value,
                "provider": ETF_PROVIDERS.get(ticker, ""),
            }
    return {
        "date": etf_data.get("date"),
        "flows": enriched_flows,
        "total_daily_flow": etf_data.get("total_daily_flow"),
    }


def _yahoo_value(yahoo_results, name):
    """Pull a scalar value from yahoo's new ``current`` dict shape (each
    asset is {value, change_value, change_pct})."""
    cur = (yahoo_results.get("current") or {}).get(name)
    if isinstance(cur, dict):
        return cur.get("value")
    return None


def _yahoo_change(yahoo_results, name):
    """Pull the % change from yahoo's new ``current`` dict shape."""
    cur = (yahoo_results.get("current") or {}).get(name)
    if isinstance(cur, dict):
        return cur.get("change_pct")
    return None


# ----- Main orchestration --------------------------------------------------

def main():
    print("=" * 50)
    print(f"Fetcher started at {datetime.now(timezone.utc).isoformat()}")
    print("=" * 50)

    results = {}
    success_count = 0
    fail_count = 0

    # --- CoinGecko: Bitcoin + Stablecoins ---
    try:
        print("\n[1/7] Fetching CoinGecko data...")
        cg_data = coingecko.fetch()
        if cg_data:
            results["coingecko"] = cg_data
            success_count += 1
            print("  OK: Bitcoin price, stablecoins")
        else:
            fail_count += 1
            print("  FAILED: CoinGecko returned None")
    except Exception as e:
        fail_count += 1
        print(f"  FAILED: {e}")

    # --- Blockchain.info: Block Height ---
    try:
        print("\n[2/7] Fetching block height...")
        bc_data = blockchain.fetch()
        if bc_data:
            results["blockchain"] = bc_data
            success_count += 1
            print(f"  OK: Block height = {bc_data.get('block_height', '?')}")
        else:
            fail_count += 1
            print("  FAILED: Blockchain returned None")
    except Exception as e:
        fail_count += 1
        print(f"  FAILED: {e}")

    # --- Fear & Greed Index ---
    try:
        print("\n[3/7] Fetching Fear & Greed Index...")
        fg_data = fear_greed.fetch()
        if fg_data:
            results["fear_greed"] = fg_data
            success_count += 1
            print(f"  OK: Fear & Greed = {fg_data.get('value', '?')} ({fg_data.get('classification', '?')})")
        else:
            fail_count += 1
            print("  FAILED: Fear & Greed returned None")
    except Exception as e:
        fail_count += 1
        print(f"  FAILED: {e}")

    # --- Macro Data (FRED + treasuries) ---
    try:
        print("\n[4/7] Fetching macro economic data...")
        fred_data = fred.fetch()
        if fred_data:
            results["fred"] = fred_data
            success_count += 1
            print(f"  OK: Series fetched: {', '.join(fred_data.keys())}")
        else:
            fail_count += 1
            print("  FAILED: Macro data returned None")
    except Exception as e:
        fail_count += 1
        print(f"  FAILED: {e}")

    # --- Yahoo Finance: Equities + FX + Crypto histories ---
    try:
        print("\n[5/7] Fetching Yahoo Finance data...")
        yahoo_data = yahoo.fetch()
        if yahoo_data:
            results["yahoo"] = yahoo_data
            success_count += 1
            print(f"  OK: Assets: {list((yahoo_data.get('current') or {}).keys())}")
        else:
            fail_count += 1
            print("  FAILED: Yahoo returned None")
    except Exception as e:
        fail_count += 1
        print(f"  FAILED: {e}")

    # --- ETF Flows ---
    try:
        print("\n[6/7] Fetching ETF flow data...")
        etf_data = etf_flows.fetch()
        if etf_data:
            results["etf_flows"] = etf_data
            success_count += 1
            print(f"  OK: ETF flows for {etf_data.get('date', '?')}, total: {etf_data.get('total_daily_flow', '?')}M")
        else:
            fail_count += 1
            print("  SKIPPED: ETF flows unavailable")
    except Exception as e:
        fail_count += 1
        print(f"  FAILED: {e}")

    # --- User-watchlist tickers (MSTR/ASST/STRC/SATA) ---
    try:
        print("\n[7/8] Fetching user ticker prices...")
        ticker_data = tickers.fetch()
        if ticker_data:
            results["tickers"] = ticker_data
            ok_tickers = [k for k, v in ticker_data.items() if v is not None]
            success_count += 1
            print(f"  OK: Tickers with data: {ok_tickers}")
        else:
            fail_count += 1
            print("  FAILED: Tickers returned None")
    except Exception as e:
        fail_count += 1
        print(f"  FAILED: {e}")

    # --- BTC Chart Data (intraday + daily series for the chart widget) ---
    try:
        print("\n[8/8] Fetching BTC chart data...")
        chart_payload = btc_chart.fetch()
        if chart_payload:
            btc_chart.write(chart_payload)
            success_count += 1
            print(f"  OK: BTC chart ({len(chart_payload.get('intraday', []))} intraday "
                  f"+ {len(chart_payload.get('daily', []))} daily points)")
        else:
            fail_count += 1
            print("  FAILED: BTC chart data unavailable")
    except Exception as e:
        fail_count += 1
        print(f"  FAILED: {e}")

    # --- Assemble final data.json ---
    print("\n" + "=" * 50)
    print("Assembling data.json...")

    cg = results.get("coingecko", {})
    fred_results = results.get("fred", {})
    yahoo_results = results.get("yahoo", {})
    ticker_results = results.get("tickers", {})

    data = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),

        # Bitcoin (CoinGecko)
        "bitcoin": cg.get("bitcoin", {
            "price_usd": None,
            "change_24h_pct": None,
            "market_cap": None,
        }),

        # Block height (Blockchain.info) — kept in data even though not rendered
        "block_height": results.get("blockchain", {}).get("block_height", None),

        # Fear & Greed — kept in data even though not rendered after Phase 1
        "fear_greed": results.get("fear_greed", {
            "value": None,
            "classification": None,
        }),

        # ---- LEGACY macro block (kept so the existing UI keeps working
        # until Phase 3 rebuilds the macro grid against the new shape) ----
        "macro": {
            "dxy": {
                "value": _yahoo_value(yahoo_results, "DXY"),
                "change": _yahoo_change(yahoo_results, "DXY"),
                "date": None,
            },
            "fed_funds_rate": {
                "value": fred_results.get("FEDFUNDS", {}).get("value"),
                "change": fred_results.get("FEDFUNDS", {}).get("change"),
                "date": fred_results.get("FEDFUNDS", {}).get("date"),
            },
            "treasury_10y": {
                "value": fred_results.get("DGS10", {}).get("value"),
                "change": fred_results.get("DGS10", {}).get("change"),
                "date": fred_results.get("DGS10", {}).get("date"),
            },
            "cpi": {
                "value": fred_results.get("CPIAUCSL", {}).get("value"),
                "change": fred_results.get("CPIAUCSL", {}).get("change"),
                "date": fred_results.get("CPIAUCSL", {}).get("date"),
            },
            "sp500": {
                "value": _yahoo_value(yahoo_results, "SP500"),
                "change": _yahoo_change(yahoo_results, "SP500"),
                "date": None,
            },
        },

        # ---- LEGACY debt block (kept for current UI until Phase 3) ----
        "debt": {
            "national_debt": {
                "value": fred_results.get("GFDEBTN", {}).get("value"),
                "change": fred_results.get("GFDEBTN", {}).get("change"),
                "date": fred_results.get("GFDEBTN", {}).get("date"),
            },
            "debt_to_gdp": {
                "value": fred_results.get("GFDEGDQ188S", {}).get("value"),
                "change": fred_results.get("GFDEGDQ188S", {}).get("change"),
                "date": fred_results.get("GFDEGDQ188S", {}).get("date"),
            },
            "deficit": {
                "value": fred_results.get("FYFSD", {}).get("value"),
                "change": fred_results.get("FYFSD", {}).get("change"),
                "date": fred_results.get("FYFSD", {}).get("date"),
            },
        },

        # Stablecoins (CoinGecko) — data kept in case the Liquidity UI is restored
        "stablecoins": cg.get("stablecoins", {
            "usdt": {"total_supply": None, "market_cap": None},
            "usdc": {"total_supply": None, "market_cap": None},
        }),

        # ETF Flows (with provider names) — also data-only after Phase 1
        "etf_flows": _enrich_etf_flows(results.get("etf_flows")),

        # ---- NEW PHASE 2 BLOCKS ----

        # Market metrics that Phase 3 will render as the new metric grid.
        # Each value is {value, change_value, change_pct} from Yahoo.
        "equities": {
            name: (yahoo_results.get("current") or {}).get(name)
            for name in ("SP500", "NASDAQ100", "GOLD", "OIL", "DXY", "USDINR")
        },

        # New Debt & Credit metrics (Phase 3 will render these).
        "treasuries": {
            "DGS2":  fred_results.get("DGS2"),
            "DGS10": fred_results.get("DGS10"),
            "DGS30": fred_results.get("DGS30"),
            "DTB3":  fred_results.get("DTB3"),
        },

        # User-watchlist ticker prices (Phase 4 will render under the BTC chart).
        "tickers": {
            sym: ticker_results.get(sym) for sym in ("MSTR", "ASST", "STRC", "SATA")
        },

        # Asset Returns (BTC vs Assets section). Yahoo now computes returns
        # for every ticker (so they're available for the new chart/metrics),
        # but the BTC-vs-Assets ranking is intentionally limited to the same
        # three assets it has always compared. Extending that view is a
        # separate UX decision; not changing it here.
        "asset_returns": {
            tf: {sym: v for sym, v in vals.items() if sym in ("BTC", "GOLD", "SP500")}
            for tf, vals in (yahoo_results.get("asset_returns") or {}).items()
        },
    }

    # --- Per-block freshness ---
    # For each top-level block, record THIS run's timestamp iff the
    # underlying source(s) succeeded. Blocks that failed get None here;
    # the stale-value fallback below will then restore the prior block AND
    # the prior block's freshness timestamp together. The result: the UI
    # can show "last successfully updated: HH:MM" for each block.
    now_iso = data["last_updated"]
    cg_ok      = results.get("coingecko") is not None
    yahoo_ok   = results.get("yahoo") is not None
    fred_ok    = results.get("fred") is not None
    tickers_ok = results.get("tickers") is not None
    bc_ok      = results.get("blockchain") is not None
    fg_ok      = results.get("fear_greed") is not None
    etf_ok     = results.get("etf_flows") is not None
    data["_data_freshness"] = {
        "bitcoin":       now_iso if cg_ok else None,
        "block_height":  now_iso if bc_ok else None,
        "fear_greed":    now_iso if fg_ok else None,
        "macro":         now_iso if (yahoo_ok or fred_ok) else None,
        "debt":          now_iso if fred_ok else None,
        "stablecoins":   now_iso if cg_ok else None,
        "etf_flows":     now_iso if etf_ok else None,
        "equities":      now_iso if yahoo_ok else None,
        "treasuries":    now_iso if fred_ok else None,
        "tickers":       now_iso if tickers_ok else None,
        "asset_returns": now_iso if yahoo_ok else None,
    }

    # --- Stale-value fallback: substitute any all-null sub-block from previous ---
    # Because _data_freshness is *not* in NEVER_FALLBACK, any timestamp set to
    # None above will be restored from previous_data.json — preserving the
    # accurate "last successful fetch" time per block.
    previous = load_previous_data()
    if previous:
        data = _fill_nulls_from_previous(data, previous)

    # Ensure public directory exists
    os.makedirs(os.path.dirname(DATA_JSON_PATH), exist_ok=True)

    # Write data.json
    with open(DATA_JSON_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {DATA_JSON_PATH}")

    # Save the (post-fallback) snapshot for the next run's fallback baseline
    save_previous_data(data)

    print(f"\nDone: {success_count} succeeded, {fail_count} failed")
    print("=" * 50)


if __name__ == "__main__":
    main()
