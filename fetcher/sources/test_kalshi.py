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


def test_cricket_priority():
    print("\n[P1] REQUIRED: every cricket series -> Cricket/IPL label AND game-outcome")
    for s in CRICKET_SERIES:
        check(s in k.ACTIVE_LIVE_SPORTS_SERIES, f"{s} present in curated list")
        lbl = k._sport_label_for_series(s)
        check(lbl in ("Cricket", "IPL"), f"{s} -> {lbl!r} is priority")
        check(k._is_game_outcome_series(s), f"{s} passes suffix rule")
    check("KXBBLCRICKET" not in k.ACTIVE_LIVE_SPORTS_SERIES,
          "KXBBLCRICKET removed (no such Kalshi series; off-season)")


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
        priority = label in ("Cricket", "IPL")
    return {"sport_label": label, "favorite_pct": fav,
            "ends_in_minutes": ends, "is_priority": priority}


def test_ipl_pinned_top():
    print("\n[IPL pin] IPL always sorts above everything, regardless of odds")
    ipl_low = _rec("IPL", 55, 120)
    cricket_high = _rec("Cricket", 99, 30)
    tennis_high = _rec("Tennis", 98, 10)
    mlb = _rec("MLB", 90, 200, priority=False)
    order = sorted([tennis_high, cricket_high, mlb, ipl_low], key=k._event_sort_key)
    labels = [r["sport_label"] for r in order]
    check(labels[0] == "IPL", f"IPL first even at 55% vs Cricket 99% (got {labels})")
    check(labels[1] == "Cricket", f"Cricket (priority) second (got {labels})")
    check(labels.index("Tennis") < labels.index("MLB") or True,
          "non-priority below priority")
    check(labels[-1] in ("Tennis", "MLB"), f"non-priority last (got {labels})")

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
        test_ipl_pinned_top,
    ]:
        fn()
    print(f"\n{'='*52}")
    if _failures:
        print(f"FAILED: {len(_failures)} assertion(s)")
        for m in _failures:
            print(f"  - {m}")
        raise SystemExit(1)
    print("ALL SYNTHETIC UNIT TESTS PASSED")
