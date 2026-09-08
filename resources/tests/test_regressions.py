"""Regression tests for the defects fixed in 0.4.0.

Each test names the symptom it prevents; each is a defect this play shipped.

    python3 -m unittest discover -s resources/tests
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS = os.path.join(ROOT, "resources", "scripts")
sys.path.insert(0, SCRIPTS)

import engine  # noqa: E402
import scan as scan_mod  # noqa: E402


class UndoWorksAsTheCardPrintsIt(unittest.TestCase):
    """The safety-critical one. Quarantining is this play's entire promise --
    "reversible, never deleted" -- and the card prints the absolute form,
    `python3 <quarantine>/undo.py`. undo.py defaulted its manifest to
    os.getcwd()/manifest.json, so that exact command found nothing, printed
    "no manifest found at /Users/you/manifest.json", and restored NOTHING
    while the files sat in quarantine."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.original = os.path.join(self.tmp, "original", "report.pdf")
        os.makedirs(os.path.dirname(self.original))
        with open(self.original, "w", encoding="utf-8") as handle:
            handle.write("x" * 5000)
        self.quarantine = os.path.join(self.tmp, "quarantine", "20260908T000000Z")
        os.makedirs(self.quarantine)
        quarantined = os.path.join(self.quarantine, "report.pdf")
        shutil.move(self.original, quarantined)
        with open(os.path.join(self.quarantine, "manifest.json"), "w", encoding="utf-8") as handle:
            json.dump([{"original_path": self.original, "quarantined_path": quarantined}], handle)
        shutil.copy(os.path.join(SCRIPTS, "undo.py"), os.path.join(self.quarantine, "undo.py"))

    def run_undo(self, cwd, *args):
        return subprocess.run(
            [sys.executable, os.path.join(self.quarantine, "undo.py")] + list(args),
            capture_output=True, text=True, cwd=cwd)

    def test_the_absolute_form_the_card_prints_restores_the_file(self):
        elsewhere = os.path.join(self.tmp, "somewhere-else")
        os.makedirs(elsewhere)
        proc = self.run_undo(elsewhere)
        self.assertIn("restored 1 file(s)", proc.stdout, proc.stdout + proc.stderr)
        self.assertTrue(os.path.isfile(self.original))

    def test_running_from_inside_the_quarantine_folder_still_works(self):
        proc = self.run_undo(self.quarantine)
        self.assertIn("restored 1 file(s)", proc.stdout)
        self.assertTrue(os.path.isfile(self.original))

    def test_an_explicit_manifest_path_still_wins(self):
        elsewhere = os.path.join(self.tmp, "elsewhere2")
        os.makedirs(elsewhere)
        proc = self.run_undo(elsewhere, os.path.join(self.quarantine, "manifest.json"))
        self.assertIn("restored 1 file(s)", proc.stdout)
        self.assertTrue(os.path.isfile(self.original))

    def test_it_refuses_to_clobber_a_file_back_at_the_original_path(self):
        os.makedirs(os.path.dirname(self.original), exist_ok=True)
        with open(self.original, "w", encoding="utf-8") as handle:
            handle.write("something new")
        proc = self.run_undo(self.quarantine)
        self.assertIn("restored 0 file(s)", proc.stdout)
        self.assertIn("already occupied", proc.stdout)
        with open(self.original, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "something new")


class TheCopyCountIsNotTheLengthOfTheTrimmedPreview(unittest.TestCase):
    """The card renders "x N copies" and derived N from the length of the
    preview list, which every trim shortened -- so a set of 200 identical
    files displayed "x 51 copies" under DUPLICATES_PER_SET_CAP alone. A count
    that shrinks with the preview is not partial, it is wrong."""

    def test_the_true_total_survives_trimming(self):
        with open(os.path.join(ROOT, "main.ts"), encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("duplicates_total", body,
                      "the card must render the pre-trim count, not the preview length")
        self.assertNotIn("${s.duplicates.length + 1} copies", body,
                         "deriving the headline count from the trimmed list is the bug")


class ScanStdoutNeverOverflowsTheRunnersCeiling(unittest.TestCase):
    """PREVIEW_CAP (150 sets) and DUPLICATES_PER_SET_CAP (50 paths) bound the
    preview by COUNT, not by SIZE, and a duplicate path is as long as
    wherever it lives. 150 sets x 50 copies of a real synced-folder path is
    ~947KB, fourteen times the 65536-byte ceiling. Confirmed fatal on a
    folder shaped like an ordinary synced archive -- which is this play's
    DEFAULT target."""

    def payload(self, sets, copies, path_len):
        path = "/Users/someone/Library/CloudStorage/Dropbox/" + ("d" * path_len) + "/file.pdf"
        entry = {"hash": "a" * 64, "size": 123456, "keep": path,
                 "duplicates": [path] * copies, "duplicates_total": copies,
                 "reclaimable_bytes": 123456 * copies}
        return {"ok": True, "files_scanned": 50000,
                "duplicate_sets": [dict(entry) for _ in range(sets)],
                "linked_sets": [], "skips": [], "total_reclaimable_bytes": 1,
                "total_duplicate_files": sets * copies, "total_duplicate_sets": sets,
                "total_linked_sets": 0, "total_skips": 0, "preview_capped": False,
                "state_file": "/x", "roots_resolved": ["~/Downloads"]}

    def emit(self, payload):
        import io
        import contextlib
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            scan_mod.emit_within_ceiling(payload)
        return buffer.getvalue()

    def test_the_documented_worst_case_fits(self):
        text = self.emit(self.payload(scan_mod.PREVIEW_CAP, 50, 60))
        self.assertLess(len(text.encode("utf-8")), 65536)
        json.loads(text)

    def test_an_ordinary_synced_archive_fits(self):
        text = self.emit(self.payload(200, 12, 60))
        self.assertLess(len(text.encode("utf-8")), 65536)
        out = json.loads(text)
        self.assertTrue(out["preview_capped"])

    def test_the_totals_never_shrink(self):
        payload = self.payload(200, 12, 60)
        expected_files = payload["total_duplicate_files"]
        expected_sets = payload["total_duplicate_sets"]
        out = json.loads(self.emit(payload))
        self.assertEqual(out["total_duplicate_files"], expected_files)
        self.assertEqual(out["total_duplicate_sets"], expected_sets)

    def test_a_small_scan_is_emitted_untouched(self):
        payload = self.payload(2, 2, 10)
        out = json.loads(self.emit(payload))
        self.assertFalse(out["preview_capped"])
        self.assertEqual(len(out["duplicate_sets"]), 2)
        self.assertEqual(len(out["duplicate_sets"][0]["duplicates"]), 2)


class TheContractAgreesWithItself(unittest.TestCase):
    def main_ts(self):
        with open(os.path.join(ROOT, "main.ts"), encoding="utf-8") as handle:
            return handle.read()

    def test_frontmatter_version_matches_engine(self):
        match = re.search(r"^ \*   version: (\S+)$", self.main_ts(), re.MULTILINE)
        self.assertEqual(match.group(1), engine.PLAY_VERSION)

    def test_presentation_version_matches_engine(self):
        match = re.search(r'const PLAY_VERSION = "([^"]+)"', self.main_ts())
        self.assertEqual(match.group(1), engine.PLAY_VERSION)

    def test_no_hardcoded_version_literal_remains(self):
        self.assertNotRegex(self.main_ts(), r'play_version: "\d')


if __name__ == "__main__":
    unittest.main()
