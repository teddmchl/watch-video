#!/usr/bin/env python3
"""Unit tests for the cache size cap (no video tools needed).

  python3 tests/test_cache.py
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "watch-video" / "scripts"))

import cache  # noqa: E402

MB = 1024 * 1024


def work_dir(root: Path, name: str, mb: float, age_s: float, marker: str = "state.json") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / marker).write_text("{}")
    (d / "blob.bin").write_bytes(b"\0" * int(mb * MB))
    t = time.time() - age_s
    for f in [*d.rglob("*"), d]:
        os.utime(f, (t, t))
    return d


class CapFromEnv(unittest.TestCase):
    def test_default(self):
        self.assertEqual(cache.cap_bytes({}), cache.DEFAULT_CAP_MB * MB)

    def test_env_override(self):
        self.assertEqual(cache.cap_bytes({"WATCH_VIDEO_CACHE_MB": "500"}), 500 * MB)

    def test_zero_means_no_cap(self):
        self.assertIsNone(cache.cap_bytes({"WATCH_VIDEO_CACHE_MB": "0"}))

    def test_bad_value_falls_back_to_default(self):
        self.assertEqual(cache.cap_bytes({"WATCH_VIDEO_CACHE_MB": "lots"}), cache.DEFAULT_CAP_MB * MB)


class Prune(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="wv-cache-test-"))

    def test_under_cap_removes_nothing(self):
        work_dir(self.root, "a", 1, 3600)
        self.assertEqual(cache.prune(self.root, 5 * MB), [])

    def test_removes_least_recently_used_first(self):
        old = work_dir(self.root, "old", 3, 7200)
        mid = work_dir(self.root, "mid", 3, 3600)
        new = work_dir(self.root, "new", 3, 1800)
        removed = cache.prune(self.root, 7 * MB)
        self.assertEqual([p for p, _ in removed], [old])
        self.assertFalse(old.exists())
        self.assertTrue(mid.exists() and new.exists())

    def test_never_removes_kept_dir(self):
        keep = work_dir(self.root, "keep", 6, 7200)
        other = work_dir(self.root, "other", 3, 3600)
        removed = cache.prune(self.root, 5 * MB, keep={keep})
        self.assertEqual([p for p, _ in removed], [other])
        self.assertTrue(keep.exists())

    def test_never_removes_recently_used_dir(self):
        busy = work_dir(self.root, "busy", 6, 30)       # in use a moment ago (a scan or live run)
        removed = cache.prune(self.root, 1 * MB)
        self.assertEqual(removed, [])
        self.assertTrue(busy.exists())

    def test_ignores_folders_that_are_not_work_dirs(self):
        stranger = self.root / "not-ours"
        stranger.mkdir()
        (stranger / "big.bin").write_bytes(b"\0" * 4 * MB)
        os.utime(stranger, (0, 0))
        self.assertEqual(cache.prune(self.root, 1 * MB), [])
        self.assertTrue(stranger.exists())

    def test_live_dirs_count_as_work_dirs(self):
        live = work_dir(self.root, "live-20261116-101010", 3, 7200, marker="LIVE.md")
        removed = cache.prune(self.root, 1 * MB)
        self.assertEqual([p for p, _ in removed], [live])

    def test_no_cap_removes_nothing(self):
        work_dir(self.root, "a", 3, 7200)
        self.assertEqual(cache.prune(self.root, None), [])

    def test_missing_root_is_fine(self):
        self.assertEqual(cache.prune(self.root / "nope", 1 * MB), [])


class Touch(unittest.TestCase):
    def test_touch_marks_dir_recently_used(self):
        root = Path(tempfile.mkdtemp(prefix="wv-cache-test-"))
        d = work_dir(root, "a", 1, 7200)
        cache.touch(d)
        self.assertLess(time.time() - cache.last_used(d), 5)


if __name__ == "__main__":
    unittest.main()
