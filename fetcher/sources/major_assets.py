"""Major Asset Performance table — multi-window changes + 52-week range.

Powers the table at the top of the BTC-vs-Assets tab. Fetches every listed
asset from Yahoo (yfinance) with a 10-year history and computes, per asset:
current price/level, change over 1D / 1W / 1M / YTD / 1Y / 5Y, and the
52-week low/high.

Two kinds, with different change semantics + UI color logic:
  - "asset" (equities, crypto, commodities, indexes, FX): changes are
    PERCENT RETURNS. Up = good (green), down = red.
  - "rate"  (treasury yields, via ^IRX/^TNX/^TYX): changes are
    PERCENTAGE-POINT DELTAS of the yield level (e.g. 4.46% -> 4.62% = +0.16).
    For bonds, up = bad (red), down = good (green) — the UI inverts color.

Self-contained: it does NOT touch the macro-card / asset_returns paths, so
it can't regress existing sections. Per-asset failures degrade to nulls
(the UI shows "—") and the data.json stale-value fallback restores the last
good values. Some assets legitimately lack long history (ASST: no 5Y;
STRC/SATA issued mid/late-2025: no 1Y/5Y) — those windows are None.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import yfinance as yf


HISTORY_PERIOD = "10y"

# (display_name, yahoo_symbol, group, kind)
ASSETS = [
    # Rates / Bonds (Yahoo yield tickers; values are the yield level in %)
    ("3-Month T-Bill", "^IRX", "Rates / Bonds", "rate"),
    ("10-Year Treasury", "^TNX", "Rates / Bonds", "rate"),
    ("30-Year Treasury", "^TYX", "Rates / Bonds", "rate"),
    # Major markets
    ("S&P 500", "^GSPC", "Major Markets", "asset"),
    ("Nasdaq 100", "^NDX", "Major Markets", "asset"),
    ("Gold", "GC=F", "Major Markets", "asset"),
    ("Crude Oil", "CL=F", "Major Markets", "asset"),
    ("Dollar Index", "DX-Y.NYB", "Major Markets", "asset"),
    ("USD / INR", "INR=X", "Major Markets", "asset"),
    # Stocks / Crypto
    ("Bitcoin", "BTC-USD", "Stocks / Crypto", "asset"),
    ("MSTR", "MSTR", "Stocks / Crypto", "asset"),
    ("ASST", "ASST", "Stocks / Crypto", "asset"),
    ("STRC", "STRC", "Stocks / Crypto", "asset"),
    ("SATA", "SATA", "Stocks / Crypto", "asset"),
]

GROUP_ORDER = ["Rates / Bonds", "Major Markets", "Stocks / Crypto"]


def _price_at_offset(hist, days_ago: int):
    """Last close on or before `days_ago` days ago, or None if history
    doesn't reach that far back."""
    if hist is None or hist.empty:
        return None
    target = datetime.now(timezone.utc) - timedelta(days=days_ago)
    idx = hist.index
    try:
        if idx.tz is not None:
            mask = idx <= target
        else:
            mask = idx <= target.replace(tzinfo=None)
    except TypeError:
        naive = idx.tz_localize(None) if idx.tz else idx
        mask = naive <= target.replace(tzinfo=None)
    if mask.any():
        return float(hist["Close"][mask].iloc[-1])
    return None


def _change(kind: str, current, past):
    """pp-delta for a rate (yield level); % return for an asset. None-safe."""
    if current is None or past is None or past == 0:
        return None
    if kind == "rate":
        return round(current - past, 2)
    return round((current - past) / past * 100.0, 2)


def _ytd_days(now: datetime) -> int:
    return (now - datetime(now.year, 1, 1, tzinfo=timezone.utc)).days


def _week52(hist):
    """52-week low/high from the trailing 365 days of closes (or all
    available history if shorter, e.g. recently-issued tickers)."""
    if hist is None or hist.empty:
        return None, None
    cutoff = hist.index[-1] - timedelta(days=365)
    window = hist["Close"][hist.index >= cutoff]
    if window.empty:
        return None, None
    return round(float(window.min()), 4), round(float(window.max()), 4)


def _row_from_history(name, symbol, group, kind, hist, now) -> dict:
    """Pure compute (no network) — testable with a synthetic history."""
    base = {"name": name, "symbol": symbol, "group": group, "kind": kind,
            "current": None, "changes": {}, "week52_low": None,
            "week52_high": None, "range_pos_pct": None}
    if hist is None or hist.empty:
        return base
    current = round(float(hist["Close"].iloc[-1]), 4)
    prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else None
    changes = {"1D": _change(kind, current, prev)}
    for label, days in (("1W", 7), ("1M", 30), ("1Y", 365), ("5Y", 365 * 5)):
        changes[label] = _change(kind, current, _price_at_offset(hist, days))
    changes["YTD"] = _change(kind, current, _price_at_offset(hist, _ytd_days(now)))
    lo, hi = _week52(hist)
    # Where the current value sits within the 52-week band (0..100), for the bar.
    pos = None
    if lo is not None and hi is not None and hi > lo:
        pos = round((current - lo) / (hi - lo) * 100.0, 1)
        pos = max(0.0, min(100.0, pos))
    base.update({"current": current, "changes": changes,
                 "week52_low": lo, "week52_high": hi, "range_pos_pct": pos})
    return base


def _one(name, symbol, group, kind, now) -> dict:
    try:
        hist = yf.Ticker(symbol).history(period=HISTORY_PERIOD)
    except Exception as e:
        print(f"[major_assets] {name} ({symbol}) error: {e}")
        hist = None
    if hist is None or hist.empty:
        print(f"[major_assets] {name} ({symbol}): no data")
    return _row_from_history(name, symbol, group, kind, hist, now)


def fetch() -> dict:
    """Return the table payload: ordered groups + per-asset rows."""
    now = datetime.now(timezone.utc)
    rows = [_one(name, symbol, group, kind, now)
            for (name, symbol, group, kind) in ASSETS]
    return {"groups": GROUP_ORDER, "assets": rows}
