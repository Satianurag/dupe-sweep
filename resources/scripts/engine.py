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
  - anything under exclude_paths: pruned during the walk itself (never
    stat()'d, not merely filtered after the fact), and a scan root that is
    itself excluded is reported as a skip rather than silently scanned
    anyway or silently ignored.

Which copy is kept is controlled by keep_rule (oldest | newest | priority),
verified against real, documented behavior in mature duplicate-finder tools
(Duplicate Cleaner Pro's "keep newest"/"by priority folder" rules, Nektony's
"Always Select" list) rather than invented from scratch. Whatever the rule,
every tie beneath it resolves the same documented way -- root order, then
path depth, then alphabetical -- so the same input always produces the same
keeper. keep_rule=priority with no priority_paths given is a contradiction
in the caller's own arguments: it degrades to "oldest", never to an
unspecified default, and the caller can detect that this happened.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, asdict, field

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


def resolve_exclude_prefixes(exclude_paths: list[str]) -> list[tuple[str, tuple[int, int] | None]]:
    """Returns (string_path, (dev, ino) or None) pairs. The stat identity is
    what actually decides exclusion (see is_excluded); the string is kept
    only to report a path that doesn't exist as a named warning rather
    than silently matching nothing."""
    resolved = []
    for p in exclude_paths:
        p = p.strip()
        if not p:
            continue
        real = os.path.realpath(os.path.expanduser(p))
        try:
            st = os.stat(real)
            identity = (st.st_dev, st.st_ino)
        except OSError:
            identity = None
        resolved.append((real, identity))
    return resolved


def is_excluded(path: str, exclude_prefixes: list[tuple[str, tuple[int, int] | None]]) -> bool:
    """Identity-based, not string-based: os.path.realpath does NOT correct
    case on macOS's default case-insensitive-but-case-preserving APFS --
    confirmed directly (realpath('/x/archives') stays '/x/archives' even
    when the real directory on disk is 'Archives'), while os.stat resolves
    both to the identical (dev, ino) since the filesystem itself is
    case-insensitive. A string-prefix check alone would silently exclude
    nothing for a caller who typed the wrong case, and with apply=true
    the files they explicitly listed as "never touch" get moved anyway."""
    if not exclude_prefixes:
        return False
    try:
        st_target = os.stat(path)
        target_identity = (st_target.st_dev, st_target.st_ino)
    except OSError:
        target_identity = None

    real = os.path.realpath(path)
    for prefix, prefix_identity in exclude_prefixes:
        if real == prefix or real.startswith(prefix + os.sep):
            return True
        if target_identity is not None and prefix_identity is not None and target_identity == prefix_identity:
            return True
    # Ancestry walk by identity, for a differently-cased path whose EXACT
    # string never matches the prefix string at any level.
    if target_identity is not None:
        current = os.path.dirname(real)
        seen_dev_ino = set()
        while current and current != os.path.dirname(current):
            try:
                st = os.stat(current)
            except OSError:
                break
            key = (st.st_dev, st.st_ino)
            if key in seen_dev_ino:
                break  # symlink loop guard
            seen_dev_ino.add(key)
            for _prefix, prefix_identity in exclude_prefixes:
                if prefix_identity is not None and key == prefix_identity:
                    return True
            current = os.path.dirname(current)
    return False


def discover_files(
    roots: list[str],
    min_size_bytes: int,
    max_files: int,
    include_hidden: bool,
    exclude_paths: list[str] | None = None,
) -> tuple[list[FileRecord], list[SkipRecord], list[str], bool, int]:
    """Walk `roots`; returns (records, skips, resolved_roots, capped, excluded_count)."""
    records: list[FileRecord] = []
    skips: list[SkipRecord] = []
    resolved_roots: list[str] = []
    capped = False
    excluded_count = 0
    seen_paths: set[str] = set()
    seen_root_identity: set[tuple[int, int]] = set()
    exclude_prefixes = resolve_exclude_prefixes(exclude_paths or [])

    for raw_root in roots:
        root = os.path.realpath(os.path.expanduser(raw_root))
        if not os.path.isdir(root):
            skips.append(SkipRecord(path=raw_root, reason="not a directory or does not exist"))
            continue
        if is_excluded(root, exclude_prefixes):
            # A scan root that is itself excluded is a contradiction in the
            # caller's own arguments -- surfaced as a skip, never silently
            # scanned anyway and never silently dropped without a trace.
            skips.append(SkipRecord(path=root, reason="this path is itself in exclude_paths — nothing scanned here"))
            continue
        # Two `paths` entries that name the SAME on-disk directory --
        # exact duplicates, or two differently-cased spellings of one
        # directory on macOS's case-insensitive-but-case-preserving APFS
        # (`paths=<root>/d1,<root>/D1`) -- must be walked once, not twice.
        # This is deliberately scoped to ROOTS only: two files reached
        # during a single walk that happen to be hardlinks of each other
        # are a different, legitimate thing this play already detects and
        # reports separately (see build_duplicate_sets' by_inode
        # partition) -- deduping by identity at the per-file level instead
        # of here was tried and found to silently make one of a real
        # hardlinked pair disappear before it ever reached that logic.
        try:
            root_identity = (os.stat(root).st_dev, os.stat(root).st_ino)
        except OSError:
            root_identity = None
        if root_identity is not None:
            if root_identity in seen_root_identity:
                skips.append(SkipRecord(path=root, reason="same directory as another paths entry (possibly different case) — scanned once"))
                continue
            seen_root_identity.add(root_identity)
        resolved_roots.append(root)

        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # Prune in place: bundles / .git / node_modules / trash / explicit
            # exclude_paths are never descended into -- an excluded directory's
            # contents are never even stat()'d, not merely filtered afterward.
            pruned = []
            keep = []
            for d in dirnames:
                dpath = os.path.join(dirpath, d)
                if should_prune_dir(d):
                    pruned.append(d)
                    continue
                if not include_hidden and is_hidden(d):
                    pruned.append(d)
                    continue
                if is_excluded(dpath, exclude_prefixes):
                    excluded_count += 1
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
                if is_excluded(fpath, exclude_prefixes):
                    excluded_count += 1
                    continue
                if len(records) >= max_files:
                    capped = True
                    break
                try:
                    st = os.stat(fpath, follow_symlinks=False)
                except OSError as exc:
                    skips.append(SkipRecord(path=fpath, reason=f"stat failed: {exc.strerror or exc}"))
                    continue
                # Realpath STRING, deliberately not identity: this catches
                # the same path reached twice through overlapping roots
                # (paths=~/a,~/a/b re-walks everything under b twice).
                # Identity (dev, ino) was tried here instead, to also catch
                # two differently-cased ROOT directories -- but that also
                # silently made one half of a real hardlinked pair vanish
                # before build_duplicate_sets' own by_inode partition ever
                # saw it, which is where hardlink detection actually
                # belongs. The differently-cased-root case is now handled
                # once, at the root level, above -- not here.
                real = os.path.realpath(fpath)
                if real in seen_paths:
                    continue  # same file reached via two overlapping roots
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

    return records, skips, resolved_roots, capped, excluded_count


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
    duplicate_mtimes: dict[str, float] = field(default_factory=dict)


@dataclass
class LinkedSet:
    sha256: str
    size: int
    paths: list[str]  # hardlinks of one another -- already share storage


def resolve_priority_prefixes(priority_paths: list[str]) -> list[str]:
    resolved = []
    for p in priority_paths:
        p = p.strip()
        if not p:
            continue
        resolved.append(os.path.realpath(os.path.expanduser(p)))
    return resolved


def priority_rank(path: str, priority_prefixes: list[str]) -> int:
    real = os.path.realpath(path)
    for i, prefix in enumerate(priority_prefixes):
        if real == prefix or real.startswith(prefix + os.sep):
            return i
    return len(priority_prefixes)  # no match -- lowest priority, sorts last


def build_duplicate_sets(
    records: list[FileRecord],
    roots_order: list[str],
    skips: list[SkipRecord],
    keep_rule: str = "oldest",
    priority_paths: list[str] | None = None,
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

    priority_prefixes = resolve_priority_prefixes(priority_paths or [])
    # keep_rule=priority with no priority_paths given is a contradiction in
    # the caller's own arguments, not a silent fallback pretending to be the
    # requested behavior -- it degrades to "oldest" but every set built under
    # it is honest, so callers can surface that this happened.
    effective_rule = keep_rule if not (keep_rule == "priority" and not priority_prefixes) else "oldest"

    def sort_key(r: FileRecord):
        # Every tie-break below this rule's primary key is deterministic and
        # documented, in the same fixed order regardless of rule, so the same
        # input always produces the same keeper -- never a guess:
        #   root order (which scanned path was listed first) -> path depth
        #   (shorter is more "canonical") -> alphabetical.
        secondary = (root_rank(r.path), r.path.count(os.sep), r.path)
        if effective_rule == "newest":
            return (-r.mtime,) + secondary
        if effective_rule == "priority":
            return (priority_rank(r.path, priority_prefixes), r.mtime) + secondary
        return (r.mtime,) + secondary  # "oldest", the default

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
            duplicate_mtimes={d.path: d.mtime for d in dups},
        ))

    dup_sets.sort(key=lambda s: -s.reclaimable_bytes)
    return dup_sets, linked_sets, effective_rule


