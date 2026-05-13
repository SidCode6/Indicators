"""Signal computation across NODE ETF snapshots.

Builds the ``latest.json`` view the dashboard reads. Operates on the on-disk
history of snapshot files; gracefully degrades when only a single day exists.

Key design choices:
- Holdings are keyed by **FIGI** internally (stable across ticker renames).
  Ticker is the display label.
- Cash positions are excluded from "active management" signals — they're
  treated as residual.
- "Conviction" signals normalize for fund inflows/outflows using the
  total-net-assets ratio between snapshots. See ``flow_adjusted_share_pct``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


# User's primary watchlist — surface these every day even if not currently held.
WATCHLIST = [
    {"ticker": "HODL", "category": "btc_direct",
     "label": "VanEck Bitcoin ETF — direct BTC exposure"},
    {"ticker": "MSTR", "category": "btc_treasury",
     "label": "Strategy Inc — BTC treasury company"},
    {"ticker": "ASST", "category": "btc_treasury",
     "label": "Asset Entities — BTC treasury company"},
    {"ticker": "STRC", "category": "btc_treasury",
     "label": "Strategy preferred (Stretch Perpetual Preferred)"},
]

# Thresholds for surfacing single-day events. Tunable.
SIGNIFICANT_SHARE_PCT = 1.0           # Δshares > 1% (raw, before flow adjustment)
SIGNIFICANT_WEIGHT_PP = 0.10          # Δweight > 0.10 percentage points
ACCUMULATION_MIN_DAYS = 3             # need ≥3 up-days in window
ACCUMULATION_WINDOW = 7               # …out of trailing 7 trading days

# Multi-day lookback windows for the deltas table
LOOKBACK_DAYS = (1, 7, 30, 60, 100)


# ----------------------------- helpers --------------------------------------


def _holding_key(h: dict) -> str:
    """Stable identifier for a holding. Prefer FIGI; fall back to TICKER."""
    figi = (h.get("figi") or "").strip()
    if figi:
        return f"FIGI:{figi}"
    return f"TICKER:{(h.get('ticker') or '').strip().upper()}"


def _by_key(snapshot: dict) -> dict[str, dict]:
    """Map FIGI/ticker key -> holding dict, excluding cash rows."""
    out: dict[str, dict] = {}
    for h in snapshot.get("holdings", []):
        if h.get("is_cash"):
            continue
        out[_holding_key(h)] = h
    return out


def _by_ticker(snapshot: dict) -> dict[str, dict]:
    """Map upper-cased ticker -> holding (excluding cash)."""
    out: dict[str, dict] = {}
    for h in snapshot.get("holdings", []):
        if h.get("is_cash"):
            continue
        t = (h.get("ticker") or "").strip().upper()
        if t:
            out[t] = h
    return out


def _find_snapshot_for_offset(snapshots: list[dict], offset: int) -> Optional[dict]:
    """Return the snapshot ``offset`` trading days before the latest, or None.

    ``snapshots`` is sorted oldest→newest. ``offset=1`` returns the prior day.
    """
    if len(snapshots) <= offset:
        return None
    return snapshots[-1 - offset]


def _pct_change(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / old * 100.0


def _aum_growth_factor(today: dict, prior: dict) -> Optional[float]:
    """Return (today_AUM / prior_AUM) - 1, as a fraction. None if unavailable."""
    t = (today.get("fund") or {}).get("total_net_assets_usd")
    p = (prior.get("fund") or {}).get("total_net_assets_usd")
    if not t or not p or p == 0:
        return None
    return (t / p) - 1.0


def _flow_adjusted_share_pct(
    today_shares: Optional[int],
    prior_shares: Optional[int],
    aum_growth: Optional[float],
) -> Optional[float]:
    """Manager-decision share delta, % terms, after backing out AUM-growth effect.

    If AUM grew 7%, the manager would have bought ~7% more shares of each
    existing position just to maintain weights against creation-unit flows.
    The "real" decision is anything beyond that.
    """
    raw_pct = _pct_change(today_shares, prior_shares)
    if raw_pct is None:
        return None
    if aum_growth is None:
        return raw_pct
    return raw_pct - (aum_growth * 100.0)


# ----------------------------- event types ----------------------------------


def _classify_share_move(delta_shares: int, delta_pct: float) -> str:
    if delta_shares > 0:
        return "increased"
    if delta_shares < 0:
        return "decreased"
    return "unchanged"


def _human_summary(event: dict) -> str:
    """One-line English description of a single-day event."""
    t = event["type"]
    ticker = event["ticker"]
    if t == "added":
        return (f"New position: {ticker} ({event.get('name', '')}) "
                f"at {event.get('weight_pct', 0):.2f}% of NAV")
    if t == "exited":
        return (f"Exited {ticker} ({event.get('name', '')}) — "
                f"previously {event.get('prior_weight_pct', 0):.2f}% of NAV")
    if t in {"increased", "decreased"}:
        verb = "Increased" if t == "increased" else "Reduced"
        dp = event.get("delta_shares_pct") or 0
        dw = event.get("delta_weight_pp") or 0
        fa = event.get("flow_adjusted_shares_pct")
        fa_note = f" (flow-adj {fa:+.1f}%)" if fa is not None else ""
        return (f"{verb} {ticker} by {dp:+.1f}% shares{fa_note}; "
                f"weight {dw:+.2f}pp")
    return f"{ticker}: {t}"


# ----------------------------- core signals ---------------------------------


def compute_today_events(snapshots: list[dict]) -> list[dict]:
    """Adds, exits, and significant share-count moves between latest two snapshots.

    Sorted by significance descending (additions/exits first; then largest
    absolute share-count changes). Returns [] if only one snapshot exists.
    """
    if len(snapshots) < 2:
        return []

    today = snapshots[-1]
    prior = snapshots[-2]
    today_by_key = _by_key(today)
    prior_by_key = _by_key(prior)

    aum_growth = _aum_growth_factor(today, prior)

    events: list[dict] = []

    # Additions
    for key, h in today_by_key.items():
        if key not in prior_by_key:
            ev = {
                "type": "added",
                "ticker": h.get("ticker"),
                "name": h.get("name"),
                "shares": h.get("shares"),
                "weight_pct": h.get("weight_pct"),
                "market_value_usd": h.get("market_value_usd"),
            }
            ev["summary"] = _human_summary(ev)
            events.append(ev)

    # Exits
    for key, h in prior_by_key.items():
        if key not in today_by_key:
            ev = {
                "type": "exited",
                "ticker": h.get("ticker"),
                "name": h.get("name"),
                "prior_shares": h.get("shares"),
                "prior_weight_pct": h.get("weight_pct"),
            }
            ev["summary"] = _human_summary(ev)
            events.append(ev)

    # Share-count moves (held in both snapshots)
    for key, h in today_by_key.items():
        if key not in prior_by_key:
            continue
        prev = prior_by_key[key]
        ds = (h.get("shares") or 0) - (prev.get("shares") or 0)
        if ds == 0:
            continue
        dp = _pct_change(h.get("shares"), prev.get("shares"))
        dw = (h.get("weight_pct") or 0) - (prev.get("weight_pct") or 0)
        fa = _flow_adjusted_share_pct(h.get("shares"), prev.get("shares"), aum_growth)

        # Suppress trivial moves — but only if both share% and weight% are small.
        if (abs(dp or 0) < SIGNIFICANT_SHARE_PCT
                and abs(dw) < SIGNIFICANT_WEIGHT_PP):
            continue

        ev = {
            "type": _classify_share_move(ds, dp or 0),
            "ticker": h.get("ticker"),
            "name": h.get("name"),
            "shares": h.get("shares"),
            "prior_shares": prev.get("shares"),
            "delta_shares": ds,
            "delta_shares_pct": dp,
            "delta_weight_pp": dw,
            "flow_adjusted_shares_pct": fa,
            "weight_pct": h.get("weight_pct"),
        }
        ev["summary"] = _human_summary(ev)
        events.append(ev)

    # Sort: additions first, then exits, then by absolute share-delta magnitude
    type_priority = {"added": 0, "exited": 1, "increased": 2, "decreased": 2}
    events.sort(key=lambda e: (
        type_priority.get(e["type"], 9),
        -abs(e.get("delta_shares") or e.get("weight_pct") or 0),
    ))
    return events


def compute_watchlist(snapshots: list[dict]) -> list[dict]:
    """Status of each user-watchlist ticker, every day."""
    today = snapshots[-1]
    today_by_ticker = _by_ticker(today)

    # Find when each watchlist ticker was last held, even if not today.
    last_seen_by_ticker: dict[str, dict] = {}
    for snap in reversed(snapshots):
        snap_by_ticker = _by_ticker(snap)
        for entry in WATCHLIST:
            t = entry["ticker"]
            if t in last_seen_by_ticker:
                continue
            if t in snap_by_ticker:
                last_seen_by_ticker[t] = {
                    "as_of": snap["as_of"],
                    "holding": snap_by_ticker[t],
                }

    out: list[dict] = []
    for entry in WATCHLIST:
        t = entry["ticker"]
        held = t in today_by_ticker

        item: dict = {
            "ticker": t,
            "category": entry["category"],
            "label": entry["label"],
        }

        if held:
            h = today_by_ticker[t]
            item["status"] = "HELD"
            item["current"] = {
                "name": h.get("name"),
                "shares": h.get("shares"),
                "weight_pct": h.get("weight_pct"),
                "market_value_usd": h.get("market_value_usd"),
            }

            # Compare to prior snapshot (1d delta)
            prior = _find_snapshot_for_offset(snapshots, 1)
            if prior is not None:
                prior_by_ticker = _by_ticker(prior)
                pv = prior_by_ticker.get(t)
                if pv is None:
                    item["status"] = "ADDED"  # not held yesterday → added today
                    item["summary"] = f"Added today at {h.get('weight_pct', 0):.2f}% of NAV"
                else:
                    ds = (h.get("shares") or 0) - (pv.get("shares") or 0)
                    dw = (h.get("weight_pct") or 0) - (pv.get("weight_pct") or 0)
                    if ds > 0:
                        dp = _pct_change(h.get("shares"), pv.get("shares")) or 0
                        item["summary"] = (
                            f"Increased: {ds:+,} shares ({dp:+.1f}%); "
                            f"weight {dw:+.2f}pp → {h.get('weight_pct', 0):.2f}%"
                        )
                    elif ds < 0:
                        dp = _pct_change(h.get("shares"), pv.get("shares")) or 0
                        item["summary"] = (
                            f"Reduced: {ds:+,} shares ({dp:+.1f}%); "
                            f"weight {dw:+.2f}pp → {h.get('weight_pct', 0):.2f}%"
                        )
                    else:
                        item["summary"] = (
                            f"Unchanged at {h.get('weight_pct', 0):.2f}% of NAV "
                            f"({h.get('shares'):,} shares)"
                        )
            else:
                item["summary"] = (
                    f"Currently held: {h.get('weight_pct', 0):.2f}% of NAV "
                    f"({h.get('shares'):,} shares)"
                )
        else:
            seen = last_seen_by_ticker.get(t)
            # Was it held yesterday and removed today?
            prior = _find_snapshot_for_offset(snapshots, 1)
            was_held_yesterday = False
            if prior is not None:
                was_held_yesterday = t in _by_ticker(prior)

            if was_held_yesterday:
                item["status"] = "EXITED"
                pv = _by_ticker(prior)[t]
                item["summary"] = (
                    f"Exited today — previously {pv.get('weight_pct', 0):.2f}% "
                    f"of NAV ({pv.get('shares'):,} shares)"
                )
            elif seen:
                item["status"] = "NOT_HELD"
                item["summary"] = f"Not currently held (last seen {seen['as_of']})"
            else:
                item["status"] = "NOT_HELD"
                item["summary"] = "Not currently held"

        out.append(item)
    return out


def compute_multi_day_patterns(snapshots: list[dict]) -> dict:
    """Accumulation / distribution patterns in the trailing window.

    'Accumulating' = share-count increased on ≥ACCUMULATION_MIN_DAYS days
    out of the last ACCUMULATION_WINDOW snapshots.
    """
    if len(snapshots) < 2:
        return {
            "accumulating": [],
            "distributing": [],
            "window_days": ACCUMULATION_WINDOW,
            "min_days": ACCUMULATION_MIN_DAYS,
            "snapshots_in_window": len(snapshots),
            "ready": False,
        }

    window = snapshots[-(ACCUMULATION_WINDOW + 1):]
    # We need pairs (day_i, day_{i-1}) to count up/down days, so we need
    # at least 2 snapshots; the window has up to ACCUMULATION_WINDOW pairs.
    pair_count = max(0, len(window) - 1)

    # Build a per-key trail of share-count and weight across the window
    tally: dict[str, dict] = {}
    for i in range(1, len(window)):
        prev = _by_key(window[i - 1])
        curr = _by_key(window[i])
        all_keys = set(prev) | set(curr)
        for k in all_keys:
            slot = tally.setdefault(k, {
                "up_days": 0, "down_days": 0, "total_delta_shares": 0,
                "first_shares": None, "last_shares": None,
                "first_weight": None, "last_weight": None,
                "ticker": None, "name": None,
            })
            p_shares = (prev.get(k) or {}).get("shares") or 0
            c_shares = (curr.get(k) or {}).get("shares") or 0
            if c_shares > p_shares:
                slot["up_days"] += 1
            elif c_shares < p_shares:
                slot["down_days"] += 1
            slot["total_delta_shares"] += (c_shares - p_shares)
            if k in curr:
                slot["ticker"] = curr[k].get("ticker") or slot["ticker"]
                slot["name"] = curr[k].get("name") or slot["name"]
                slot["last_shares"] = c_shares
                slot["last_weight"] = curr[k].get("weight_pct")
            if slot["first_shares"] is None and k in prev:
                slot["first_shares"] = p_shares
                slot["first_weight"] = prev[k].get("weight_pct")
                slot["ticker"] = slot["ticker"] or prev[k].get("ticker")
                slot["name"] = slot["name"] or prev[k].get("name")

    accumulating, distributing = [], []
    for slot in tally.values():
        if slot["ticker"] is None:
            continue
        first_s = slot["first_shares"] or 0
        last_s = slot["last_shares"] or 0
        total_pct = _pct_change(last_s, first_s) if first_s else None
        record = {
            "ticker": slot["ticker"],
            "name": slot["name"],
            "up_days": slot["up_days"],
            "down_days": slot["down_days"],
            "pair_count": pair_count,
            "total_delta_shares": slot["total_delta_shares"],
            "total_delta_pct": total_pct,
            "current_weight_pct": slot["last_weight"],
        }
        if slot["up_days"] >= ACCUMULATION_MIN_DAYS and slot["down_days"] == 0:
            record["summary"] = (
                f"{slot['ticker']}: increased on {slot['up_days']} of last "
                f"{pair_count} days"
                + (f", +{total_pct:.1f}% shares" if total_pct is not None else "")
            )
            accumulating.append(record)
        elif slot["down_days"] >= ACCUMULATION_MIN_DAYS and slot["up_days"] == 0:
            record["summary"] = (
                f"{slot['ticker']}: decreased on {slot['down_days']} of last "
                f"{pair_count} days"
                + (f", {total_pct:.1f}% shares" if total_pct is not None else "")
            )
            distributing.append(record)

    accumulating.sort(key=lambda r: -(r.get("total_delta_pct") or 0))
    distributing.sort(key=lambda r: (r.get("total_delta_pct") or 0))

    return {
        "accumulating": accumulating,
        "distributing": distributing,
        "window_days": ACCUMULATION_WINDOW,
        "min_days": ACCUMULATION_MIN_DAYS,
        "snapshots_in_window": len(window),
        "ready": pair_count >= ACCUMULATION_MIN_DAYS,
    }


def compute_top_movers(snapshots: list[dict]) -> dict:
    """Top 5 gainers and losers by 1-day weight change. Pure ranking helper."""
    if len(snapshots) < 2:
        return {"weight_gainers": [], "weight_losers": []}

    today = snapshots[-1]
    prior = snapshots[-2]
    today_by_key = _by_key(today)
    prior_by_key = _by_key(prior)

    movers = []
    for key, h in today_by_key.items():
        pv = prior_by_key.get(key)
        if pv is None:
            continue
        dw = (h.get("weight_pct") or 0) - (pv.get("weight_pct") or 0)
        if dw == 0:
            continue
        movers.append({
            "ticker": h.get("ticker"),
            "name": h.get("name"),
            "delta_weight_pp": dw,
            "weight_pct": h.get("weight_pct"),
        })

    movers.sort(key=lambda m: -m["delta_weight_pp"])
    return {
        "weight_gainers": [m for m in movers if m["delta_weight_pp"] > 0][:5],
        "weight_losers": [m for m in movers if m["delta_weight_pp"] < 0][-5:][::-1],
    }


def compute_lookback_deltas(snapshots: list[dict]) -> dict:
    """For each holding currently in the portfolio, compute Δshares / Δweight
    vs N trading days ago for several N. Used by the dashboard for trend chips.
    """
    today = snapshots[-1]
    today_by_key = _by_key(today)

    out: dict = {}
    for key, h in today_by_key.items():
        ticker = h.get("ticker")
        if not ticker:
            continue
        record: dict = {"deltas": {}}
        for n in LOOKBACK_DAYS:
            prior = _find_snapshot_for_offset(snapshots, n)
            if prior is None:
                continue
            prior_by_key = _by_key(prior)
            pv = prior_by_key.get(key)
            aum_growth = _aum_growth_factor(today, prior)
            if pv is None:
                record["deltas"][f"{n}d"] = {
                    "status": "new_in_window",
                    "shares": h.get("shares"),
                    "weight_pct": h.get("weight_pct"),
                }
            else:
                dp = _pct_change(h.get("shares"), pv.get("shares"))
                dw = (h.get("weight_pct") or 0) - (pv.get("weight_pct") or 0)
                fa = _flow_adjusted_share_pct(
                    h.get("shares"), pv.get("shares"), aum_growth,
                )
                record["deltas"][f"{n}d"] = {
                    "delta_shares_pct": dp,
                    "delta_weight_pp": dw,
                    "flow_adjusted_shares_pct": fa,
                }
        out[ticker] = record
    return out


# ----------------------------- top-level ------------------------------------


def _enrich_fund_with_deltas(snapshots: list[dict]) -> dict:
    """Copy of today's ``fund`` block with day-over-day deltas added.

    Deltas are None when there's no prior snapshot (first day of history).
    """
    today = snapshots[-1]
    fund = dict(today.get("fund") or {})

    if len(snapshots) >= 2:
        prior = snapshots[-2]
        t_aum = (today.get("fund") or {}).get("total_net_assets_usd")
        p_aum = (prior.get("fund") or {}).get("total_net_assets_usd")
        if t_aum is not None and p_aum is not None:
            fund["total_net_assets_prior_usd"] = p_aum
            fund["total_net_assets_change_usd"] = t_aum - p_aum
            fund["total_net_assets_change_pct"] = (
                _pct_change(t_aum, p_aum)
            )
            fund["prior_snapshot_date"] = prior.get("as_of")
    return fund


def build_latest(snapshots: list[dict]) -> dict:
    """Top-level assembler: takes snapshots (oldest→newest), returns the
    object the dashboard reads as ``latest.json``.
    """
    if not snapshots:
        raise ValueError("Need at least one snapshot to build latest.json")

    today = snapshots[-1]

    return {
        "ticker": "NODE",
        "as_of": today["as_of"],
        "fetched_at": today["fetched_at"],
        "source_filename": today.get("source_filename"),
        "fund": _enrich_fund_with_deltas(snapshots),
        "watchlist": compute_watchlist(snapshots),
        "today_events": compute_today_events(snapshots),
        "multi_day_patterns": compute_multi_day_patterns(snapshots),
        "top_movers": compute_top_movers(snapshots),
        "lookback_deltas": compute_lookback_deltas(snapshots),
        "holdings": today.get("holdings", []),
        "history_summary": {
            "first_snapshot_date": snapshots[0]["as_of"],
            "latest_snapshot_date": today["as_of"],
            "num_snapshots": len(snapshots),
            "lookback_windows_days": list(LOOKBACK_DAYS),
        },
    }
