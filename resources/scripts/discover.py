#!/usr/bin/env python3
"""Step 1: build the file inventory to scan.

Every process.exec step in this play runs inside an isolated rote-managed
workspace, not the caller's terminal directory -- confirmed by testing in
a sibling play, not assumed. So `paths` are resolved as given: absolute
paths, or `~`-relative ones expanded against $HOME (which a step does
inherit). Demo mode ignores `paths` and points at the bundled fixture
directory instead, which ships a handful of deliberately duplicated files
so the whole pipeline produces real, meaningful output with zero setup.
"""
import json
import os
import secrets
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine  # noqa: E402


def parse_bool(s: str) -> bool:
    return s.strip().lower() in ("true", "1", "yes")


def main():
    paths_arg = sys.argv[1] if len(sys.argv) > 1 else ""
    min_size_bytes = int(sys.argv[2]) if len(sys.argv) > 2 else 4096
    max_files = int(sys.argv[3]) if len(sys.argv) > 3 else 50000
    include_hidden = parse_bool(sys.argv[4]) if len(sys.argv) > 4 else False
    demo = parse_bool(sys.argv[5]) if len(sys.argv) > 5 else False
    exclude_paths_arg = sys.argv[6] if len(sys.argv) > 6 else ""
    exclude_paths = [p.strip() for p in exclude_paths_arg.split(",") if p.strip()]
    quarantine_dir = sys.argv[7] if len(sys.argv) > 7 and sys.argv[7] else "~/.rote/dupe-sweep/quarantine"
    if not os.path.isabs(os.path.expanduser(quarantine_dir)):
        print(json.dumps({
            "ok": True, "demo": False, "roots_input": [], "roots_resolved": [],
            "file_count": 0, "skip_count": 0, "excluded_count": 0, "capped": False, "state_file": None,
            "warning": f"quarantine_dir must be absolute, got a relative path: {quarantine_dir!r}",
        }))
        return
    # A quarantine_dir sitting inside one of the scanned paths would mean
    # the NEXT run treats run 1's already-quarantined copies as live
    # duplicate candidates -- and since shutil.move preserves mtime, the
    # quarantined copy can even win the oldest-wins keeper tie-break,
    # moving the user's real, live file into quarantine instead. Always
    # excluded automatically; the caller never has to remember to.
    exclude_paths = exclude_paths + [quarantine_dir]
    exclude_non_absolute = [p for p in exclude_paths if not os.path.isabs(os.path.expanduser(p))]
    if exclude_non_absolute:
        print(json.dumps({
            "ok": True, "demo": False, "roots_input": [], "roots_resolved": [],
            "file_count": 0, "skip_count": 0, "excluded_count": 0, "capped": False, "state_file": None,
            "warning": (
                f"exclude_paths must be absolute, got relative path(s): {exclude_non_absolute!r} — "
                "same reason paths must be absolute: steps run in an isolated Rote workspace."
            ),
        }))
        return

    if demo:
        # Copied into a fresh temp dir every run, never scanned in place:
        # demo=true + apply=true must be able to actually move files without
        # ever mutating the fixtures this play ships (or leaving them
        # half-consumed for the next demo run).
        here = os.path.dirname(os.path.abspath(__file__))
        src = os.path.join(os.path.dirname(here), "fixtures", "demo", "DownloadsDemo")
        tmp_root = tempfile.mkdtemp(prefix="dupe-sweep-demo-")
        dest = os.path.join(tmp_root, "DownloadsDemo")
        shutil.copytree(src, dest)
        # copytree copies file *contents*, so the fixture's hardlink pair
        # would silently become two independent inodes -- re-link them here
        # so the demo still exercises the already-linked (not reclaimable)
        # code path, not just plain duplicates.
        la, lb = os.path.join(dest, "linked_a.bin"), os.path.join(dest, "linked_b.bin")
        if os.path.isfile(la) and os.path.isfile(lb):
            os.remove(lb)
            os.link(la, lb)

        # Rote stages this play's own bundled resources into a fresh
        # workspace before this script ever runs, which resets every
        # fixture file's mtime to "now" -- and does so in an order that is
        # copy-timing noise, not the fixture's intended story. Left alone,
        # that makes the demo's keeper choice nondeterministic between
        # runs, which this play explicitly promises never happens. So the
        # demo's chronology is set explicitly and deterministically here,
        # matching the story the fixture was built to tell (the plain-named
        # file is the original download; "_copy"/"(1)" are later re-saves).
        base = 1700000000
        deterministic_order = [
            "invoice_march.pdf",
            os.path.join("Invoices", "invoice_march_copy.pdf"),
            os.path.join("Invoices", "invoice_march (1).pdf"),
            os.path.join("Photos", "vacation.jpg"),
            os.path.join("Photos", "vacation_IMG_0001.jpg"),
            os.path.join("Photos", "family.jpg"),
            "notes.txt",
            "linked_a.bin",
            "tiny_icon.ico",
        ]
        for i, rel in enumerate(deterministic_order):
            p = os.path.join(dest, rel)
            if os.path.isfile(p):
                os.utime(p, (base + i, base + i))
        roots = [dest]
    else:
        roots = [p.strip() for p in paths_arg.split(",") if p.strip()]

    # Every process.exec step's CWD is an isolated Rote-managed workspace,
    # not the caller's terminal directory -- confirmed by testing, not
    # assumed (a sibling play hit this first). A relative `paths` entry
    # would silently resolve against that workspace instead, which for
    # THIS play contains its own bundled demo fixtures -- so `paths=.`
    # would scan and report the play's own shipped files as the caller's
    # duplicates, and apply=true would move them. Refused outright, the
    # same way ci-digest-guard already refuses a non-absolute repo_path.
    if not demo:
        non_absolute = [r for r in roots if not os.path.isabs(os.path.expanduser(r))]
        if non_absolute:
            result = {
                "ok": True, "demo": demo, "roots_input": roots, "roots_resolved": [],
                "file_count": 0, "skip_count": 0, "excluded_count": 0, "capped": False, "state_file": None,
                "warning": (
                    f"paths must be absolute, got relative path(s): {non_absolute!r} — "
                    "steps run in an isolated Rote workspace, not your terminal's directory, "
                    "so a relative path cannot be resolved against anything meaningful. "
                    "Pass an absolute path, e.g. paths=$(pwd) or paths=~/Downloads."
                ),
            }
            print(json.dumps(result))
            return

    # The full file inventory can be arbitrarily large (one entry per
    # scanned file) -- far past the 64KB ceiling Rote enforces on a
    # cross-step @step{...} argv substitution (confirmed by testing: a real
    # scan of a large directory tree failed with a truncated-JSON parse
    # error at exactly byte 65536 when this was passed to the next step
    # inline). So the full inventory is written to a state file in this
    # run's shared workspace directory instead, and stdout carries only a
    # small, boundedly-sized summary plus the state file's path -- the next
    # step reads the file itself rather than receiving the data inline.
    # Unique per invocation: two runs sharing a workspace (concurrent runs,
    # or a fast repeat) must never read or overwrite each other's state --
    # a fixed filename risked exactly that, one run's scan silently reading
    # a half-written or already-superseded inventory from another.
    run_token = secrets.token_hex(6)
    state_file = f"dupe_sweep_discover_state_{run_token}.json"

    if not roots:
        result = {
            "ok": True, "demo": demo, "roots_input": roots, "roots_resolved": [],
            "file_count": 0, "skip_count": 0, "excluded_count": 0, "capped": False, "state_file": None,
            "warning": "no paths given -- pass paths=~/Downloads (comma-separated for more than one), or demo=true",
        }
    else:
        records, skips, resolved_roots, capped, excluded_count = engine.discover_files(
            roots, min_size_bytes, max_files, include_hidden, exclude_paths,
        )
        with open(state_file, "w") as f:
            json.dump({
                "records": [engine.asdict(r) for r in records],
                "skips": [engine.asdict(s) for s in skips],
                "roots_resolved": resolved_roots,
            }, f)
        result = {
            "ok": True,
            "demo": demo,
            "roots_input": roots,
            "roots_resolved": resolved_roots,
            "file_count": len(records),
            "skip_count": len(skips),
            "excluded_count": excluded_count,
            "capped": capped,
            "min_size_bytes": min_size_bytes,
            "max_files": max_files,
            "exclude_paths": exclude_paths,
            "state_file": state_file,
            "run_token": run_token,
        }

    print(json.dumps(result))


if __name__ == "__main__":
    main()
