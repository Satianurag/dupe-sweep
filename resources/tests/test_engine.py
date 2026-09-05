"""dupe-sweep engine tests.

Scoped deliberately: every case here pins behaviour that this play would be
actively dangerous to get wrong, because it decides which of your files gets
moved. Anything that is merely "nice if correct" is left to the demo fixtures.

    python3 -m unittest discover -s resources/tests
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import engine  # noqa: E402


def write(path: str, content: bytes, mtime: float | None = None) -> str:
    """Create a file and return its REAL path.

    The engine reports realpath-resolved paths, and on macOS a temp dir under
    /var is really /private/var -- so a fixture that kept the unresolved path
    would fail a comparison for a reason that has nothing to do with the code.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return os.path.realpath(path)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dupe-sweep-test-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def scan(self, keep_rule="oldest", priority_paths=None, exclude_paths=None,
             min_size=1, include_hidden=False):
        records, skips, roots, capped, excluded = engine.discover_files(
            [self.root], min_size, 10_000, include_hidden, exclude_paths or [])
        dups, linked, effective = engine.build_duplicate_sets(
            records, roots, skips, keep_rule=keep_rule,
            priority_paths=priority_paths or [])
        self.effective_rule = effective
        return dups, linked


class KeeperChoice(Base):
    """Which copy survives is the whole trust question for this play."""

    def setUp(self):
        super().setUp()
        body = b"x" * 5000
        self.old = write(os.path.join(self.root, "a", "old.bin"), body, mtime=1_000_000)
        self.mid = write(os.path.join(self.root, "b", "mid.bin"), body, mtime=2_000_000)
        self.new = write(os.path.join(self.root, "c", "new.bin"), body, mtime=3_000_000)

    def test_oldest_is_the_default(self):
        dups, _ = self.scan()
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0].keep, self.old)

    def test_newest(self):
        dups, _ = self.scan(keep_rule="newest")
        self.assertEqual(dups[0].keep, self.new)

    def test_priority_folder_wins(self):
        dups, _ = self.scan(keep_rule="priority",
                            priority_paths=[os.path.join(self.root, "c")])
        self.assertEqual(dups[0].keep, self.new)

    def test_priority_with_no_matching_file_still_picks_oldest(self):
        """priority_paths were given but nothing in this set lives under one.
        The rule was genuinely applied -- nothing ranked -- so it stays
        "priority", and the set falls through to the ordinary oldest
        tie-break rather than picking arbitrarily."""
        dups, _ = self.scan(keep_rule="priority",
                            priority_paths=[os.path.join(self.root, "nowhere")])
        self.assertEqual(dups[0].keep, self.old)
        self.assertEqual(self.effective_rule, "priority")

    def test_priority_with_no_paths_at_all_is_reported_as_a_degrade(self):
        """Asking for priority while naming no priority paths contradicts
        itself. It degrades to oldest, and the effective rule says so, so a
        caller can surface it instead of the card implying priority ran."""
        dups, _ = self.scan(keep_rule="priority", priority_paths=[])
        self.assertEqual(dups[0].keep, self.old)
        self.assertEqual(self.effective_rule, "oldest")

    def test_priority_that_matches_reports_priority(self):
        self.scan(keep_rule="priority", priority_paths=[os.path.join(self.root, "c")])
        self.assertEqual(self.effective_rule, "priority")

    def test_same_input_same_keeper(self):
        """Deterministic, not a coin flip -- the play promises this."""
        first = self.scan()[0][0].keep
        for _ in range(3):
            self.assertEqual(self.scan()[0][0].keep, first)


class NeverReclaimsWhatItCannot(Base):
    def test_hardlinks_are_reported_separately_and_never_moved(self):
        """Two names for one inode free zero bytes if you delete one, so they
        are a LinkedSet, never a duplicate set."""
        a = write(os.path.join(self.root, "linked_a.bin"), b"y" * 5000)
        b = os.path.join(self.root, "linked_b.bin")
        os.link(a, b)
        b = os.path.realpath(b)
        dups, linked = self.scan()
        self.assertEqual(dups, [])
        self.assertEqual(len(linked), 1)
        self.assertEqual(sorted(linked[0].paths), sorted([a, b]))

    def test_zero_byte_files_are_never_duplicates(self):
        """Every empty file hashes identically; quarantining one reclaims
        nothing, so they are excluded outright rather than reported."""
        write(os.path.join(self.root, "e1"), b"")
        write(os.path.join(self.root, "e2"), b"")
        dups, linked = self.scan(min_size=0)
        self.assertEqual((dups, linked), ([], []))

    def test_same_size_different_content_is_not_a_duplicate(self):
        write(os.path.join(self.root, "p.bin"), b"a" * 5000)
        write(os.path.join(self.root, "q.bin"), b"b" * 5000)
        dups, _ = self.scan()
        self.assertEqual(dups, [])


class WalkBoundaries(Base):
    def test_opaque_directories_are_never_descended(self):
        """Deduplicating a file inside a .app or a .git can break the thing
        that owns it. These are leaves, not folders to walk."""
        for opaque in (".git", "node_modules", "Thing.app"):
            write(os.path.join(self.root, opaque, "dup.bin"), b"z" * 5000)
        write(os.path.join(self.root, "visible.bin"), b"z" * 5000)
        records, _, _, _, _ = engine.discover_files([self.root], 1, 10_000, False, [])
        self.assertEqual([os.path.basename(r.path) for r in records], ["visible.bin"])

    def test_exclude_paths_prunes_during_the_walk(self):
        write(os.path.join(self.root, "keep", "f.bin"), b"w" * 5000)
        write(os.path.join(self.root, "skip", "f.bin"), b"w" * 5000)
        dups, _ = self.scan(exclude_paths=[os.path.join(self.root, "skip")])
        self.assertEqual(dups, [])

    def test_hidden_files_are_off_by_default(self):
        write(os.path.join(self.root, ".hidden.bin"), b"h" * 5000)
        write(os.path.join(self.root, "shown.bin"), b"h" * 5000)
        self.assertEqual(self.scan()[0], [])
        self.assertEqual(len(self.scan(include_hidden=True)[0]), 1)


class Quarantine(Base):
    def test_apply_moves_duplicates_and_leaves_the_keeper(self):
        body = b"q" * 5000
        keep = write(os.path.join(self.root, "a", "keep.bin"), body, mtime=1_000_000)
        dupe = write(os.path.join(self.root, "b", "dupe.bin"), body, mtime=2_000_000)
        dups, _ = self.scan()
        qroot = os.path.join(self.root, "_quarantine")
        manifest, failures, dest = engine.apply_quarantine(dups, qroot)
        self.assertEqual(failures, [])
        self.assertTrue(os.path.exists(keep), "the keeper must never move")
        self.assertFalse(os.path.exists(dupe), "the duplicate should have moved")
        self.assertEqual(len(manifest), 1)

    def test_undo_script_travels_with_the_quarantine(self):
        """Restoring must not need Rote installed -- only python3."""
        body = b"u" * 5000
        write(os.path.join(self.root, "a", "keep.bin"), body, mtime=1_000_000)
        write(os.path.join(self.root, "b", "dupe.bin"), body, mtime=2_000_000)
        dups, _ = self.scan()
        _, _, dest = engine.apply_quarantine(dups, os.path.join(self.root, "_q"))
        self.assertTrue(os.path.exists(os.path.join(dest, "undo.py")))
        self.assertTrue(os.path.exists(os.path.join(dest, "manifest.json")))


if __name__ == "__main__":
    unittest.main()
