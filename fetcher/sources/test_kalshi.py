"""Synthetic-data unit tests for kalshi.py (no network).

Run: python3 fetcher/sources/test_kalshi.py
Guards the invariants behind the Kalshi audit fixes (P1-P5):
- every curated series passes the GAME/MATCH/FIGHT/RACE suffix rule
- every curated series resolves to a non-empty sport label
- every cricket series is priority (Cricket/IPL) AND game-outcome
- P2 add/remove of dead series
- P3 Soccer/Basketball labels
- no SPORT_LABEL_RULES shadowing
- _slugify_series_title is the canonical rule (apostrophe NOT stripped)
- _is_total_fetch_failure + write() stale-value fallback
- _evaluate_event isolates a malformed event instead of raising
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

_SPEC = importlib.util.spec_from_file_location(
    "kalshi", os.path.join(os.path.dirname(os.path.abspath(__file__)), "kalshi.py")
)
k = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(k)

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
        _failures.append(msg)


CRICKET_SERIES = [
    "KXIPLGAME", "KXCRICKETT20IMATCH", "KXPSLGAME", "KXCOUNTYCHAMPMATCH",
    "KXT20MATCH", "KXWT20MATCH", "KXCRICKETWOMENT20IMATCH",
]


def test_no_dead_entries():
    print("\n[P1/P2] no dead entries — every curated series passes the suffix rule")
    for s in k.ACTIVE_LIVE_SPORTS_SERIES:
        check(k._is_game_outcome_series(s), f"{s} ends in GAME/MATCH/FIGHT/RACE")


def test_no_empty_labels():
    print("\n[P3] every curated series resolves to a non-empty sport label")
    for s in k.ACTIVE_LIVE_SPORTS_SERIES:
        lbl = k._sport_label_for_series(s)
        check(lbl != "", f"{s} -> label {lbl!r}")


def test_label_has_duration():
    print("\n[P3/P4] every resolved label has a SPORT_DURATION_MINUTES entry")
    labels = {k._sport_label_for_series(s) for s in k.ACTIVE_LIVE_SPORTS_SERIES}
    for lbl in sorted(labels):
        check(lbl in k.SPORT_DURATION_MINUTES,
              f"{lbl!r} in SPORT_DURATION_MINUTES (else default {k.DEFAULT_DURATION_MINUTES}m)")


def _live_event(series, fav_dollars, other_dollars="0.0100"):
    """Synthetic Sports event that is 'live now' (occurrence = now)."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "category": "Sports", "series_ticker": series,
        "event_ticker": f"{series}-TEST", "title": "A vs B",
        "markets": [
            {"occurrence_datetime": now_iso, "yes_sub_title": "A",
             "yes_bid_dollars": fav_dollars, "ticker": f"{series}-A"},
            {"occurrence_datetime": now_iso, "yes_sub_title": "B",
             "yes_bid_dollars": other_dollars, "ticker": f"{series}-B"},
        ],
    }


def test_cricket_priority():
    print("\n[rule] cricket series still LABEL Cricket/IPL + pass suffix (structural)")
    for s in CRICKET_SERIES:
        check(s in k.ACTIVE_LIVE_SPORTS_SERIES, f"{s} present in curated list")
        lbl = k._sport_label_for_series(s)
        check(lbl in ("Cricket", "IPL"), f"{s} -> label {lbl!r}")
        check(k._is_game_outcome_series(s), f"{s} passes suffix rule")
    check("KXBBLCRICKET" not in k.ACTIVE_LIVE_SPORTS_SERIES,
          "KXBBLCRICKET removed (no such Kalshi series)")

    print("\n[rule 2026-05-16] ONLY IPL is gate-exempt; non-IPL cricket obeys 83-98")
    # Non-IPL cricket at 99% -> filtered out (was: always shown)
    out = k._evaluate_event(_live_event("KXT20MATCH", "0.9900"), {})
    check(out is None, "non-IPL cricket @99% is DROPPED (outside 83-98)")
    # Non-IPL cricket at 90% -> shown, but NOT priority
    out = k._evaluate_event(_live_event("KXT20MATCH", "0.9000"), {})
    check(out is not None and out["is_priority"] is False,
          "non-IPL cricket @90% shown, is_priority=False")
    # IPL at 50% -> shown (gate-exempt) AND priority
    out = k._evaluate_event(_live_event("KXIPLGAME", "0.5000", "0.5000"), {})
    check(out is not None and out["is_priority"] is True,
          "IPL @50% shown despite odds, is_priority=True")
    # IPL at 99% -> still shown (exempt)
    out = k._evaluate_event(_live_event("KXIPLGAME", "0.9900"), {})
    check(out is not None and out["is_priority"] is True,
          "IPL @99% still shown (gate does not apply to IPL)")


