# dupe-sweep

**Your Downloads folder has the same invoice three times and you didn't put them there. This finds files that are byte-for-byte identical — proven by a hash match, never a filename guess — and safely quarantines every copy but one.**

```
dupe-sweep · 8 file(s) scanned · ~/Downloads

  FOUND    4.92 KB × 3 copies
           keep: invoice_march.pdf
           dup:  Invoices/invoice_march_copy.pdf
           dup:  Invoices/invoice_march (1).pdf

  DRY RUN — 14.8 KB reclaimable across 3 file(s).
  Nothing was touched. Re-run with apply=true to quarantine duplicates (reversible, never deleted).
```

## Why this exists

A browser re-download, a cloud-sync conflict copy (`file (1).pdf`), an export
run twice, a photo library backed up from two phones — every one of these
leaves an exact, silent duplicate somewhere on disk. Not a "similar" file, not
a same-name-different-content collision: byte-for-byte identical content,
proven by a SHA-256 hash match after a cheap size pre-filter, never a
filename or size guess. Two files with the same content and different names
are duplicates; two files with the same name and different content are never
treated as one, no matter how close their size.

This is the kind of thing everyone who has ever used a computer does
repeatedly and manually — eyeballing a Downloads folder, deleting the one
that looks newer — except a person can't verify byte-for-byte identity by
eye, and a mistaken delete of the *wrong* copy is not something eyeballing
can undo. This play never deletes: it quarantines, with a manifest and an
`undo.py` that ships inside the quarantine folder itself, so restoring needs
no Rote install, just `python3`.

## What it will never do

- **Never delete.** Duplicates move to a dated quarantine folder; nothing is
  removed from disk until the user removes the quarantine folder themselves.
- **Never guess.** A hardlinked pair (two paths already sharing one inode) is
  detected and reported separately — deleting one frees zero bytes, so it is
  never counted as reclaimable and never moved. Zero-byte files are excluded
  outright: hashing them is a tautology.
- **Never descend into a macOS app bundle** (`.app`/`.framework`/`.bundle`/
  `.photoslibrary` and friends) or **`.git`/`node_modules`** — both are
  treated as opaque leaves. Deduplicating a file inside one can silently
  break an application or corrupt a repository; this play's job is freeing
  clutter, not taking that risk.
- **Never act on stale data.** A file that changes size or mtime between the
  initial scan and the moment it would be hashed or moved is skipped with a
  stated reason (a real TOCTOU guard, not a theoretical one).
- **Never silently drop a file it couldn't read.** Permission denied, or a
  file that vanished mid-scan, is reported as a named skip — never dropped,
  never counted as clear.

## The keeper is chosen deterministically

Oldest file first, then by which scanned path was listed first, then
shortest path, then alphabetically. Every tie-break is documented and
applied in a fixed order, so the same input always produces the same
keeper — never a coin flip, and never a different answer on a second run of
identical input.

`keep_rule` makes the *primary* rule explicit instead of always defaulting
to oldest — `oldest` (default), `newest`, or `priority` (prefer a file under
a designated `priority_paths` folder, e.g. always keep the copy in your
Photos library over the one in Downloads). This was not invented from
scratch: real duplicate-finder tools (Duplicate Cleaner Pro's Selection
Assistant, Nektony's "Always Select" list) document exactly these three
rule shapes, verified before building rather than guessed at. A duplicate
set with no file under any `priority_paths` folder falls back to `oldest`
for that set — and `keep_rule=priority` with `priority_paths` left empty
degrades to `oldest` for the whole run — both a stated fallback in the
output, never a silent one.

`exclude_paths` skips named directories or files entirely, pruned during
the walk itself so an excluded path's contents are never even `stat()`'d.
Also verified before building: fdupes' own GitHub issue tracker carries a
long-standing user request for exactly an `--exclude` flag.

## Built and found real problems along the way

This is not the first design that shipped. Three real bugs surfaced during
testing, on real data, not synthetic fixtures alone:

1. **A real 64KB ceiling on cross-step data passing.** Rote's `@step{...}`
   substitution — used to hand one step's output to the next as an argv
   value — silently truncates at 65536 bytes, which a raw file inventory for
   a large real directory (`~/Documents`, 12 GB, thousands of files) blew
   past, breaking JSON mid-string on the next step. Fixed by routing bulk
   data through a state file in the run's own workspace instead: each step
   writes its full result to disk and passes only a small pointer/summary
   through the DAG, capped in every case to stay well under that ceiling —
   confirmed by re-running the same 12 GB scan afterward, which completed in
   under a second and found real duplicates (icon files that exist in both
   a `mobile/` and `web/` app tree, a stray dist copy of a JS bundle,
   a duplicated `LICENSE` file, a cross-repo duplicated test file).
2. **A non-deterministic demo.** Rote stages a play's own bundled resource
   files into a fresh workspace before every run, which resets every
   fixture file's mtime to "now" — in an order that is copy-timing noise,
   not the fixture's intended story. Left alone, that made the bundled
   demo's "which file is the keeper" choice change between runs of
   `demo=true` on identical input, which directly contradicts this play's
   own determinism guarantee. Fixed by explicitly setting the demo's
   chronology after staging, so `demo=true` produces the exact same keeper
   on every run — verified by running it twice in a row and diffing the
   output.
3. **Hardlinks silently double-counted as reclaimable.** An early version
   treated any two paths with matching content as a duplicate set worth
   quarantining. A hardlinked pair already shares one inode's storage —
   "reclaiming" it by moving one copy frees zero bytes and, worse, would
   have broken the other link's mtime/permissions assumptions. Fixed by
   partitioning hash matches by `(st_dev, st_ino)` and reporting hardlinks
   in their own `linked_sets`, excluded from every reclaimable-bytes count.

Also verified directly, not assumed: a permission-denied file surfaces as a
named `UNKNOWN` skip (not a silent drop or a crash); a symlink is never
followed or hashed; a `.app` bundle's internal files are never touched even
when the bundle sits inside a scanned directory; a file that changes on disk
between scan and move is skipped rather than acted on with stale data.

## Usage

```bash
# Zero setup: bundled fixtures (including a real hardlinked pair), in an
# isolated temp copy -- apply=true is safe to try here, nothing real is touched
rote play run satianurag/dupe-sweep demo=true
rote play run satianurag/dupe-sweep demo=true apply=true

# Report only, against real directories -- dry run is the default
rote play run satianurag/dupe-sweep paths=~/Downloads
rote play run satianurag/dupe-sweep paths=~/Downloads,~/Desktop

# Actually quarantine duplicates (reversible, never deleted)
rote play run satianurag/dupe-sweep paths=~/Downloads apply=true

# Always keep the copy under your Photos library; skip an archive folder entirely
rote play run satianurag/dupe-sweep paths=~/Downloads,~/Pictures \
  keep_rule=priority priority_paths=~/Pictures/Library \
  exclude_paths=~/Downloads/Archives apply=true
```

Undo any run:

```bash
python3 ~/.rote/dupe-sweep/quarantine/<run-timestamp>/undo.py
```

No third-party dependencies: pure `python3` standard library end to end —
`hashlib`, `os`, `shutil`, `json`, `dataclasses`. Nothing to `pip install` on
a machine that may not allow it. No network, no credentials, no `git`/`gh` —
every step is a local filesystem operation.
