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
    keep_rule = sys.argv[2] if len(sys.argv) > 2 else "oldest"
    priority_paths_arg = sys.argv[3] if len(sys.argv) > 3 else ""
    priority_paths = [p.strip() for p in priority_paths_arg.split(",") if p.strip()]
    priority_non_absolute = [p for p in priority_paths if not os.path.isabs(os.path.expanduser(p))]
    if priority_non_absolute:
        print(json.dumps({
            "ok": True, "files_scanned": 0, "duplicate_sets": [], "linked_sets": [], "skips": [],
            "total_reclaimable_bytes": 0, "total_duplicate_files": 0, "total_duplicate_sets": 0,
            "preview_capped": False, "state_file": None, "keep_rule_effective": keep_rule,
            "roots_resolved": discover_summary.get("roots_resolved", []),
            "demo": discover_summary.get("demo", False),
            "capped": discover_summary.get("capped", False),
            "warning": (
                f"priority_paths must be absolute, got relative path(s): {priority_non_absolute!r} — "
                "steps run in an isolated Rote workspace, so a relative path would never match a real file."
            ),
        }))
        return
    state_file = discover_summary.get("state_file")

    if not state_file:
        # discover itself found nothing to scan (no paths given) -- an
        # honest, already-reported case, not a failure of this step.
        result = {
            "ok": True, "files_scanned": 0, "duplicate_sets": [], "linked_sets": [], "skips": [],
            "total_reclaimable_bytes": 0, "total_duplicate_files": 0, "total_duplicate_sets": 0,
            "preview_capped": False, "state_file": None, "keep_rule_effective": keep_rule,
            "roots_resolved": discover_summary.get("roots_resolved", []),
            "demo": discover_summary.get("demo", False),
            "capped": discover_summary.get("capped", False),
        }
        print(json.dumps(result))
        return

    if not os.path.isfile(state_file):
        # discover PROMISED a state file at this exact path and it is
        # missing -- a disk-full write failure, a recycled/corrupted
        # workspace, or two concurrent runs sharing one workspace and
        # colliding on this same fixed filename. Confirmed as a real gap:
        # the old code treated this identically to "nothing to scan" and
        # reported files_scanned: 0, duplicate_sets: [] with ok: true,
        # which main.ts then rendered as a clean CLEAR — the one verdict
        # this play must never fabricate when it did not actually look.
        result = {
            "ok": True, "error": f"discover reported a state file that no longer exists: {state_file}",
            "files_scanned": 0, "duplicate_sets": [], "linked_sets": [], "skips": [],
            "total_reclaimable_bytes": 0, "total_duplicate_files": 0, "total_duplicate_sets": 0,
            "preview_capped": False, "state_file": None, "keep_rule_effective": keep_rule,
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

    dup_sets, linked_sets, effective_rule = engine.build_duplicate_sets(
        records, roots_resolved, skips, keep_rule, priority_paths,
    )

    total_reclaimable = sum(s.reclaimable_bytes for s in dup_sets)
    total_duplicate_files = sum(len(s.duplicates) for s in dup_sets)

    # Reuses discover's own run_token, if present, so the whole run's state
    # files share one identifiable family and never collide with a
    # concurrent or fast-repeat run's files in the same workspace.
    run_token = discover_summary.get("run_token") or "legacy"
    scan_state_file = f"dupe_sweep_scan_state_{run_token}.json"
    with open(scan_state_file, "w") as f:
        json.dump({
            "duplicate_sets": [engine.asdict(s) for s in dup_sets],
            "linked_sets": [engine.asdict(s) for s in linked_sets],
            "skips": [engine.asdict(s) for s in skips],
        }, f)

    # PREVIEW_CAP alone only bounds how many SETS are shown -- one hash
    # group of tens of thousands of identical files (a synced asset/thumbnail
    # cache) is still exactly one set, carrying an unbounded `duplicates`
    # list, and could reproduce the same 64KB cross-step ceiling this
    # capping was introduced to avoid in the first place. Every set's own
    # duplicates list is capped too for the STDOUT preview -- apply.py
    # still reads the complete, uncapped list from scan_state_file.
    DUPLICATES_PER_SET_CAP = 50
    any_set_truncated = any(len(s.duplicates) > DUPLICATES_PER_SET_CAP for s in dup_sets[:PREVIEW_CAP])
    preview_capped = (
        len(dup_sets) > PREVIEW_CAP or len(skips) > PREVIEW_CAP
        or len(linked_sets) > PREVIEW_CAP or any_set_truncated
    )

    def capped_set_dict(s: engine.DuplicateSet) -> dict:
        d = engine.asdict(s)
        if len(d["duplicates"]) > DUPLICATES_PER_SET_CAP:
            d["duplicates"] = d["duplicates"][:DUPLICATES_PER_SET_CAP]
        d.pop("duplicate_mtimes", None)  # internal bookkeeping, not needed in the preview
        return d

    result = {
        "ok": True,
        "files_scanned": len(records),
        "duplicate_sets": [capped_set_dict(s) for s in dup_sets[:PREVIEW_CAP]],
        "linked_sets": [engine.asdict(s) for s in linked_sets[:PREVIEW_CAP]],
        "skips": [engine.asdict(s) for s in skips[:PREVIEW_CAP]],
        "total_reclaimable_bytes": total_reclaimable,
        "total_duplicate_files": total_duplicate_files,
        "total_duplicate_sets": len(dup_sets),
        "total_linked_sets": len(linked_sets),
        "total_skips": len(skips),
        "preview_capped": preview_capped,
        "state_file": scan_state_file,
        "keep_rule_requested": keep_rule,
        "keep_rule_effective": effective_rule,
        "excluded_count": discover_summary.get("excluded_count", 0),
        "roots_resolved": roots_resolved,
        "demo": discover_summary.get("demo", False),
        "capped": discover_summary.get("capped", False),
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