def test_p2_dead_series():
    print("\n[P2] dead series replaced/removed; correct tickers present")
    for good in ("KXBOXINGFIGHT", "KXBELGIANPLGAME"):
        check(good in k.ACTIVE_LIVE_SPORTS_SERIES, f"{good} present")
    for bad in ("KXBOXING", "KXBELGIANPL", "KXNTLFRIENDLY",
                "KXATPSETWINNER", "KXSUMOWIN", "KXLPGATOUR"):
        check(bad not in k.ACTIVE_LIVE_SPORTS_SERIES, f"{bad} removed")


def test_p3_labels():
    print("\n[P3] Soccer/Basketball label rules + EuroCup confirmed Basketball")
    for s in ("KXEFLL1GAME", "KXDFBPOKALGAME", "KXAFCACGAME", "KXINTLFRIENDLYGAME"):
        check(k._sport_label_for_series(s) == "Soccer", f"{s} -> Soccer")
    for s in ("KXNBLGAME", "KXFIBAGAME", "KXARGLNBGAME", "KXVTBGAME"):
        check(k._sport_label_for_series(s) == "Basketball", f"{s} -> Basketball")
    check(k._sport_label_for_series("KXEUROCUPGAME") == "Basketball",
          "KXEUROCUPGAME -> Basketball (verified: 'EuroCup Basketball Game')")


def test_no_rule_shadowing():
    print("\n[P3] no SPORT_LABEL_RULES ordering shadow (generic before specific)")
    rules = k.SPORT_LABEL_RULES
    ok = True
    for i, (p, l) in enumerate(rules):
        for j in range(i + 1, len(rules)):
            p2, l2 = rules[j]
            if p2.startswith(p) and l != l2:
                check(False, f"SHADOW {p!r}->{l!r} hides {p2!r}->{l2!r}")
                ok = False
    check(ok, "no shadowing pair found")


def test_slugify_canonical():
    print("\n[apostrophe] _slugify_series_title is canonical (NOT stripping ')")
    check(k._slugify_series_title("ATP Tennis Match") == "atp-tennis-match",
          "spaces -> dashes, lowercased")
    check(k._slugify_series_title("Challenger ATP ") == "challenger-atp-",
          "trailing space -> trailing dash preserved (spec §8)")
    # The apostrophe is intentionally NOT stripped — Kalshi's real slug
    # behavior is unverified (open question; can't crawl kalshi.com).
    check(k._slugify_series_title("Men's T20 Cricket Match") == "men's-t20-cricket-match",
          "apostrophe LEFT IN (canonical behavior, open question logged)")


def test_total_fetch_failure_logic():
    print("\n[P4] _is_total_fetch_failure distinguishes 'errored' vs 'no live'")
    check(k._is_total_fetch_failure(
        {"events_seen": 0, "errors": {"count": 69, "sample": "x"}}) is True,
        "events_seen=0 + errors -> total failure (keep previous)")
    check(k._is_total_fetch_failure(
        {"events_seen": 540, "errors": None}) is False,
        "events_seen>0, no errors -> NOT failure (legit 'none live')")
    check(k._is_total_fetch_failure(
        {"events_seen": 0, "errors": None}) is False,
        "events_seen=0, no errors -> NOT failure (don't clobber on a no-op)")


