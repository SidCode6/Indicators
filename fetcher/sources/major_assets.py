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

import requests
import yfinance as yf


HISTORY_PERIOD = "10y"

# (display_name, display_symbol, group, kind, source)
#   source = "yahoo"           -> daily Yahoo history (display_symbol IS the ticker)
#   source = "fred:<SERIES>"   -> FRED monthly series (display_symbol is a label;
#                                 the FRED id follows the colon). Foreign sovereign
#                                 10Y yields aren't on Yahoo, and FRED's OECD long-
#                                 term rates are MONTHLY + ~1-2 months lagged, so
#                                 those rows have no 1D/1W and are tagged "monthly".
ASSETS = [
    # Rates / Bonds (Yahoo yield tickers; values are the yield level in %)
    ("3-Month T-Bill", "^IRX", "Rates / Bonds", "rate", "yahoo"),
    ("10-Year Treasury", "^TNX", "Rates / Bonds", "rate", "yahoo"),
    ("30-Year Treasury", "^TYX", "Rates / Bonds", "rate", "yahoo"),
    ("Japan 10-Year", "JP10Y", "Rates / Bonds", "rate", "fred:IRLTLT01JPM156N"),
    ("India 10-Year", "IN10Y", "Rates / Bonds", "rate", "fred:INDIRLTLT01STM"),
    # Major markets
    ("S&P 500", "^GSPC", "Major Markets", "asset", "yahoo"),
    ("Nasdaq 100", "^NDX", "Major Markets", "asset", "yahoo"),
    ("Gold", "GC=F", "Major Markets", "asset", "yahoo"),
    ("Crude Oil", "CL=F", "Major Markets", "asset", "yahoo"),
    ("Dollar Index", "DX-Y.NYB", "Major Markets", "asset", "yahoo"),
    ("USD / INR", "INR=X", "Major Markets", "asset", "yahoo"),
    # Stocks / Crypto
    ("Bitcoin", "BTC-USD", "Stocks / Crypto", "asset", "yahoo"),
    ("MSTR", "MSTR", "Stocks / Crypto", "asset", "yahoo"),
    ("ASST", "ASST", "Stocks / Crypto", "asset", "yahoo"),
    ("STRC", "STRC", "Stocks / Crypto", "asset", "yahoo"),
    ("SATA", "SATA", "Stocks / Crypto", "asset", "yahoo"),
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
            "week52_high": None, "range_pos_pct": None,
            "freq": "daily", "as_of": None}
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


# ----------------------------- FRED (monthly) -------------------------------
# Foreign sovereign 10Y yields (Japan, India) aren't on Yahoo. The only free
# source is FRED's OECD long-term rates, which are MONTHLY and ~1-2 months
# lagged. FRED's CSV endpoint works from Railway (the live CPI/debt come from
# it). These rows have no 1D/1W and are tagged freq="monthly".

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"


def _fred_observations(series_id: str):
    """Fetch a FRED series as a sorted list of (datetime, value). [] on error."""
    try:
        resp = requests.get(FRED_CSV_URL.format(series_id), timeout=15,
                            headers={"User-Agent": "Indicators-Dashboard/1.0"})
        resp.raise_for_status()
        out = []
        for line in resp.text.strip().split("\n")[1:]:
            parts = line.split(",")
            if len(parts) != 2:
                continue
            date_s, val_s = parts[0].strip(), parts[1].strip()
            if not val_s or val_s == ".":
                continue
            try:
                dt = datetime.strptime(date_s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                out.append((dt, float(val_s)))
            except ValueError:
                continue
        out.sort(key=lambda x: x[0])
        return out
    except Exception as e:
        print(f"[major_assets] FRED {series_id} error: {e}")
        return []


def _val_on_or_before(obs, target):
    """Last value whose date <= target (obs sorted oldest->newest), else None."""
    v = None
    for dt, val in obs:
        if dt <= target:
            v = val
        else:
            break
    return v


def _row_from_fred(name, symbol, group, kind, obs, now) -> dict:
    """Pure compute for a MONTHLY FRED rate. Changes are percentage-point
    deltas anchored on the LATEST observation (true month-over-month / YTD /
    YoY moves regardless of publication lag). 1D/1W are None."""
    base = {"name": name, "symbol": symbol, "group": group, "kind": kind,
            "current": None, "changes": {}, "week52_low": None,
            "week52_high": None, "range_pos_pct": None,
            "freq": "monthly", "as_of": None}
    if not obs:
        return base
    as_of, current = obs[-1][0], round(obs[-1][1], 2)

    def d(v):  # pp-delta of the yield level vs current
        return None if v is None else round(current - v, 2)

    jan1 = datetime(as_of.year, 1, 1, tzinfo=timezone.utc)
    changes = {
        "1D": None, "1W": None,
        "1M":  d(_val_on_or_before(obs, as_of - timedelta(days=31))),
        "YTD": d(_val_on_or_before(obs, jan1)),
        "1Y":  d(_val_on_or_before(obs, as_of - timedelta(days=365))),
        "5Y":  d(_val_on_or_before(obs, as_of - timedelta(days=365 * 5))),
    }
    recent = [v for (dt, v) in obs if dt >= as_of - timedelta(days=365)]
    lo = round(min(recent), 2) if recent else None
    hi = round(max(recent), 2) if recent else None
    pos = None
    if lo is not None and hi is not None and hi > lo:
        pos = max(0.0, min(100.0, round((current - lo) / (hi - lo) * 100.0, 1)))
    base.update({"current": current, "changes": changes, "week52_low": lo,
                 "week52_high": hi, "range_pos_pct": pos,
                 "as_of": as_of.strftime("%Y-%m-%d")})
    return base


def _one_fred(name, symbol, series_id, group, kind, now) -> dict:
    return _row_from_fred(name, symbol, group, kind,
                          _fred_observations(series_id), now)


def fetch() -> dict:
    """Return the table payload: ordered groups + per-asset rows."""
    now = datetime.now(timezone.utc)
    rows = []
    for (name, symbol, group, kind, source) in ASSETS:
        if source.startswith("fred:"):
            rows.append(_one_fred(name, symbol, source.split(":", 1)[1], group, kind, now))
        else:
            rows.append(_one(name, symbol, group, kind, now))
    return {"groups": GROUP_ORDER, "assets": rows}
