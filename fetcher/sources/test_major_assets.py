"""Synthetic unit tests for major_assets._row_from_history (no network).

Run: python3 fetcher/sources/test_major_assets.py

Verifies the column math the dashboard relies on:
- assets -> PERCENT returns; rates -> PERCENTAGE-POINT deltas
- YTD baseline = last close on/before Jan 1
- 52-week low/high + current position in the band
- insufficient history -> None for windows that don't reach back
"""
from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

_SPEC = importlib.util.spec_from_file_location(
    "major_assets", os.path.join(os.path.dirname(os.path.abspath(__file__)), "major_assets.py")
)
ma = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ma)

_fail = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        _fail.append(msg)


def _hist(values):
    """Daily UTC history ending today, given a list of closes (oldest->newest)."""
    idx = pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize(),
                        periods=len(values), freq="D", tz="UTC")
    return pd.DataFrame({"Close": values}, index=idx)


def test_asset_percent_returns():
    print("\n[asset] step history 100->150: every window = +50% return")
    now = datetime.now(timezone.utc)
    h = _hist([100.0] * 1999 + [150.0])  # ~5.5y so the 5Y window has data
    r = ma._row_from_history("Test", "TST", "Major Markets", "asset", h, now)
    check(r["current"] == 150.0, "current = 150")
    for w in ("1D", "1W", "1M", "YTD", "1Y", "5Y"):
        check(r["changes"][w] == 50.0, f"{w} = +50.00% (asset return)")
    check(r["week52_low"] == 100.0 and r["week52_high"] == 150.0, "52w low/high = 100/150")
    check(r["range_pos_pct"] == 100.0, "current at top of 52w band -> pos 100%")


def test_rate_pp_deltas():
    print("\n[rate] step history 4.00->4.50: every window = +0.50 pp (NOT %)")
    now = datetime.now(timezone.utc)
    h = _hist([4.00] * 1999 + [4.50])  # ~5.5y so the 5Y window has data
    r = ma._row_from_history("10Y", "^TNX", "Rates / Bonds", "rate", h, now)
    check(r["current"] == 4.5, "current = 4.5")
    for w in ("1D", "1W", "1M", "YTD", "1Y", "5Y"):
        check(r["changes"][w] == 0.5, f"{w} = +0.50 pp (yield delta, not %)")


def test_ytd_baseline():
    print("\n[YTD] baseline = last close on/before Jan 1 (prior year-end)")
    now = datetime.now(timezone.utc)
    jan1 = pd.Timestamp(year=now.year, month=1, day=1, tz="UTC")
    idx = pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=800, freq="D", tz="UTC")
    # 100 up to & including Jan 1, then 200 afterwards; current = 200 -> YTD +100%
    closes = [100.0 if d <= jan1 else 200.0 for d in idx]
    r = ma._row_from_history("Test", "TST", "Major Markets", "asset",
                             pd.DataFrame({"Close": closes}, index=idx), now)
    check(r["changes"]["YTD"] == 100.0, "YTD = +100% (200 vs Jan-1 baseline 100)")


def test_short_history_none():
    print("\n[short] 5-day history -> long windows are None")
    now = datetime.now(timezone.utc)
    r = ma._row_from_history("New", "NEW", "Stocks / Crypto", "asset",
                             _hist([100.0, 100.0, 100.0, 100.0, 150.0]), now)
    check(r["changes"]["1D"] == 50.0, "1D present (+50%)")
    for w in ("1W", "1M", "1Y", "5Y"):
        check(r["changes"][w] is None, f"{w} = None (history too short)")


def test_range_position():
    print("\n[range] current mid-band -> correct position %")
    now = datetime.now(timezone.utc)
    # closes span 80..120, current 110 -> pos = (110-80)/(120-80) = 75%
    vals = [80.0, 120.0] + [100.0] * 360 + [110.0]
    r = ma._row_from_history("Test", "TST", "Major Markets", "asset", _hist(vals), now)
    check(r["week52_low"] == 80.0 and r["week52_high"] == 120.0, "52w low/high 80/120")
    check(r["range_pos_pct"] == 75.0, "current 110 -> 75% of band")


def test_fred_monthly():
    print("\n[FRED monthly] pp-deltas anchored on latest print; 1D/1W None")
    months = []
    y, m = 2021, 1
    for _ in range(64):  # Jan 2021 .. Apr 2026
        months.append(datetime(y, m, 1, tzinfo=timezone.utc))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    obs = [(d, 2.0) for d in months]
    obs[-1] = (months[-1], 2.5)  # latest month jumps to 2.5
    r = ma._row_from_fred("Japan 10-Year", "JP10Y", "Rates / Bonds", "rate",
                          obs, datetime.now(timezone.utc))
    check(r["freq"] == "monthly", "freq = monthly")
    check(r["current"] == 2.5, "current = latest print (2.5)")
    check(r["as_of"] == months[-1].strftime("%Y-%m-%d"), "as_of = latest obs date")
    check(r["changes"]["1D"] is None and r["changes"]["1W"] is None,
          "1D/1W = None (no daily data)")
    for w in ("1M", "YTD", "1Y", "5Y"):
        check(r["changes"][w] == 0.5, f"{w} = +0.50 pp")
    check(r["week52_low"] == 2.0 and r["week52_high"] == 2.5, "52w range 2.0-2.5")
    check(r["range_pos_pct"] == 100.0, "current at top of 52w band")
    empty = ma._row_from_fred("X", "X", "g", "rate", [], datetime.now(timezone.utc))
    check(empty["current"] is None and empty["freq"] == "monthly", "empty obs -> nulls")