def test_write_stale_fallback():
    print("\n[P4] write() preserves previous events on a total-fetch-failure")
    orig = k.OUTPUT_PATH
    tmpd = tempfile.mkdtemp()
    k.OUTPUT_PATH = os.path.join(tmpd, "kalshi.json")
    try:
        good = {"fetched_at": "T1", "events_seen": 400, "live_sports_count": 1,
                "errors": None, "events": [{"event_ticker": "E1"}]}
        k.write(good)
        fail = {"fetched_at": "T2", "events_seen": 0, "live_sports_count": 0,
                "errors": {"count": 69, "sample": "429"}, "events": []}
        k.write(fail)
        with open(k.OUTPUT_PATH) as f:
            out = json.load(f)
        check(out.get("events") == [{"event_ticker": "E1"}],
              "previous events kept despite a failed cycle")
        check(out.get("stale") is True, "stale flag set")
        check(out.get("last_attempt_at") == "T2", "last_attempt_at records the failed cycle")
        # And a legitimate 'no live games' cycle DOES overwrite (clears it)
        nolive = {"fetched_at": "T3", "events_seen": 500, "live_sports_count": 0,
                  "errors": None, "events": []}
        k.write(nolive)
        with open(k.OUTPUT_PATH) as f:
            out2 = json.load(f)
        check(out2.get("events") == [] and "stale" not in out2,
              "legit no-live cycle overwrites and clears stale")
    finally:
        k.OUTPUT_PATH = orig


def test_evaluate_event_isolation():
    print("\n[P4] _evaluate_event tolerates a malformed event (no raise)")
    raised = False
    try:
        r = k._evaluate_event({"category": "Sports", "series_ticker": "KXMLBGAME",
                               "markets": [{"occurrence_datetime": "not-a-date",
                                            "yes_bid_dollars": "oops"}]}, {})
    except Exception as e:  # noqa
        raised = True
        r = None
    check(not raised, "malformed event did not raise out of _evaluate_event")
    check(r is None, "malformed event -> None (skipped)")
    # Non-Sports / non-game-outcome short-circuit to None
    check(k._evaluate_event({"category": "Politics"}, {}) is None, "non-Sports -> None")


def test_seed_titles_consistent():
    print("\n[P4] SEED_SERIES_TITLES only references current curated series")
    cur = set(k.ACTIVE_LIVE_SPORTS_SERIES)
    stale = [t for t in k.SEED_SERIES_TITLES if t not in cur]
    check(not stale, f"no seed entry for a removed series (stale={stale})")
    missing = [s for s in k.ACTIVE_LIVE_SPORTS_SERIES if s not in k._load_series_title_cache()]
    check(len(missing) <= 1,
          f"<=1 curated series missing a warm title (runtime fills it): {missing}")


def _rec(label, fav, ends, priority=None):
    if priority is None:
        priority = (label == "IPL")  # revised rule: only IPL is priority
    return {"sport_label": label, "favorite_pct": fav,
            "ends_in_minutes": ends, "is_priority": priority}


def _act_event(series, occ_dt, fav="0.9500", other="0.0400", oi="0"):
    iso = occ_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if occ_dt else None
    def mk(sub, bid):
        m = {"yes_sub_title": sub, "yes_bid_dollars": bid,
             "ticker": series + "-" + sub, "open_interest_fp": oi,
             "volume_fp": oi, "volume_24h_fp": oi}
        if iso:
            m["occurrence_datetime"] = iso
        return m
    return {"category": "Sports", "series_ticker": series,
            "event_ticker": series + "-T", "title": "A vs B",
            "markets": [mk("A", fav), mk("B", other)]}


