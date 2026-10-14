"""Unit tests for the output panel's version header (no Qt).

The header names the app version and the commit the code came from, so a bug
report can be tied to an exact checkout. The commit is shelled out to git
once and cached; every failure mode must collapse to "" so the header
degrades to the version alone rather than the app failing to start.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ytdl import APP_VERSION  # noqa: E402
from ytdl import utils  # noqa: E402


class _Result:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


class TestGitCommitShort(unittest.TestCase):
    def setUp(self):
        # Each case must re-resolve; the real cache may hold a value.
        self._cache = utils._GIT_COMMIT_CACHE
        utils._GIT_COMMIT_CACHE = None

    def tearDown(self):
        utils._GIT_COMMIT_CACHE = self._cache

    def test_real_checkout_returns_a_hash(self):
        # No mocks: this repo has a .git, so the lookup must really work.
        commit = utils.get_git_commit_short()
        self.assertRegex(commit, r"[0-9a-f]{7,40}")

    def test_hash_is_traced_back_to_head(self):
        # The reported commit must be the one HEAD points at, not a stale
        # cached value from an earlier run of the app.
        import subprocess

        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(utils.get_git_commit_short(), head)

    def test_missing_git_dir_reports_unknown(self):
        # A PyInstaller bundle ships no repository beside the package.
        with mock.patch.object(utils.os.path, "isdir", return_value=False):
            with mock.patch.object(utils.subprocess, "run") as run:
                self.assertEqual(utils.get_git_commit_short(), "")
        run.assert_not_called()

    def test_missing_git_binary_reports_unknown(self):
        with mock.patch.object(
            utils.subprocess, "run", side_effect=FileNotFoundError("git")
        ):
            self.assertEqual(utils.get_git_commit_short(), "")

    def test_timeout_reports_unknown(self):
        with mock.patch.object(
            utils.subprocess,
            "run",
            side_effect=utils.subprocess.TimeoutExpired("git", 5),
        ):
            self.assertEqual(utils.get_git_commit_short(), "")

    def test_nonzero_exit_reports_unknown(self):
        with mock.patch.object(
            utils.subprocess, "run", return_value=_Result("", 128)
        ):
            self.assertEqual(utils.get_git_commit_short(), "")

    def test_non_hash_output_is_rejected(self):
        # A wrapper or alias could print something that is not a hash.
        with mock.patch.object(
            utils.subprocess, "run", return_value=_Result("fatal: not a repo\n", 0)
        ):
            self.assertEqual(utils.get_git_commit_short(), "")

    def test_lookup_runs_once(self):
        # Cached: shelling out per call would stall the UI for nothing.
        with mock.patch.object(
            utils.subprocess, "run", return_value=_Result("babb7fb\n", 0)
        ) as run:
            self.assertEqual(utils.get_git_commit_short(), "babb7fb")
            self.assertEqual(utils.get_git_commit_short(), "babb7fb")
        self.assertEqual(run.call_count, 1)

    def test_failure_is_cached_too(self):
        # A broken git must not be re-shelled on every call either.
        with mock.patch.object(
            utils.subprocess, "run", side_effect=FileNotFoundError("git")
        ) as run:
            self.assertEqual(utils.get_git_commit_short(), "")
            self.assertEqual(utils.get_git_commit_short(), "")
        self.assertEqual(run.call_count, 1)


class TestVersionHeader(unittest.TestCase):
    def test_commit_is_bracketed_after_the_version(self):
        with mock.patch.object(utils, "get_git_commit_short", return_value="babb7fb"):
            header = utils.version_header()
        self.assertEqual(header, f"YT-DLP Downloader {APP_VERSION} [babb7fb]")

    def test_brackets_are_omitted_when_unknown(self):
        with mock.patch.object(utils, "get_git_commit_short", return_value=""):
            header = utils.version_header()
        self.assertEqual(header, f"YT-DLP Downloader {APP_VERSION}")
        self.assertNotIn("[", header)

    def test_header_leads_with_the_version(self):
        # The version stays first: it is the user-facing identity.
        self.assertTrue(utils.version_header().startswith(f"YT-DLP Downloader {APP_VERSION}"))


if __name__ == "__main__":
    unittest.main()
