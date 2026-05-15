"""Asset prices + historical returns via yfinance.

For each ticker we publish:
- current value
- 24h $ and % change (current close vs prior close)
- historical returns over 1M / 3M / 6M / 1Y / 3Y / 5Y

The BTC row reuses the same history so the front-end chart in Phase 4 can
draw arbitrary timeframes off ``asset_history.BTC`` without an extra round
trip.
"""

import yfinance as yf
from datetime import datetime, timedelta, timezone


# Mapping of internal name → Yahoo Finance symbol.
# Keep keys stable: the UI references them by these names.
TICKERS = {
    "DXY": "DX-Y.NYB",     # US Dollar Index
    "SP500": "^GSPC",       # S&P 500
    "NASDAQ100": "^NDX",    # Nasdaq-100 Index (user prefers 100 over Composite)
    "GOLD": "GC=F",         # Gold futures
    "OIL": "CL=F",          # WTI Crude futures
    "USDINR": "INR=X",      # USD/INR exchange rate
    "BTC": "BTC-USD",       # Bitcoin (also feeds the chart in Phase 4)
}

TIMEFRAMES = {
    "1M": 30,
    "3M": 90,
    "6M": 180,
    "1Y": 365,
    "3Y": 365 * 3,
    "5Y": 365 * 5,
}

# 10 years covers the longest user timeframe (10Y) plus a safety margin.
# Using a fixed period rather than "max" keeps fetch sizes consistent and
# avoids edge cases with tickers that have decades of history.
HISTORY_PERIOD = "10y"


def _calculate_return(current, past):
    if past is None or past == 0:
        return None
    return round(((current - past) / past) * 100, 2)


def _get_price_at_offset(hist, days_ago):
    """Last close on or before `days_ago` days ago. Returns None if the
    history doesn't reach that far back."""
    if hist.empty:
        return None
    target = datetime.now(timezone.utc) - timedelta(days=days_ago)
    try:
        if hist.index.tz is not None:
            mask = hist.index <= target
        else:
            mask = hist.index <= target.replace(tzinfo=None)
    except TypeError:
        naive_index = hist.index.tz_localize(None) if hist.index.tz else hist.index
        mask = naive_index <= target.replace(tzinfo=None)
        hist = hist.copy()
        hist.index = naive_index
    if mask.any():
        return float(hist.loc[mask, "Close"].iloc[-1])
    return None


def _build_current(hist):
    """Latest close + 24h $ and % change. Returns None if no data."""
    if hist.empty:
        return None
    last_close = float(hist["Close"].iloc[-1])
    prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else None
    change_value = round(last_close - prev_close, 2) if prev_close is not None else None
    change_pct = (
        round((last_close - prev_close) / prev_close * 100, 2)
        if prev_close not in (None, 0)
        else None
    )
    return {
        "value": round(last_close, 2),
        "change_value": change_value,
        "change_pct": change_pct,
    }


def fetch():
    """Fetch all asset histories and compute current + returns."""
    current = {}
    histories = {}
    asset_returns = {tf: {} for tf in TIMEFRAMES}

    for name, symbol in TICKERS.items():
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(period=HISTORY_PERIOD)
            if hist.empty:
                print(f"[yahoo] No data for {name} ({symbol})")
                continue
            histories[name] = hist
            cur = _build_current(hist)
            if cur is not None:
                current[name] = cur
        except Exception as e:
            print(f"[yahoo] {name} error: {e}")
            continue

    for tf_name, tf_days in TIMEFRAMES.items():
        for name, hist in histories.items():
            past = _get_price_at_offset(hist, tf_days)
            cur_value = (current.get(name) or {}).get("value")
            if past is None or cur_value is None:
                continue
            ret = _calculate_return(cur_value, past)
            if ret is not None:
                asset_returns[tf_name][name] = ret

    return {
        "current": current,
        "asset_returns": asset_returns,
    }
