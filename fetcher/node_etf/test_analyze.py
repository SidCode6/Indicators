"""Synthetic unit tests for node_etf/analyze.py — Today's Changes de-noising.

Run: python3 fetcher/node_etf/test_analyze.py

Guards the 2026-05-18 change: "Today's Changes" surfaces a held-position
move only when the MANAGER DECISION (flow-adjusted share %, AUM-growth
backed out) is meaningful — so pure inflow-driven proportional adds are
suppressed — and ranks survivors by that flow-adjusted magnitude. New
positions and exits are always kept.
"""
from __future__ import annotations

import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "analyze", os.path.join(os.path.dirname(os.path.abspath(__file__)), "analyze.py")
)
az = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(az)

_failures = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        _failures.append(msg)


def _h(figi, ticker, shares, weight=1.0):
    return {"figi": figi, "ticker": ticker, "name": ticker + " Inc",
            "shares": shares, "weight_pct": weight,
            "market_value_usd": shares * 10.0, "is_cash": False}


def _snap(as_of, aum, holdings):
    return {"as_of": as_of, "fetched_at": as_of + "T00:00:00Z",
            "fund": {"total_net_assets_usd": aum}, "holdings": holdings}


def test_today_events_flow_adjusted():
    # AUM grows 8% -> a +8% proportional share bump everywhere is pure flow.
    prior = _snap("2026-01-01", 100_000_000, [
        _h("F-PROP", "PROP", 1000), _h("F-TINY", "TINY", 1000),
        _h("F-REAL", "REAL", 1000), _h("F-TRIM", "TRIM", 1000),
        _h("F-GONE", "GONE", 500),
    ])
    today = _snap("2026-01-02", 108_000_000, [
        _h("F-PROP", "PROP", 1080),   # +8% raw  -> flow-adj  0%  (noise)
        _h("F-TINY", "TINY", 1085),   # +8.5% raw -> flow-adj +0.5% (noise)
        _h("F-REAL", "REAL", 1200),   # +20% raw -> flow-adj +12% (real)
        _h("F-TRIM", "TRIM", 900),    # -10% raw -> flow-adj -18% (real)
        _h("F-NEW",  "NEW",  500),    # added
        # GONE absent today -> exited
    ])
    events = az.compute_today_events([prior, today])
    by_ticker = {e["ticker"]: e for e in events}

    print("\n[de-noise] AUM-proportional moves suppressed, real decisions kept")
    check("PROP" not in by_ticker, "PROP (+8% == AUM growth, flow-adj 0%) suppressed")
    check("TINY" not in by_ticker, "TINY (flow-adj +0.5% < 1%) suppressed")
    check("REAL" in by_ticker, "REAL (flow-adj +12%) surfaced")
    check("TRIM" in by_ticker, "TRIM (flow-adj -18%) surfaced")
    check("NEW" in by_ticker and by_ticker["NEW"]["type"] == "added",
          "NEW position always surfaced (added)")
    check("GONE" in by_ticker and by_ticker["GONE"]["type"] == "exited",
          "GONE always surfaced (exited)")

    print("\n[rank] additions first, exits next, then held by flow-adj magnitude")
    order = [e["ticker"] for e in events]
    check(order[0] == "NEW", f"added first (got {order})")
    check(order[1] == "GONE", f"exited second (got {order})")
    # Among held: TRIM (|-18|) ranks above REAL (|12|)
    check(order.index("TRIM") < order.index("REAL"),
          f"held ranked by |flow-adj|: TRIM(18) before REAL(12) (got {order})")
    check(abs(by_ticker["REAL"]["flow_adjusted_shares_pct"] - 12.0) < 0.5,
          "REAL flow-adjusted ≈ +12%")
    check(abs(by_ticker["TRIM"]["flow_adjusted_shares_pct"] + 18.0) < 0.5,
          "TRIM flow-adjusted ≈ -18%")


def test_no_prior_aum_falls_back_to_raw():
    # When AUM growth can't be computed, fall back to raw share% gating.
    prior = _snap("2026-01-01", None, [_h("F-X", "XYZ", 1000)])
    today = _snap("2026-01-02", None, [_h("F-X", "XYZ", 1100)])  # +10% raw
    events = az.compute_today_events([prior, today])
    print("\n[fallback] no AUM data -> raw share% gate still works")
    check(any(e["ticker"] == "XYZ" for e in events),
          "XYZ +10% surfaced when AUM growth unavailable (raw fallback)")


def test_single_snapshot_empty():
    print("\n[edge] single snapshot -> no events")
    check(az.compute_today_events([_snap("2026-01-01", 1, [_h("F", "A", 1)])]) == [],
          "one snapshot returns []")


if __name__ == "__main__":
    test_today_events_flow_adjusted()
    test_no_prior_aum_falls_back_to_raw()
    test_single_snapshot_empty()
    print("\n" + "=" * 52)
    if _failures:
        print(f"FAILED: {len(_failures)} assertion(s)")
        for m in _failures:
            print("  - " + m)
        raise SystemExit(1)
    print("ALL ANALYZE TESTS PASSED")