@dataclass
class ManifestEntry:
    original_path: str
    quarantined_path: str
    sha256: str
    size: int
    kept_path: str


def _write_manifest(manifest_path: str, manifest: list[ManifestEntry]) -> None:
    with open(manifest_path, "w") as f:
        json.dump([asdict(m) for m in manifest], f, indent=2)


def apply_quarantine(
    dup_sets: list[DuplicateSet],
    quarantine_root: str,
) -> tuple[list[ManifestEntry], list[SkipRecord], str]:
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest_dir = os.path.join(os.path.expanduser(quarantine_root), run_id)
    os.makedirs(dest_dir, exist_ok=True)

    manifest_path = os.path.join(dest_dir, "manifest.json")
    manifest: list[ManifestEntry] = []
    # Written empty and undo.py copied in BEFORE the first file is ever
    # moved -- confirmed necessary by tracing what happens if this step is
    # killed (timeout, apply=true on thousands of files, a cross-device
    # move that silently degrades shutil.move to copy+delete on 20GB):
    # every move up to that point had already happened, but with the old
    # write-once-at-the-end manifest, nothing anywhere recorded where any
    # of them came from. This play's one central promise is "reversible,
    # never deleted" -- that promise was void for exactly the runs large
    # enough, or slow enough, to need it most.
    _write_manifest(manifest_path, manifest)
    undo_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "undo.py")
    if os.path.isfile(undo_src):
        shutil.copy2(undo_src, os.path.join(dest_dir, "undo.py"))

    failures: list[SkipRecord] = []

    for s in dup_sets:
        # The keeper is never re-verified once chosen at scan time -- if it
        # was itself deleted or moved since, every "duplicate" in this set
        # would otherwise be quarantined, leaving zero copies anywhere.
        # Confirmed as a real, not hypothetical, gap in the original apply
        # logic, which only ever re-checked the files it was about to move.
        if not os.path.isfile(s.keep):
            for dup_path in s.duplicates:
                failures.append(SkipRecord(
                    path=dup_path,
                    reason=f"keeper no longer exists ({s.keep}) — refusing to quarantine any copy in this set",
                ))
            continue

        for dup_path in s.duplicates:
            try:
                st = os.stat(dup_path, follow_symlinks=False)
            except OSError as exc:
                failures.append(SkipRecord(path=dup_path, reason=f"vanished before move: {exc.strerror or exc}"))
                continue
            # Size alone is not proof the content is unchanged -- a file
            # edited in place to the same byte length (a config file, a
            # fixed-width record, a tool that rewrites a timestamp) would
            # otherwise be quarantined as if it were still the identical
            # duplicate hashed at scan time. mtime is checked too, matching
            # the same TOCTOU guard build_duplicate_sets already applies
            # before hashing -- this is the same check at the other end of
            # the same window, not a new invariant.
            if st.st_size != s.size or st.st_mtime != s.duplicate_mtimes.get(dup_path):
                failures.append(SkipRecord(path=dup_path, reason="changed on disk since scan — not moved"))
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
            # Re-written after every single move, not batched at the end:
            # a run killed here still leaves an accurate record of
            # everything moved up to this exact point.
            _write_manifest(manifest_path, manifest)

    return manifest, failures, dest_dir