def test_activity_liveness():
    print("\n[activity liveness] occurrence_datetime drift no longer hides live games")
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    far = now + timedelta(minutes=276)   # like the real ITF: occ 4.6h FUTURE
    # The exact failing case: live ITF, occ +4.6h, in-range, HEAVY activity
    out = k._evaluate_event(_act_event("KXITFWMATCH", far, "0.9500", "0.0400", oi="264787"), {})
    check(out is not None, "ITF occ +276m but OI 264k & 95% -> SHOWN (was hidden)")
    # Same timing but NO trading activity -> stays hidden (not live)
    out = k._evaluate_event(_act_event("KXITFWMATCH", far, "0.9500", "0.0400", oi="0"), {})
    check(out is None, "ITF occ +276m, zero activity -> DROPPED (correctly not live)")
    # Noise activity (below threshold) -> not live
    out = k._evaluate_event(_act_event("KXITFWMATCH", far, "0.9500", "0.0400", oi="571"), {})
    check(out is None, "activity 571 (< 5000 threshold) -> DROPPED (pre-market noise)")
    # Old time-window path still works WITHOUT activity (additive, no regression)
    out = k._evaluate_event(_act_event("KXATPMATCH", now, "0.9000", "0.0800", oi="0"), {})
    check(out is not None, "in-window match, zero activity -> still SHOWN (time path intact)")
    # Activity path still respects the 83-98 gate for non-IPL
    out = k._evaluate_event(_act_event("KXITFWMATCH", far, "0.5400", "0.4400", oi="42048"), {})
    check(out is None, "live (high activity) but fav 54% -> DROPPED (below gate)")
    # IPL: heavy activity + any odds -> shown & priority
    out = k._evaluate_event(_act_event("KXIPLGAME", far, "0.4000", "0.6000", oi="90000"), {})
    check(out is not None and out["is_priority"], "IPL high-activity @40% -> SHOWN priority")
    # Sanity bound: heavy activity but occ ~20h away -> NOT live (far future)
    out = k._evaluate_event(_act_event("KXITFWMATCH", now + timedelta(hours=20),
                                       "0.9500", "0.0400", oi="264787"), {})
    check(out is None, "heavy activity but occ +20h (> ±12h sanity) -> DROPPED")
    # No occurrence_datetime at all + heavy activity -> live
    out = k._evaluate_event(_act_event("KXITFWMATCH", None, "0.9500", "0.0400", oi="264787"), {})
    check(out is not None, "no occ but heavy activity -> SHOWN")
    # helper parses *_fp strings; missing -> 0
    check(k._event_market_activity([{"open_interest_fp": "1234.5"}]) == 1234.5,
          "_event_market_activity parses open_interest_fp")
    check(k._event_market_activity([{}]) == 0.0, "missing activity fields -> 0")


