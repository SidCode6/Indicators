"""BTC chart data fetcher.

Pulls two BTC-USD price series from Yahoo Finance and writes them as a
compact JSON file that powers the price chart on the Macro tab:

- intraday: 7 days of hourly bars (~168 points) — 24H and 1W views
- daily:    10 years of daily closes (~3650 points) — 1M through 10Y views

The dashboard fetches ``public/charts/btc.json`` once and switches between
series client-side based on which timeframe button is active.
"""

import json
import os
from datetime import datetime, timezone

import yfinance as yf


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
CHART_DIR = os.path.join(PROJECT_DIR, "public", "charts")
CHART_PATH = os.path.join(CHART_DIR, "btc.json")


def _series_from_hist(hist):
    """Convert a yfinance history DataFrame to [[ts_ms, close], ...]."""
    out = []
    for idx, row in hist.iterrows():
        try:
            close = float(row["Close"])
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        # idx is a pandas Timestamp; .timestamp() returns POSIX seconds.
        ts_ms = int(idx.timestamp() * 1000)
        out.append([ts_ms, round(close, 2)])
    return out


def fetch():
    """Return the chart payload, or None on failure."""
    try:
        ticker = yf.Ticker("BTC-USD")

        intraday_hist = ticker.history(period="7d", interval="1h")
        intraday = _series_from_hist(intraday_hist) if not intraday_hist.empty else []

        daily_hist = ticker.history(period="10y", interval="1d")
        daily = _series_from_hist(daily_hist) if not daily_hist.empty else []

        if not intraday and not daily:
            return None

        return {
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "intraday": intraday,
            "daily": daily,
        }
    except Exception as e:
        print(f"[btc_chart] Error: {e}")
        return None


def write(payload):
    """Write the chart payload to public/charts/btc.json (compact JSON)."""
    if not payload:
        return False
    os.makedirs(os.path.dirname(CHART_PATH), exist_ok=True)
    tmp = CHART_PATH + ".tmp"
    with open(tmp, "w") as f:
        # Compact (no whitespace) to minimize bandwidth — ~100 KB instead of ~250 KB.
        json.dump(payload, f, separators=(",", ":"))
    os.replace(tmp, CHART_PATH)
    return True
