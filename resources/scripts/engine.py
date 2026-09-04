"""dupe-sweep core engine -- pure python3 standard library, no third-party
dependencies (a deliberate choice: nothing to pip install on a machine that
may refuse it).

Finds exact-duplicate files across one or more directories and, on request,
safely quarantines every duplicate but one per set. "Duplicate" here means
byte-for-byte identical content, proven by a SHA-256 hash match after a
cheap size pre-filter -- never a filename or size guess. Two files with the
same content and different names are duplicates; two files with the same
name and different content are not, and this engine never treats them as
such.

Skipped, on purpose, never silently:
  - zero-byte files: hashing them is a tautology (all empty files "match"),
    and quarantining one reclaims zero bytes, so they are excluded entirely
    rather than reported as a duplicate set that helps nobody.
  - symlinks: never followed, never treated as file content.
  - hardlinks (same st_dev + st_ino at two different paths): these already
    share one block of storage. Reporting them as a duplicate set and
    "reclaiming" space by deleting one would be a false claim -- the bytes
    are not freed, the other link still holds them. Reported separately as
    already_linked, never counted in reclaimable_bytes, never moved.
  - bundle-shaped directories (.app, .framework, .bundle, .photoslibrary,
    .imovielibrary, .fcpbundle) and .git / node_modules: never descended
    into. Deduplicating a file *inside* a macOS app bundle can silently
    break the app; deduplicating inside .git can corrupt a repository. Both
    are treated as opaque leaves, matching this play's zero-risk-to-git
    posture even though its core purpose has nothing to do with git.
  - unreadable files (permission denied, vanished mid-scan): reported as
    UNKNOWN with the OSError reason, never dropped silently and never
    treated as "clear".
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, asdict

HASH_CHUNK = 1024 * 1024  # 1 MiB streamed reads; never load a whole file into memory
BUNDLE_SUFFIXES = (
    ".app", ".framework", ".bundle", ".plugin", ".kext",
    ".photoslibrary", ".imovielibrary", ".fcpbundle", ".theater",
    ".sparsebundle",
)
ALWAYS_SKIP_DIRNAMES = {".git", "node_modules", ".Trash", ".Trashes"}


@dataclass
class FileRecord:
    path: str
    size: int
    mtime: float
    dev: int
    ino: int


@dataclass
class SkipRecord:
    path: str
    reason: str


def is_bundle_dir(name: str) -> bool:
    lower = name.lower()
    return any(lower.endswith(suffix) for suffix in BUNDLE_SUFFIXES)


def should_prune_dir(name: str) -> bool:
    return name in ALWAYS_SKIP_DIRNAMES or is_bundle_dir(name)


def is_hidden(name: str) -> bool:
    return name.startswith(".")


def discover_files(
    roots: list[str],
    min_size_bytes: int,
    max_files: int,
    include_hidden: bool,
) -> tuple[list[FileRecord], list[SkipRecord], list[str], bool]:
    """Walk `roots`; returns (records, skips, resolved_roots, capped)."""
    records: list[FileRecord] = []
    skips: list[SkipRecord] = []
    resolved_roots: list[str] = []
    capped = False
    seen_paths: set[str] = set()

    for raw_root in roots:
        root = os.path.realpath(os.path.expanduser(raw_root))
        if not os.path.isdir(root):
            skips.append(SkipRecord(path=raw_root, reason="not a directory or does not exist"))
            continue
        resolved_roots.append(root)

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # Prune in place: bundles / .git / node_modules / trash are never descended into.
            pruned = []
            keep = []
            for d in dirnames:
                if should_prune_dir(d):
                    pruned.append(d)
                    continue
                if not include_hidden and is_hidden(d):
                    pruned.append(d)
                    continue
                keep.append(d)
            dirnames[:] = keep

            for fname in filenames:
                if not include_hidden and is_hidden(fname):
                    continue
                fpath = os.path.join(dirpath, fname)
                if os.path.islink(fpath):
                    continue  # never follow or hash a symlink's target
                real = os.path.realpath(fpath)
                if real in seen_paths:
                    continue  # same file reached via two overlapping roots
                if len(records) >= max_files:
                    capped = True
                    break
                try:
                    st = os.stat(fpath, follow_symlinks=False)
                except OSError as exc:
                    skips.append(SkipRecord(path=fpath, reason=f"stat failed: {exc.strerror or exc}"))
                    continue
                if st.st_size == 0:
                    continue  # zero-byte files: excluded, see module docstring
                if st.st_size < min_size_bytes:
                    continue
                seen_paths.add(real)
                records.append(FileRecord(
                    path=fpath, size=st.st_size, mtime=st.st_mtime,
                    dev=st.st_dev, ino=st.st_ino,
                ))
            if capped:
                break
        if capped:
            break

    return records, skips, resolved_roots, capped


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


@dataclass
class DuplicateSet:
    sha256: str
    size: int
    keep: str
    duplicates: list[str]
    reclaimable_bytes: int


@dataclass
class LinkedSet:
    sha256: str
    size: int
    paths: list[str]  # hardlinks of one another -- already share storage


def build_duplicate_sets(
    records: list[FileRecord],
    roots_order: list[str],
    skips: list[SkipRecord],
) -> tuple[list[DuplicateSet], list[LinkedSet]]:
    # Cheap pre-filter: only files that share an exact size can possibly match.
    by_size: dict[int, list[FileRecord]] = {}
    for r in records:
        by_size.setdefault(r.size, []).append(r)

    by_hash: dict[str, list[FileRecord]] = {}
    for size, group in by_size.items():
        if len(group) < 2:
            continue
        for r in group:
            # Re-check identity immediately before hashing: a file that
            # changed size or mtime since discovery is not safe to compare
            # against the snapshot taken a moment ago (TOCTOU).
            try:
                st = os.stat(r.path, follow_symlinks=False)
            except OSError as exc:
                skips.append(SkipRecord(path=r.path, reason=f"vanished before hashing: {exc.strerror or exc}"))
                continue
            if st.st_size != r.size or st.st_mtime != r.mtime:
                skips.append(SkipRecord(path=r.path, reason="changed on disk between scan and hash — skipped, not guessed"))
                continue
            try:
                digest = sha256_of(r.path)
            except OSError as exc:
                skips.append(SkipRecord(path=r.path, reason=f"could not read for hashing: {exc.strerror or exc}"))
                continue
            by_hash.setdefault(digest, []).append(r)

    def root_rank(path: str) -> int:
        for i, root in enumerate(roots_order):
            if path.startswith(root + os.sep) or path == root:
                return i
        return len(roots_order)

    def sort_key(r: FileRecord):
        # Oldest file wins as the keeper; ties broken by which scan root was
        # listed first, then by path depth (shorter = more "canonical"),
        # then alphabetically. Every tie-break is deterministic and documented
        # so the same input always produces the same keeper -- never a guess.
        return (r.mtime, root_rank(r.path), r.path.count(os.sep), r.path)

    dup_sets: list[DuplicateSet] = []
    linked_sets: list[LinkedSet] = []

    for digest, group in by_hash.items():
        if len(group) < 2:
            continue
        # Partition by (dev, ino): hardlinks of each other share one inode
        # and already share storage -- they are not "reclaimable" duplicates.
        by_inode: dict[tuple[int, int], list[FileRecord]] = {}
        for r in group:
            by_inode.setdefault((r.dev, r.ino), []).append(r)

        distinct = [grp[0] for grp in by_inode.values()]  # one representative per real file
        for grp in by_inode.values():
            if len(grp) > 1:
                linked_sets.append(LinkedSet(
                    sha256=digest, size=grp[0].size,
                    paths=sorted(x.path for x in grp),
                ))

        if len(distinct) < 2:
            continue  # every path in this hash group was the same underlying file

        ordered = sorted(distinct, key=sort_key)
        keeper = ordered[0]
        dups = ordered[1:]
        dup_sets.append(DuplicateSet(
            sha256=digest,
            size=keeper.size,
            keep=keeper.path,
            duplicates=[d.path for d in dups],
            reclaimable_bytes=keeper.size * len(dups),
        ))

    dup_sets.sort(key=lambda s: -s.reclaimable_bytes)
    return dup_sets, linked_sets


@dataclass
class ManifestEntry:
    original_path: str
    quarantined_path: str
    sha256: str
    size: int
    kept_path: str


def apply_quarantine(
    dup_sets: list[DuplicateSet],
    quarantine_root: str,
) -> tuple[list[ManifestEntry], list[SkipRecord], str]:
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest_dir = os.path.join(os.path.expanduser(quarantine_root), run_id)
    os.makedirs(dest_dir, exist_ok=True)

    manifest: list[ManifestEntry] = []
    failures: list[SkipRecord] = []

    for s in dup_sets:
        for dup_path in s.duplicates:
            try:
                st = os.stat(dup_path, follow_symlinks=False)
            except OSError as exc:
                failures.append(SkipRecord(path=dup_path, reason=f"vanished before move: {exc.strerror or exc}"))
                continue
            if st.st_size != s.size:
                failures.append(SkipRecord(path=dup_path, reason="size changed since scan — not moved"))
                continue

            base = os.path.basename(dup_path)
            dest = os.path.join(dest_dir, base)
            suffix = 1
            while os.path.exists(dest):
                stem, ext = os.path.splitext(base)
                dest = os.path.join(dest_dir, f"{stem}__{suffix}{ext}")
                suffix += 1

            try:
                shutil.move(dup_path, dest)
            except OSError as exc:
                failures.append(SkipRecord(path=dup_path, reason=f"move failed: {exc.strerror or exc}"))
                continue

            # Verify: source actually gone, destination actually present with the same size.
            src_gone = not os.path.exists(dup_path)
            dest_ok = os.path.isfile(dest) and os.path.getsize(dest) == s.size
            if not (src_gone and dest_ok):
                failures.append(SkipRecord(path=dup_path, reason="post-move verification failed — treat as unresolved"))
                continue

            manifest.append(ManifestEntry(
                original_path=dup_path,
                quarantined_path=dest,
                sha256=s.sha256,
                size=s.size,
                kept_path=s.keep,
            ))

    manifest_path = os.path.join(dest_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump([asdict(m) for m in manifest], f, indent=2)

    undo_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "undo.py")
    if os.path.isfile(undo_src):
        shutil.copy2(undo_src, os.path.join(dest_dir, "undo.py"))

    return manifest, failures, dest_dir