def test_maximal_coverage():
    print("\n[maximal coverage 2026-05-16] missing series added + mislabels fixed")
    check(len(k.ACTIVE_LIVE_SPORTS_SERIES) >= 160,
          f"curated list expanded (~164), got {len(k.ACTIVE_LIVE_SPORTS_SERIES)}")
    # the exact series from the user's screenshot, now present + labelled
    for s, lbl in [("KXFACUPGAME", "Soccer"), ("KXLALIGA2GAME", "Soccer"),
                   ("KXWTAMATCH", "Tennis"), ("KXWTACHALLENGERMATCH", "Tennis"),
                   ("KXCHALLENGERMATCH", "Tennis"), ("KXELITESERIENGAME", "Soccer")]:
        check(s in k.ACTIVE_LIVE_SPORTS_SERIES, f"{s} now in curated list")
        check(k._sport_label_for_series(s) == lbl, f"{s} -> {lbl}")
    # dormant Hockey mislabels corrected to Soccer (titles prove soccer)
    for s in ("KXHNLGAME", "KXSWISSLEAGUEGAME", "KXKLEAGUEGAME",
              "KXCZEFLGAME", "KXECULPGAME", "KXCHLLDPGAME"):
        check(k._sport_label_for_series(s) == "Soccer",
              f"{s} -> Soccer (was mislabeled Hockey)")
    # WPL is Women's IPL cricket, NOT soccer (generator bug we caught)
    check(k._sport_label_for_series("KXWPLGAME") == "Cricket",
          "KXWPLGAME -> Cricket (Women's IPL, not Soccer)")
    # 5-day Test cricket deliberately NOT added (doesn't fit live model)
    for s in ("KXTESTMATCH", "KXWTESTMATCH", "KXCRICKETTESTMATCH"):
        check(s not in k.ACTIVE_LIVE_SPORTS_SERIES, f"{s} excluded (multi-day Test)")
    # the occurrence-drift fix: default buffer widened 60 -> 150
    check(k.DEFAULT_PRE_GAME_BUFFER == 150, "DEFAULT_PRE_GAME_BUFFER == 150")
    check(k.PRE_GAME_BUFFER_MINUTES.get("IPL") == 300, "IPL buffer still 300")
    check("Baseball" in k.SPORT_DURATION_MINUTES
          and "Football" in k.SPORT_DURATION_MINUTES,
          "new duration keys Baseball/Football present")


def _event_at(series, occ_dt, fav="0.7600", other="0.2400"):
    iso = occ_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "category": "Sports", "series_ticker": series,
        "event_ticker": f"{series}-T", "title": "A vs B",
        "markets": [
            {"occurrence_datetime": iso, "yes_sub_title": "A",
             "yes_bid_dollars": fav, "ticker": f"{series}-A"},
            {"occurrence_datetime": iso, "yes_sub_title": "B",
             "yes_bid_dollars": other, "ticker": f"{series}-B"},
        ],
    }


def test_ipl_never_hidden():
    print("\n[IPL never hidden] the real 2026-05-16 bug + window edge cases")
    now = datetime.now(timezone.utc)

    # THE EXACT BUG: live IPL whose occurrence_datetime is ~1.5h in the
    # FUTURE (KXIPLGAME-26MAY16GTKKR: occ 17:00Z, live at 15:30Z).
    out = k._evaluate_event(_event_at("KXIPLGAME", now + timedelta(minutes=90)), {})
    check(out is not None, "live IPL with occ +90m in FUTURE is SHOWN (was hidden)")
    check(out and out["is_priority"] is True, "that IPL is priority (tier 0)")

    # IPL shown regardless of odds (1% / 50% / 99%)
    for fav in ("0.0100", "0.5000", "0.9900"):
        o = k._evaluate_event(_event_at("KXIPLGAME", now + timedelta(minutes=90),
                                        fav=fav, other="0.0100"), {})
        check(o is not None and o["is_priority"], f"IPL @ {fav} shown (gate bypassed)")

    # _is_live_now(IPL) window boundaries: buffer 300m, duration 360m
    liv = lambda mins: k._is_live_now(now + timedelta(minutes=mins), "IPL")
    check(liv(90) is True,   "occ now+90m  -> live (the bug case)")
    check(liv(299) is True,  "occ now+299m -> live (just inside 300m buffer)")
    check(liv(330) is False, "occ now+330m -> NOT live (genuinely >5h away)")
    check(liv(-350) is True, "occ now-350m -> live (within 360m duration)")
    check(liv(-370) is False,"occ now-370m -> NOT live (match over)")

    # Non-IPL cricket with the SAME future-occ does NOT get the wide
    # window (Cricket buffer 60m) and is gated 83-98 anyway.
    c = k._evaluate_event(_event_at("KXT20MATCH", now + timedelta(minutes=90)), {})
    check(c is None, "non-IPL cricket occ+90m -> hidden (60m buffer, not IPL)")

    # IPL outranks a 99% non-IPL cricket AND a 98% tennis (tier 0)
    ipl = k._evaluate_event(_event_at("KXIPLGAME", now + timedelta(minutes=90),
                                      fav="0.4000", other="0.6000"), {})
    recs = [ipl,
            _rec("Cricket", 99, 30), _rec("Tennis", 98, 10)]
    order = sorted([r for r in recs if r], key=k._event_sort_key)
    check(order[0].get("sport_label") == "IPL",
          "IPL @40% still sorts above Cricket@99 & Tennis@98")

    # Determinism / refresh-idempotence: pure function of (occ, now),
    # no module state — repeated calls give the same answer.
    occ = now + timedelta(minutes=90)
    check(k._is_live_now(occ, "IPL") == k._is_live_now(occ, "IPL"),
          "_is_live_now is deterministic (refresh cannot flip/reset it)")
    e = _event_at("KXIPLGAME", occ)
    check(k._evaluate_event(e, {}) == k._evaluate_event(e, {}),
          "_evaluate_event is idempotent (stateless — refresh-safe)")


