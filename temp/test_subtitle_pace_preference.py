"""Unit tests for the subtitle pace preference.

Run from the repo root:  python3 -m unittest temp.test_subtitle_pace_preference -v

Covers the pure mapping `subtitle_layout_targets()` (preferences.json
key -> layout_cues targets) and the effect of the preference on a real
ASR capture: more, shorter cues, word stream intact. No Qt needed.
"""

import glob
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ytdl import preferences as P  # noqa: E402
from ytdl import subtitle_layout as L  # noqa: E402
from ytdl.utils import parse_srt_time  # noqa: E402

CAPTURES = sorted(
    glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "subtitle-capture-*.srt"))
)


def read_capture(path):
    """Parse a capture the way resync_subtitles parses a downloaded file."""
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read().replace("\r\n", "\n")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        if len(lines) < 3:
            continue
        match = re.match(r"([\d:,]+)\s+-->\s+([\d:,]+)", lines[1])
        if not match:
            continue
        raw = re.sub(r"\[[^\[\]]*?\]", " ",
                     re.sub(r"^>>\s*", "", "\n".join(lines[2:]), flags=re.M))
        text = " ".join(raw.split())
        if text:
            cues.append({"start": parse_srt_time(match.group(1)),
                         "end": parse_srt_time(match.group(2)), "text": text})
    return cues


class TestSubtitleLayoutTargets(unittest.TestCase):
    """The preference maps onto layout targets, or stays out of the way."""

    def test_absent_key_returns_empty(self):
        self.assertEqual(P.subtitle_layout_targets({}), {})
        self.assertEqual(
            P.subtitle_layout_targets({"channel_name_map": {}}), {})

    def test_valid_key_derives_ceiling(self):
        self.assertEqual(
            P.subtitle_layout_targets({"subtitle_words_per_cue": 8}),
            {"target_words": 8, "ceiling_words": 10},
        )

    def test_default_matches_layout_constants(self):
        targets = P.subtitle_layout_targets(
            {"subtitle_words_per_cue": P.SUBTITLE_WORDS_DEFAULT})
        self.assertEqual(targets["target_words"], L.LAYOUT_TARGET_WORDS)
        self.assertEqual(targets["ceiling_words"], L.LAYOUT_CEILING_WORDS)

    def test_non_integer_is_ignored(self):
        for bad in ("8", 8.5, True, [8], {"words": 8}):
            self.assertEqual(
                P.subtitle_layout_targets({"subtitle_words_per_cue": bad}), {})

    def test_out_of_range_is_clamped(self):
        self.assertEqual(
            P.subtitle_layout_targets({"subtitle_words_per_cue": 99}),
            {"target_words": P.SUBTITLE_WORDS_MAX,
             "ceiling_words": P.SUBTITLE_WORDS_MAX + 2},
        )
        self.assertEqual(
            P.subtitle_layout_targets({"subtitle_words_per_cue": 1}),
            {"target_words": P.SUBTITLE_WORDS_MIN,
             "ceiling_words": P.SUBTITLE_WORDS_MIN + 2},
        )

    def test_live_preference_is_read_at_call_time(self):
        saved_prefs = P.preferences
        saved_map = P.CHANNEL_NAME_MAP
        try:
            P.apply_preferences({"subtitle_words_per_cue": 7})
            self.assertEqual(P.subtitle_layout_targets(),
                             {"target_words": 7, "ceiling_words": 9})
        finally:
            P.apply_preferences(saved_prefs)
            P.CHANNEL_NAME_MAP = saved_map


class TestPreferenceChangesLayout(unittest.TestCase):
    """The preference actually moves the output on a real capture."""

    def test_captures_exist(self):
        self.assertTrue(CAPTURES, "no subtitle-capture-*.srt in temp/")

    def test_lower_preference_more_shorter_cues(self):
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                cues = read_capture(path)
                default, _ = L.layout_cues(cues)
                targets = P.subtitle_layout_targets(
                    {"subtitle_words_per_cue": P.SUBTITLE_WORDS_MIN})
                paced, _ = L.layout_cues(cues, targets=targets)
                self.assertGreater(len(paced), len(default))
                for cue in paced:
                    self.assertLessEqual(
                        len(cue["text"].split()),
                        P.SUBTITLE_WORDS_MIN + 2)

    def test_word_stream_survives_the_preference(self):
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                cues = read_capture(path)
                before = " ".join(c["text"] for c in cues).split()
                targets = P.subtitle_layout_targets(
                    {"subtitle_words_per_cue": P.SUBTITLE_WORDS_MIN})
                out, _ = L.layout_cues(cues, targets=targets)
                after = " ".join(c["text"].replace("\n", " ")
                                 for c in out).split()
                self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
