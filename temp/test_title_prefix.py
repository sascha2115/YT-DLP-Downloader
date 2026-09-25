"""
Unit tests for build_title_with_prefix() — no Qt, no network, no GUI state.

Covers the malformed title prefixes produced when a video has no usable
upload date: the old unconditional `short_date + " - " + title` yielded
" - Title" (absent date) and "not-a-date - Title" (unparseable date), and
that string became the on-disk folder/file name.

Run from the repo root:
    python3 -m unittest temp.test_title_prefix -v
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ytdl.info_fetch import apply_episode_rules, build_title_with_prefix


class TestBuildTitleWithPrefix(unittest.TestCase):
    """The prefix is omitted unless a usable code is available."""

    def test_episode_code_is_used_as_prefix(self):
        self.assertEqual(
            build_title_with_prefix("S01E2481", "Duncan Trussell"),
            "S01E2481 - Duncan Trussell",
        )

    def test_date_code_is_used_as_prefix(self):
        self.assertEqual(
            build_title_with_prefix("S26E0110", "Some Video Title"),
            "S26E0110 - Some Video Title",
        )

    def test_empty_prefix_omits_separator(self):
        """Regression: used to produce " - Some Video Title"."""
        self.assertEqual(
            build_title_with_prefix("", "Some Video Title"),
            "Some Video Title",
        )

    def test_none_prefix_omits_separator(self):
        self.assertEqual(
            build_title_with_prefix(None, "Some Video Title"),
            "Some Video Title",
        )

    def test_whitespace_prefix_omits_separator(self):
        self.assertEqual(
            build_title_with_prefix("   ", "Some Video Title"),
            "Some Video Title",
        )

    def test_result_is_sanitized(self):
        # Illegal characters are replaced/dropped even when a prefix is present.
        self.assertEqual(
            build_title_with_prefix("S26E0110", 'A/B: "quoted" <tag>'),
            "S26E0110 - A-B - quoted tag",
        )

    def test_result_never_starts_with_dash(self):
        """The on-disk name must not begin with an orphaned separator."""
        for prefix in ("", None, "  "):
            with self.subTest(prefix=prefix):
                self.assertFalse(build_title_with_prefix(prefix, "Title").startswith("-"))


class TestTitlePrefixWithEpisodeRules(unittest.TestCase):
    """An episode rule must win over a date-derived prefix, or supply one."""

    def test_episode_rule_wins_over_date_code(self):
        # apply_episode_rules yields the code; the caller passes it as prefix.
        code, clean = apply_episode_rules(
            "PowerfulJRE", "Joe Rogan Experience #2481 - Duncan Trussell"
        )
        self.assertEqual(code, "S01E2481")
        self.assertEqual(
            build_title_with_prefix(code, clean),
            "S01E2481 - Joe Rogan Experience - Duncan Trussell",
        )

    def test_episode_rule_supplies_prefix_without_any_date(self):
        """No upload date at all, but a channel rule still yields a code."""
        code, clean = apply_episode_rules(
            "PowerfulJRE", "Joe Rogan Experience #2481 - Duncan Trussell"
        )
        self.assertEqual(
            build_title_with_prefix("", clean),
            "Joe Rogan Experience - Duncan Trussell",
        )
        # ...and the rule-supplied code is the prefix that matters:
        self.assertEqual(
            build_title_with_prefix(code, clean),
            "S01E2481 - Joe Rogan Experience - Duncan Trussell",
        )

    def test_no_rule_match_keeps_title_and_needs_no_prefix(self):
        code, clean = apply_episode_rules("Some Channel", "Plain Video Title")
        self.assertEqual(code, "")
        self.assertEqual(build_title_with_prefix(code, clean), "Plain Video Title")


class TestUploadDateParsing(unittest.TestCase):
    """short_date must stay empty unless strptime succeeded.

    Mirrors the parsing block in get_video_info(); a malformed date must not
    reach the title, so no raw value is ever used as a prefix.
    """
    # fmt: off
    CASES = [
        # (upload_date, expected_prefix, expect_prefix)
        (None,        "",    False),
        ("",          "",    False),
        ("20260110",  "S26E0110", True),
        ("not-a-date", "",   False),
        ("2026-01-10", "",   False),
        ("20261332",  "",    False),
        ("20261340",  "",    False),
        ("19700101",  "S70E0101", True),
    ]
    # fmt: on

    def _parse(self, upload_date):
        from datetime import datetime

        short_date = ""
        if upload_date:
            try:
                short_date = datetime.strptime(
                    upload_date, "%Y%m%d"
                ).strftime("S%yE%m%d")
            except ValueError:
                short_date = ""
        return short_date

    def test_prefix_only_when_date_parses(self):
        for upload_date, expected, expect_prefix in self.CASES:
            with self.subTest(upload_date=upload_date):
                prefix = self._parse(upload_date)
                self.assertEqual(prefix, expected)
                title = build_title_with_prefix(prefix, "Some Video Title")
                if expect_prefix:
                    self.assertEqual(title, f"{expected} - Some Video Title")
                else:
                    self.assertEqual(title, "Some Video Title")


if __name__ == "__main__":
    unittest.main(verbosity=2)
