"""
Unit tests for apply_episode_rules() — no Qt, no network, no GUI state.

Run from the repo root:
    python3 -m unittest temp.test_episode_rules -v
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ytdl.info_fetch import apply_episode_rules


class TestPowerfulJRE(unittest.TestCase):
    """Joe Rogan Experience — requires explicit '#' prefix."""

    def test_standard(self):
        code, title = apply_episode_rules(
            "PowerfulJRE",
            "Joe Rogan Experience #2481 - Duncan Trussell",
        )
        self.assertEqual(code, "S01E2481")
        self.assertEqual(title, "Joe Rogan Experience - Duncan Trussell")

    def test_no_space_after_hash(self):
        # "#2467-" variant (hash directly followed by dash)
        code, title = apply_episode_rules(
            "PowerfulJRE",
            "Joe Rogan Experience #2467- Michael Pollan",
        )
        # regex requires "(?: -| |$)" after digits, so this should NOT match
        self.assertEqual(code, "")

    def test_fight_companion_false_positive(self):
        """Bare year must NOT be treated as an episode number."""
        code, title = apply_episode_rules(
            "PowerfulJRE",
            "Fight Companion - June 2024",
        )
        self.assertEqual(code, "")
        self.assertEqual(title, "Fight Companion - June 2024")

    def test_fight_companion_with_hash(self):
        """A fight companion that actually has a '#' should still match."""
        code, title = apply_episode_rules(
            "PowerfulJRE",
            "Fight Companion #12 - July 2024",
        )
        self.assertEqual(code, "S01E0012")
        self.assertNotIn("#12", title)

    def test_no_episode_number(self):
        code, title = apply_episode_rules(
            "PowerfulJRE",
            "Joe Rogan & Elon Musk",
        )
        self.assertEqual(code, "")
        self.assertEqual(title, "Joe Rogan & Elon Musk")

    def test_padding(self):
        code, _ = apply_episode_rules("PowerfulJRE", "JRE #42 - Short Number")
        self.assertEqual(code, "S01E0042")


class TestShawnRyanShow(unittest.TestCase):

    def test_hash_prefix(self):
        code, title = apply_episode_rules(
            "Shawn Ryan Show",
            "Why I Left the CIA | SRS #285",
        )
        self.assertEqual(code, "S01E0285")
        self.assertNotIn("SRS", title)
        self.assertNotIn("#285", title)

    def test_no_hash(self):
        code, title = apply_episode_rules(
            "Shawn Ryan Show",
            "Untold Navy SEAL Story | SRS 301",
        )
        self.assertEqual(code, "S01E0301")

    def test_pipe_stripped(self):
        _, title = apply_episode_rules(
            "Shawn Ryan Show",
            "My Title | SRS #100",
        )
        self.assertNotIn("|", title)

    def test_no_episode(self):
        code, title = apply_episode_rules("Shawn Ryan Show", "Some Interview")
        self.assertEqual(code, "")
        self.assertEqual(title, "Some Interview")


class TestLexFridman(unittest.TestCase):

    def test_standard(self):
        code, title = apply_episode_rules(
            "Lex Fridman",
            "Elon Musk: Neuralink | Lex Fridman Podcast #400",
        )
        self.assertEqual(code, "S01E0400")
        self.assertNotIn("Lex Fridman Podcast", title)
        self.assertNotIn("#400", title)

    def test_no_hash(self):
        code, _ = apply_episode_rules(
            "Lex Fridman",
            "Sam Altman: OpenAI | Lex Fridman Podcast 491",
        )
        self.assertEqual(code, "S01E0491")

    def test_pipe_stripped(self):
        _, title = apply_episode_rules(
            "Lex Fridman",
            "My Guest | Lex Fridman Podcast #100",
        )
        self.assertNotIn("|", title)

    def test_no_episode(self):
        code, title = apply_episode_rules("Lex Fridman", "Short Clip")
        self.assertEqual(code, "")
        self.assertEqual(title, "Short Clip")


class TestPBDPodcast(unittest.TestCase):

    def test_short_form(self):
        code, title = apply_episode_rules(
            "PBD Podcast",
            "Is AI Taking Over? | PBD #754",
        )
        self.assertEqual(code, "S01E0754")
        self.assertNotIn("PBD", title)

    def test_long_form(self):
        code, title = apply_episode_rules(
            "PBD Podcast",
            "Big Interview | PBD Podcast #800",
        )
        self.assertEqual(code, "S01E0800")
        self.assertNotIn("PBD Podcast", title)

    def test_no_hash(self):
        code, _ = apply_episode_rules(
            "PBD Podcast",
            "Some Topic | PBD 600",
        )
        self.assertEqual(code, "S01E0600")

    def test_no_episode(self):
        code, title = apply_episode_rules("PBD Podcast", "Clip Without Number")
        self.assertEqual(code, "")
        self.assertEqual(title, "Clip Without Number")


class TestUnknownChannel(unittest.TestCase):

    def test_unknown_channel_untouched(self):
        code, title = apply_episode_rules(
            "Some Random Channel",
            "Episode #42 - Great Content",
        )
        self.assertEqual(code, "")
        self.assertEqual(title, "Episode #42 - Great Content")


class TestFixtureInfo2(unittest.TestCase):
    """Verify against the actual info2.json fixture (PowerfulJRE #2481)."""

    def test_info2_fixture(self):
        import json
        fixture = os.path.join(os.path.dirname(__file__), "info2.json")
        with open(fixture, encoding="utf-8") as f:
            data = json.load(f)
        channel = data.get("channel", "")
        title = data.get("title", "")
        code, clean = apply_episode_rules(channel, title)
        self.assertEqual(code, "S01E2481")
        self.assertNotIn("#2481", clean)
        self.assertIn("Duncan Trussell", clean)


if __name__ == "__main__":
    unittest.main()