def test_ipl_pinned_top():
    print("\n[IPL pin] IPL always first; everything else is pure favorite order")
    ipl_low = _rec("IPL", 55, 120)
    cricket_high = _rec("Cricket", 99, 30)
    tennis_high = _rec("Tennis", 98, 10)
    mlb = _rec("MLB", 90, 200)
    order = sorted([tennis_high, cricket_high, mlb, ipl_low], key=k._event_sort_key)
    labels = [r["sport_label"] for r in order]
    check(labels[0] == "IPL", f"IPL first even @55% vs Cricket @99% (got {labels})")
    # Non-IPL is NOT special anymore — strictly favorite % desc
    check(labels[1:] == ["Cricket", "Tennis", "MLB"],
          f"rest ordered purely by favorite% 99/98/90 (got {labels})")
    # Cricket has no privilege: a higher-fav Tennis beats a lower-fav Cricket
    t99, c95 = _rec("Tennis", 99, 10), _rec("Cricket", 95, 10)
    check(sorted([c95, t99], key=k._event_sort_key) == [t99, c95],
          "Tennis @99 beats Cricket @95 (cricket no longer privileged)")

    # Two IPL games: higher favorite first, then ending soonest
    a = _rec("IPL", 70, 90)
    b = _rec("IPL", 88, 90)
    c = _rec("IPL", 88, 40)
    ipl_order = sorted([a, b, c], key=k._event_sort_key)
    check(ipl_order == [c, b, a],
          "within IPL: higher fav first, then ends-soonest")

    # IPL can never be truncated by the 15-item cap (it's tier 0)
    many = [_rec("Tennis", 90 + (i % 8), i, priority=False) for i in range(20)]
    many.append(_rec("IPL", 60, 300))
    top = sorted(many, key=k._event_sort_key)[:k.MAX_OUTPUT_ITEMS]
    check(any(r["sport_label"] == "IPL" for r in top),
          "IPL survives the MAX_OUTPUT_ITEMS cap (pinned at index 0)")
    check(top[0]["sport_label"] == "IPL", "IPL is literally the first pill")


if __name__ == "__main__":
    for fn in [
        test_no_dead_entries, test_no_empty_labels, test_label_has_duration,
        test_cricket_priority, test_p2_dead_series, test_p3_labels,
        test_no_rule_shadowing, test_slugify_canonical,
        test_total_fetch_failure_logic, test_write_stale_fallback,
        test_evaluate_event_isolation, test_seed_titles_consistent,
        test_ipl_never_hidden, test_ipl_pinned_top, test_maximal_coverage,
        test_activity_liveness,
    ]:
        fn()
    print(f"\n{'='*52}")
    if _failures:
        print(f"FAILED: {len(_failures)} assertion(s)")
        for m in _failures:
            print(f"  - {m}")
        raise SystemExit(1)
    print("ALL SYNTHETIC UNIT TESTS PASSED")