def test_empty_history():
    print("\n[edge] empty history -> all None, no crash")
    now = datetime.now(timezone.utc)
    r = ma._row_from_history("X", "X", "Major Markets", "asset",
                             pd.DataFrame({"Close": []}), now)
    check(r["current"] is None and r["changes"] == {} and r["range_pos_pct"] is None,
          "empty -> nulls")


def _daily_obs(overrides=None, n=2000, base=2.00,
               as_of=datetime(2026, 5, 19, tzinfo=timezone.utc)):
    """Daily (datetime, value) obs ending at as_of; value=base except for the
    day-offsets in `overrides` ({days_back_from_as_of: value})."""
    ov = overrides or {}
    return [(as_of - timedelta(days=k), ov.get(k, base)) for k in range(n - 1, -1, -1)]


def test_series_daily():
    print("\n[series daily] MOF-style daily rate: 1D/1W POPULATED + pp-deltas")
    now = datetime.now(timezone.utc)
    # as_of=2.50, prior trading day=2.40, everything earlier=2.00
    obs = _daily_obs({0: 2.50, 1: 2.40})
    r = ma._row_from_series("Japan 10-Year", "JP10Y", "Rates / Bonds", "rate",
                            obs, now, freq="daily")
    check(r["freq"] == "daily", "freq = daily")
    check(r["current"] == 2.5, "current = 2.5 (latest obs)")
    check(r["as_of"] == "2026-05-19", "as_of = latest obs date")
    check(r["changes"]["1D"] == 0.1, "1D = +0.10 pp (2.50 vs prior 2.40)")
    check(r["changes"]["1W"] == 0.5, "1W = +0.50 pp (2.50 vs 2.00)")
    for w in ("1M", "YTD", "1Y", "5Y"):
        check(r["changes"][w] == 0.5, f"{w} = +0.50 pp")
    check(r["week52_low"] == 2.0 and r["week52_high"] == 2.5, "52w range 2.0-2.5")
    check(r["range_pos_pct"] == 100.0, "current at top of 52w band")


def test_mof_parse():
    print("\n[MOF parse] skips title/header/blank/footer/'-'; keeps 10Y col")
    csv = "\n".join([
        "Interest Rate,,,,,,,,,,,,,,,(Unit : %)",
        "Date,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y",
        "2026/5/18,1.128,1.431,1.628,1.849,2.015,2.163,2.308,2.454,2.594,2.729,3.306,3.665,3.979,4,4.006",
        "2026/5/19,1.135,1.452,1.654,1.878,2.051,2.208,2.359,2.507,2.646,2.783,3.36,3.717,4.028,4.043,4.043",
        "1974/9/24,10.327,9.362,8.83,8.515,8.348,8.29,8.24,8.121,8.127,-,-,-,-,-,-",
        ",,,,,,,,,,,,,,,",
        '"  garbage footer note about clearing cache",,,,,,,,,,,,,,,',
    ])
    into = {}
    ma._parse_mof_csv(csv, into)
    check(len(into) == 2, "only the 2 valid 10Y rows parse (1974 has '-')")
    k19 = datetime(2026, 5, 19, tzinfo=timezone.utc)
    k18 = datetime(2026, 5, 18, tzinfo=timezone.utc)
    check(into.get(k19) == 2.783, "2026-05-19 -> 2.783 (col index 10)")
    check(into.get(k18) == 2.729, "2026-05-18 -> 2.729")


def test_oecd_parse():
    print("\n[OECD parse] header-indexed TIME_PERIOD/OBS_VALUE; sorted; skips blanks")
    csv_text = "\n".join([
        "STRUCTURE,REF_AREA,Reference area,FREQ,MEASURE,TIME_PERIOD,OBS_VALUE,OBS_STATUS",
        "DATAFLOW,IND,India,M,IRLT,2026-01,6.732,A",
        "DATAFLOW,IND,India,M,IRLT,2026-03,6.84,A",   # out of order on purpose
        "DATAFLOW,IND,India,M,IRLT,2026-02,6.77,A",
        "DATAFLOW,IND,India,M,IRLT,2025-12,,A",        # blank value -> skip
    ])
    obs = ma._parse_oecd_csv(csv_text)
    check(len(obs) == 3, "3 valid rows parse (blank-value row skipped)")
    check(obs == sorted(obs), "obs sorted oldest->newest")
    check(obs[-1][1] == 6.84 and obs[-1][0].strftime("%Y-%m") == "2026-03",
          "latest = 6.84 @ 2026-03")
    r = ma._row_from_series("India 10-Year", "IN10Y", "Rates / Bonds", "rate",
                            obs, datetime.now(timezone.utc), freq="monthly")
    check(r["current"] == 6.84 and r["freq"] == "monthly", "row current=6.84, monthly")
    check(r["changes"]["1D"] is None and r["changes"]["1W"] is None,
          "1D/1W None (monthly)")


if __name__ == "__main__":
    for fn in (test_asset_percent_returns, test_rate_pp_deltas, test_ytd_baseline,
               test_short_history_none, test_range_position, test_fred_monthly,
               test_empty_history, test_series_daily, test_mof_parse, test_oecd_parse):
        fn()
    print("\n" + "=" * 50)
    if _fail:
        print(f"FAILED: {len(_fail)} assertion(s)")
        for m in _fail:
            print("  - " + m)
        raise SystemExit(1)
    print("ALL MAJOR_ASSETS TESTS PASSED")
