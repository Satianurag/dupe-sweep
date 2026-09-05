#!/usr/bin/env python3
"""Undo one dupe-sweep quarantine run. Standalone stdlib script -- this file
gets copied into every quarantine run directory alongside manifest.json, so
undo works even without Rote installed, from the quarantine directory
itself: `python3 undo.py` (run from inside the dated quarantine folder, or
pass the manifest path explicitly as argv[1]).

Refuses to overwrite anything already sitting at the original path -- if a
new file has since been created there, that entry is reported and skipped
rather than clobbered.
"""
import json
import os
import shutil
import sys


def main():
    manifest_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.getcwd(), "manifest.json")
    if not os.path.isfile(manifest_path):
        print(f"no manifest found at {manifest_path}")
        return 1

    with open(manifest_path) as f:
        entries = json.load(f)

    restored = 0
    skipped = []
    for entry in entries:
        original = entry["original_path"]
        quarantined = entry["quarantined_path"]
        if os.path.exists(original):
            skipped.append((quarantined, f"original path already occupied: {original}"))
            continue
        if not os.path.isfile(quarantined):
            skipped.append((quarantined, "no longer in quarantine (already restored or moved)"))
            continue
        try:
            os.makedirs(os.path.dirname(original), exist_ok=True)
            shutil.move(quarantined, original)
        except OSError as exc:
            # One entry's original parent being read-only, or itself now a
            # file instead of a directory, must not abort every remaining
            # entry -- confirmed as a real gap: an unguarded move here
            # meant a single bad entry silently stopped every restore
            # after it, with no "restored N" summary ever printed at all.
            skipped.append((quarantined, f"restore failed: {exc.strerror or exc}"))
            continue
        restored += 1

    print(f"restored {restored} file(s)")
    for path, reason in skipped:
        print(f"  skipped: {path} -- {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
