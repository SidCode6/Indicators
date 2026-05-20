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
#   source = "mof_jp:<SERIES>" -> Japan 10Y from MOF's official DAILY JGB CSV
#                                 (live, full windows). Falls back to the FRED
#                                 monthly series after the colon if MOF can't be
#                                 reached from the host.
ASSETS = [
    # Rates / Bonds (Yahoo yield tickers; values are the yield level in %)
    ("3-Month T-Bill", "^IRX", "Rates / Bonds", "rate", "yahoo"),
    ("10-Year Treasury", "^TNX", "Rates / Bonds", "rate", "yahoo"),
    ("30-Year Treasury", "^TYX", "Rates / Bonds", "rate", "yahoo"),
    ("Japan 10-Year", "JP10Y", "Rates / Bonds", "rate", "mof_jp:IRLTLT01JPM156N"),
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


def _row_from_series(name, symbol, group, kind, obs, now, freq) -> dict:
    """Pure compute for a dated value series — `obs` is a list of
    (datetime, value) sorted oldest->newest. Changes are anchored on the
    LATEST observation (`as_of`) so they reflect true period moves regardless
    of any publication lag: percentage-point deltas for a rate, percent
    returns for an asset.

      freq="daily"   -> 1D/1W are computed from the trailing trading days.
      freq="monthly" -> 1D/1W are None (no daily granularity)."""
    base = {"name": name, "symbol": symbol, "group": group, "kind": kind,
            "current": None, "changes": {}, "week52_low": None,
            "week52_high": None, "range_pos_pct": None,
            "freq": freq, "as_of": None}
    if not obs:
        return base
    as_of, current = obs[-1][0], round(obs[-1][1], 2)

    def ch(target):  # change vs the value as-of `target` (pp for rate, % else)
        past = _val_on_or_before(obs, target)
        if past is None:
            return None
        if kind == "rate":
            return round(current - past, 2)
        return None if past == 0 else round((current - past) / past * 100.0, 2)

    daily = freq == "daily"
    jan1 = datetime(as_of.year, 1, 1, tzinfo=timezone.utc)
    changes = {
        "1D":  ch(as_of - timedelta(days=1)) if daily else None,
        "1W":  ch(as_of - timedelta(days=7)) if daily else None,
        "1M":  ch(as_of - timedelta(days=31)),
        "YTD": ch(jan1),
        "1Y":  ch(as_of - timedelta(days=365)),
        "5Y":  ch(as_of - timedelta(days=365 * 5)),
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


def _row_from_fred(name, symbol, group, kind, obs, now) -> dict:
    """MONTHLY FRED rate (foreign sovereign 10Y). Thin wrapper over
    _row_from_series with freq="monthly" (1D/1W -> None)."""
    return _row_from_series(name, symbol, group, kind, obs, now, freq="monthly")


def _one_fred(name, symbol, series_id, group, kind, now) -> dict:
    return _row_from_fred(name, symbol, group, kind,
                          _fred_observations(series_id), now)


# --------------------------- Japan MOF (daily) ------------------------------
# Japan's 10Y JGB yield isn't on Yahoo, but the Ministry of Finance publishes
# the official daily yield curve as plain CSV. Two files are needed: the
# historical "_all" file (1974->prior month) plus the current-month file (the
# historical file lags ~3 weeks). Both share the same columns, so 10Y is
# always column index 10. Missing values are "-"; dates are YYYY/M/D. This
# gives Japan full daily windows (1D/1W/.../5Y) like the US treasuries.

MOF_JGB_URLS = (
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv",
    "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv",
)
MOF_JGB_10Y_COL = 10  # Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y


def _parse_mof_csv(text, into: dict) -> None:
    """Parse MOF JGB CSV text, adding {datetime: 10Y_yield} into `into`.
    Silently skips the title/header/blank/footer rows and "-"/"" values."""
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) <= MOF_JGB_10Y_COL:
            continue
        date_s, val_s = parts[0].strip(), parts[MOF_JGB_10Y_COL].strip()
        if not val_s or val_s == "-":
            continue
        try:
            dt = datetime.strptime(date_s, "%Y/%m/%d").replace(tzinfo=timezone.utc)
            into[dt] = float(val_s)
        except ValueError:
            continue


def _mof_jgb_observations():
    """Japan 10Y JGB daily yields from the MOF CSVs, merged + sorted as
    [(datetime, yield%)]. Each URL is fetched independently so a current-month
    hiccup still yields the historical series; [] only if BOTH fail (caller
    then falls back to FRED monthly)."""
    merged: dict = {}
    for url in MOF_JGB_URLS:
        try:
            resp = requests.get(url, timeout=20,
                                headers={"User-Agent": "Indicators-Dashboard/1.0"})
            resp.raise_for_status()
            _parse_mof_csv(resp.content.decode("utf-8", errors="replace"), merged)
        except Exception as e:
            print(f"[major_assets] MOF JGB {url.split('/')[-1]} error: {e}")
            continue
    return sorted(merged.items())


def _one_japan(name, symbol, fred_series, group, kind, now) -> dict:
    """Japan 10Y: prefer MOF official DAILY yields; fall back to the FRED
    MONTHLY OECD long-term rate if MOF is unreachable from the host."""
    obs = _mof_jgb_observations()
    if obs:
        return _row_from_series(name, symbol, group, kind, obs, now, freq="daily")
    print(f"[major_assets] {name}: MOF unavailable, falling back to FRED monthly")
    return _one_fred(name, symbol, fred_series, group, kind, now)


def fetch() -> dict:
    """Return the table payload: ordered groups + per-asset rows."""
    now = datetime.now(timezone.utc)
    rows = []
    for (name, symbol, group, kind, source) in ASSETS:
        if source.startswith("mof_jp:"):
            rows.append(_one_japan(name, symbol, source.split(":", 1)[1], group, kind, now))
        elif source.startswith("fred:"):
            rows.append(_one_fred(name, symbol, source.split(":", 1)[1], group, kind, now))
        else:
            rows.append(_one(name, symbol, group, kind, now))
    return {"groups": GROUP_ORDER, "assets": rows}
