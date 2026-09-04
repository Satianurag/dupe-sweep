#!/usr/bin/env python3
"""Step 2: turn the file inventory into duplicate sets.

Reads discover's inventory from its state file (a plain local file read,
not a Rote cross-step substitution -- that path is reserved for the small
pointer/summary, never the bulk data; see discover.py's docstring). Writes
its own full result to a state file for apply.py, and prints a bounded
summary to stdout: a capped preview plus the true totals, so a scan with
many duplicate sets never risks the same 64KB cross-step ceiling that a
raw file inventory hit.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine  # noqa: E402

PREVIEW_CAP = 150


def main():
    discover_summary = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    state_file = discover_summary.get("state_file")

    if not state_file or not os.path.isfile(state_file):
        result = {
            "ok": True, "files_scanned": 0, "duplicate_sets": [], "linked_sets": [], "skips": [],
            "total_reclaimable_bytes": 0, "total_duplicate_files": 0, "total_duplicate_sets": 0,
            "preview_capped": False, "state_file": None,
            "roots_resolved": discover_summary.get("roots_resolved", []),
            "demo": discover_summary.get("demo", False),
            "capped": discover_summary.get("capped", False),
        }
        print(json.dumps(result))
        return

    with open(state_file) as f:
        discover_state = json.load(f)

    records = [engine.FileRecord(**r) for r in discover_state.get("records", [])]
    skips = [engine.SkipRecord(**s) for s in discover_state.get("skips", [])]
    roots_resolved = discover_state.get("roots_resolved", [])

    dup_sets, linked_sets = engine.build_duplicate_sets(records, roots_resolved, skips)

    total_reclaimable = sum(s.reclaimable_bytes for s in dup_sets)
    total_duplicate_files = sum(len(s.duplicates) for s in dup_sets)

    scan_state_file = "dupe_sweep_scan_state.json"
    with open(scan_state_file, "w") as f:
        json.dump({
            "duplicate_sets": [engine.asdict(s) for s in dup_sets],
            "linked_sets": [engine.asdict(s) for s in linked_sets],
            "skips": [engine.asdict(s) for s in skips],
        }, f)

    preview_capped = len(dup_sets) > PREVIEW_CAP or len(skips) > PREVIEW_CAP or len(linked_sets) > PREVIEW_CAP

    result = {
        "ok": True,
        "files_scanned": len(records),
        "duplicate_sets": [engine.asdict(s) for s in dup_sets[:PREVIEW_CAP]],
        "linked_sets": [engine.asdict(s) for s in linked_sets[:PREVIEW_CAP]],
        "skips": [engine.asdict(s) for s in skips[:PREVIEW_CAP]],
        "total_reclaimable_bytes": total_reclaimable,
        "total_duplicate_files": total_duplicate_files,
        "total_duplicate_sets": len(dup_sets),
        "total_linked_sets": len(linked_sets),
        "total_skips": len(skips),
        "preview_capped": preview_capped,
        "state_file": scan_state_file,
        "roots_resolved": roots_resolved,
        "demo": discover_summary.get("demo", False),
        "capped": discover_summary.get("capped", False),
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
