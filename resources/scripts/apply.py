#!/usr/bin/env python3
"""Step 3 (conditional on apply=true): quarantine every duplicate but the
keeper in each set, and write a manifest that can put every one of them
back. Never runs in demo mode or dry-run mode -- see main.ts for the
condition. Quarantine, never delete: this play is reversible by design,
the same posture as downloads-filed's sweep+manifest pattern in this same
registry.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine  # noqa: E402


def main():
    scan_summary = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    quarantine_root = sys.argv[2] if len(sys.argv) > 2 else "~/.rote/dupe-sweep/quarantine"

    # Read the full duplicate-set list from scan's own state file, never
    # from its (possibly capped) stdout preview -- apply must quarantine
    # every duplicate, not just the ones shown in a human-readable summary.
    state_file = scan_summary.get("state_file")
    if state_file and os.path.isfile(state_file):
        with open(state_file) as f:
            scan_state = json.load(f)
        dup_sets_raw = scan_state.get("duplicate_sets", [])
    else:
        dup_sets_raw = scan_summary.get("duplicate_sets", [])
    dup_sets = [engine.DuplicateSet(**s) for s in dup_sets_raw]

    if not dup_sets:
        result = {"ok": True, "moved": 0, "failures": [], "quarantine_dir": None, "manifest": []}
    else:
        manifest, failures, dest_dir = engine.apply_quarantine(dup_sets, quarantine_root)
        result = {
            "ok": True,
            "moved": len(manifest),
            "failures": [engine.asdict(f) for f in failures],
            "quarantine_dir": dest_dir,
            "manifest": [engine.asdict(m) for m in manifest],
        }

    # Terminal step -- nothing downstream consumes a "packed" field from
    # this one, unlike discover/scan which feed the next step in the DAG.
    print(json.dumps(result))


if __name__ == "__main__":
    main()
