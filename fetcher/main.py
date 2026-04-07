#!/usr/bin/env python3
"""
Main fetcher script for Bitcoin/Macro dashboard.
Fetches data from all sources and writes public/data.json.
"""

import json
import os
import sys
from datetime import datetime, timezone

# Add parent directory to path so we can run from anywhere
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sources import coingecko, blockchain, fear_greed, fred, yahoo, etf_flows


# Paths relative to this script's location
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_JSON_PATH = os.path.join(PROJECT_DIR, "public", "data.json")
PREVIOUS_DATA_PATH = os.path.join(SCRIPT_DIR, "previous_data.json")


def load_previous_data():
    """Load previously saved data for computing deltas."""
    try:
        if os.path.exists(PREVIOUS_DATA_PATH):
            with open(PREVIOUS_DATA_PATH, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_previous_data(data):
    """Save current data for future delta computation."""
    try:
        with open(PREVIOUS_DATA_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[main] Warning: Could not save previous data: {e}")


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


def main():
    print("=" * 50)
    print(f"Fetcher started at {datetime.now(timezone.utc).isoformat()}")
    print("=" * 50)

    results = {}
    success_count = 0
    fail_count = 0

    # --- CoinGecko: Bitcoin + Stablecoins ---
    try:
        print("\n[1/6] Fetching CoinGecko data...")
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
        print("\n[2/6] Fetching block height...")
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
        print("\n[3/6] Fetching Fear & Greed Index...")
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

    # --- Macro Data (free APIs, no key needed) ---
    try:
        print("\n[4/6] Fetching macro economic data...")
        fred_data = fred.fetch()
        if fred_data:
            results["fred"] = fred_data
            success_count += 1
            fetched_series = ", ".join(fred_data.keys())
            print(f"  OK: Series fetched: {fetched_series}")
        else:
            fail_count += 1
            print("  FAILED: Macro data returned None")
    except Exception as e:
        fail_count += 1
        print(f"  FAILED: {e}")

    # --- Yahoo Finance: Asset Prices & Returns ---
    try:
        print("\n[5/6] Fetching Yahoo Finance data...")
        yahoo_data = yahoo.fetch()
        if yahoo_data:
            results["yahoo"] = yahoo_data
            success_count += 1
            print(f"  OK: Assets: {list(yahoo_data.get('current', {}).keys())}")
        else:
            fail_count += 1
            print("  FAILED: Yahoo returned None")
    except Exception as e:
        fail_count += 1
        print(f"  FAILED: {e}")

    # --- ETF Flows ---
    try:
        print("\n[6/6] Fetching ETF flow data...")
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

    # --- Assemble final data.json ---
    print("\n" + "=" * 50)
    print("Assembling data.json...")

    cg = results.get("coingecko", {})
    fred_results = results.get("fred", {})
    yahoo_results = results.get("yahoo", {})

    data = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),

        # Bitcoin
        "bitcoin": cg.get("bitcoin", {
            "price_usd": None,
            "change_24h_pct": None,
            "market_cap": None,
        }),

        # Block height
        "block_height": results.get("blockchain", {}).get("block_height", None),

        # Fear & Greed
        "fear_greed": results.get("fear_greed", {
            "value": None,
            "classification": None,
        }),

        # Macro indicators from FRED + Yahoo
        "macro": {
            "dxy": {
                "value": yahoo_results.get("current", {}).get("DXY", None),
                "change": None,
                "date": None,
            },
            "fed_funds_rate": {
                "value": fred_results.get("FEDFUNDS", {}).get("value", None),
                "change": fred_results.get("FEDFUNDS", {}).get("change", None),
                "date": fred_results.get("FEDFUNDS", {}).get("date", None),
            },
            "treasury_10y": {
                "value": fred_results.get("DGS10", {}).get("value", None),
                "change": fred_results.get("DGS10", {}).get("change", None),
                "date": fred_results.get("DGS10", {}).get("date", None),
            },
            "cpi": {
                "value": fred_results.get("CPIAUCSL", {}).get("value", None),
                "change": fred_results.get("CPIAUCSL", {}).get("change", None),
                "date": fred_results.get("CPIAUCSL", {}).get("date", None),
            },
            "sp500": {
                "value": yahoo_results.get("current", {}).get("SP500", None),
                "change": None,
                "date": None,
            },
        },

        # Debt indicators from FRED
        "debt": {
            "national_debt": {
                "value": fred_results.get("GFDEBTN", {}).get("value", None),
                "change": fred_results.get("GFDEBTN", {}).get("change", None),
                "date": fred_results.get("GFDEBTN", {}).get("date", None),
            },
            "debt_to_gdp": {
                "value": fred_results.get("GFDEGDQ188S", {}).get("value", None),
                "change": fred_results.get("GFDEGDQ188S", {}).get("change", None),
                "date": fred_results.get("GFDEGDQ188S", {}).get("date", None),
            },
            "deficit": {
                "value": fred_results.get("FYFSD", {}).get("value", None),
                "change": fred_results.get("FYFSD", {}).get("change", None),
                "date": fred_results.get("FYFSD", {}).get("date", None),
            },
        },

        # Stablecoins
        "stablecoins": cg.get("stablecoins", {
            "usdt": {"total_supply": None, "market_cap": None},
            "usdc": {"total_supply": None, "market_cap": None},
        }),

        # ETF Flows (add provider names)
        "etf_flows": _enrich_etf_flows(results.get("etf_flows")),

        # Asset Returns
        "asset_returns": yahoo_results.get("asset_returns", {}),
    }

    # Ensure public directory exists
    os.makedirs(os.path.dirname(DATA_JSON_PATH), exist_ok=True)

    # Write data.json
    with open(DATA_JSON_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {DATA_JSON_PATH}")

    # Save for future delta computation
    save_previous_data(data)

    # Summary
    print(f"\nDone: {success_count} succeeded, {fail_count} failed")
    print("=" * 50)


if __name__ == "__main__":
    main()
