"""Daily orchestrator: fetch NODE holdings, append snapshot, regenerate latest.json.

Idempotent:
- If a snapshot for the as-of date already exists on disk, the snapshot file
  is NOT overwritten. (Holdings files are immutable per the publication.)
- ``latest.json`` is always regenerated — so a re-run picks up code changes
  in the analysis layer without needing new data.

Run as a module from the repo root:

    python3 -m fetcher.node_etf.run

Exit codes:
  0 = success (with or without a new snapshot today)
  1 = fetch or write failure
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from glob import glob

from . import analyze, fetch as fetch_mod


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(os.path.dirname(SCRIPT_DIR))
NODE_DIR = os.path.join(PROJECT_DIR, "public", "node")
SNAPSHOT_DIR = os.path.join(NODE_DIR, "snapshots")
LATEST_PATH = os.path.join(NODE_DIR, "latest.json")


def _load_all_snapshots() -> list[dict]:
    """Load every snapshot file, sorted oldest -> newest by as-of date."""
    paths = sorted(glob(os.path.join(SNAPSHOT_DIR, "*.json")))
    out: list[dict] = []
    for p in paths:
        try:
            with open(p, "r") as f:
                out.append(json.load(f))
        except Exception as e:
            print(f"[run] WARNING: skipped malformed snapshot {p}: {e}",
                  file=sys.stderr)
    # Defensive sort by as_of even if filenames are correct
    out.sort(key=lambda s: s.get("as_of", ""))
    return out


def _snapshot_path(as_of: str) -> str:
    return os.path.join(SNAPSHOT_DIR, f"{as_of}.json")


def _write_json(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
        f.write("\n")
    os.replace(tmp, path)


def _snapshot_dict_from_fetch(result: "fetch_mod.FetchResult") -> dict:
    """Shape the fetch result into the snapshot JSON schema."""
    return {
        "ticker": "NODE",
        "as_of": result.as_of,
        "fetched_at": result.fetched_at,
        "source_filename": result.source_filename,
        "fund": result.fund,
        "holdings": result.holdings,
    }


def main() -> int:
    print(f"[node_etf] Starting daily run. Snapshot dir: {SNAPSHOT_DIR}")

    # 1. Fetch today's data from VanEck.
    try:
        result = fetch_mod.fetch()
    except Exception as e:
        print(f"[node_etf] FETCH FAILED: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    snapshot = _snapshot_dict_from_fetch(result)
    target = _snapshot_path(result.as_of)

    # 2. Append snapshot if we don't already have this date.
    if os.path.exists(target):
        print(f"[node_etf] Snapshot already exists for {result.as_of} — "
              "not overwriting.")
        snapshot_added = False
    else:
        try:
            _write_json(target, snapshot)
            print(f"[node_etf] Wrote new snapshot: {target}")
            snapshot_added = True
        except Exception as e:
            print(f"[node_etf] FAILED to write snapshot: {e}", file=sys.stderr)
            return 1

    # 3. Regenerate latest.json from all on-disk snapshots.
    try:
        snapshots = _load_all_snapshots()
        if not snapshots:
            # Fall back to the just-fetched one (e.g. very first run before write)
            snapshots = [snapshot]
        latest = analyze.build_latest(snapshots)
        _write_json(LATEST_PATH, latest)
        print(f"[node_etf] Regenerated {LATEST_PATH} from "
              f"{len(snapshots)} snapshot(s).")
    except Exception as e:
        print(f"[node_etf] FAILED to build latest.json: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    print(f"[node_etf] Done. as_of={result.as_of}, "
          f"new_snapshot={snapshot_added}, "
          f"history_size={len(snapshots)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
