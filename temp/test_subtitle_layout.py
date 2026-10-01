"""Unit tests for the subtitle display layout (sentence-aligned, timed).

Run from the repo root:  python3 -m unittest temp.test_subtitle_layout -v

The layout is pure (ytdl/subtitle_layout.py, no Qt, no filesystem), and it is
exercised against six real YouTube ASR captures of 12 minutes each, sliced from
the videos in the app's own download history:

    subtitle-capture-DRiCP1sO3ck.srt  Jasmin Kosubek (DE), fast
    subtitle-capture-o5bPneN-Pqg.srt  Jasmin Kosubek (DE), slower
    subtitle-capture-nk15CT41MFc.srt  Huberman Lab (EN)
    subtitle-capture-dYPXINFcvmI.srt  Joe Rogan (EN)
    subtitle-capture-8y5Y01NhfOE.srt  Shawn Ryan (EN)
    subtitle-capture-UQ3O04Wtqa4.srt  The Diary of a CEO (EN)

They span 2.1-3.3 words/sec, so the constants are exercised on both sides of
the reading target. The invariants asserted per capture are the ones that make
a subtitle file usable: the word stream survives intact, at most two lines per
cue, no cue overlapping the next, and durations in a sane range.
"""

import glob
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


class TestLayoutInvariants(unittest.TestCase):
    """Every real capture must come out watchable."""

    def test_captures_exist(self):
        self.assertEqual(len(CAPTURES), 6, CAPTURES)

    def test_word_stream_is_preserved(self):
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                cues = read_capture(path)
                before = " ".join(c["text"] for c in cues).split()
                out, _ = L.layout_cues(cues)
                after = " ".join(c["text"].replace("\n", " ") for c in out).split()
                self.assertEqual(before, after)

    def test_at_most_two_lines_per_cue(self):
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                out, _ = L.layout_cues(read_capture(path))
                for cue in out:
                    self.assertLessEqual(len(cue["text"].split("\n")), 2, cue["text"])

    def test_no_cue_overlaps_the_next(self):
        # Two cues on screen at once are stacked by the player, which is the
        # symptom this whole stage exists to avoid.
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                out, _ = L.layout_cues(read_capture(path))
                for a, b in zip(out, out[1:]):
                    self.assertLessEqual(a["end"], b["start"] + 1e-6,
                                         f"{a['text']!r} / {b['text']!r}")

    def test_durations_stay_in_range(self):
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                out, _ = L.layout_cues(read_capture(path))
                for cue in out:
                    self.assertGreater(cue["end"], cue["start"], cue["text"])
                    self.assertLessEqual(cue["end"] - cue["start"],
                                         L.LAYOUT_MAX_DUR + 1e-6, cue["text"])

class TestPacking(unittest.TestCase):
    """The rules, on inputs small enough to reason about by hand."""

    def _cues(self, *windows):
        return [{"start": s, "end": e, "text": t} for s, e, t in windows]

    def test_one_sentence_becomes_one_cue(self):
        out, _ = L.layout_cues(self._cues((0.0, 4.0, "Hallo Welt. Guten Tag.")))
        self.assertEqual(len(out), 1)
        self.assertIn("Hallo Welt. Guten Tag.", out[0]["text"].replace("\n", " "))

    def test_short_sentences_merge_into_one_cue(self):
        # Two sentences a fraction of a second apart cannot be shown one after
        # the other; they must be one cue, not two stacked on screen.
        out, _ = L.layout_cues(self._cues((0.0, 0.9, "Right."), (1.0, 1.8, "Exactly.")))
        self.assertEqual(len(out), 1, [c["text"] for c in out])

    def test_long_sentence_is_split(self):
        words = " ".join(f"word{i}" for i in range(40)) + "."
        out, _ = L.layout_cues(self._cues((0.0, 20.0, words)))
        self.assertGreater(len(out), 1)
        for cue in out:
            self.assertLessEqual(len(cue["text"].split()),
                                 L.LAYOUT_MERGE_CEILING_WORDS)

    def test_start_comes_from_the_first_word_time(self):
        # A cue must not appear before the word it shows is spoken.
        out, _ = L.layout_cues(self._cues((0.0, 2.0, "One two three four.")))
        self.assertGreaterEqual(out[0]["start"], 0.0)
        self.assertLess(out[0]["start"], 2.0)

    def test_empty_input_is_handled(self):
        self.assertEqual(L.layout_cues([])[0], [])
        self.assertEqual(L.layout_cues([{"start": 0, "end": 0, "text": ""}])[0], [])

    def test_effective_rate_is_clamped_to_the_band(self):
        # Never slower than the readability target, never faster than the band.
        self.assertEqual(L.effective_wps(1.0), L.LAYOUT_TARGET_WPS)
        self.assertEqual(L.effective_wps(99.0), L.LAYOUT_MAX_WPS)
        self.assertEqual(L.effective_wps(3.0), 3.0)
        self.assertEqual(L.effective_wps(0.0), L.LAYOUT_TARGET_WPS)

    def test_abbreviations_do_not_end_a_sentence(self):
        words = L.word_stream(self._cues((0.0, 3.0, "Dr. Smith kam heute.")))
        self.assertEqual(len(L.split_sentences(words)), 1)

    def test_overlapping_source_windows_stay_in_order(self):
        # YouTube's ASR windows overlap in time; interpolated word times must
        # still come out non-decreasing or the output cues are out of order.
        out, _ = L.layout_cues(self._cues(
            (0.0, 3.0, "erster Satz hier."),
            (1.0, 4.0, "zweiter Satz da."),
        ))
        for a, b in zip(out, out[1:]):
            self.assertLessEqual(a["start"], b["start"])


if __name__ == "__main__":
    unittest.main()
    def test_word_ceiling_is_respected(self):
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                out, _ = L.layout_cues(read_capture(path))
                for cue in out:
                    self.assertLessEqual(len(cue["text"].split()),
                                         L.LAYOUT_MERGE_CEILING_WORDS, cue["text"])

    def test_boundaries_are_sentence_aligned(self):
        # The point of the stage: most cues end at a sentence end, where the
        # old pipeline inherited the ASR's time-based cuts and did not.
        ends = re.compile(r"[.!?][\"'’”)\]]*$")
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                out, _ = L.layout_cues(read_capture(path))
                texts = [c["text"].replace("\n", " ") for c in out]
                aligned = sum(1 for t in texts[:-1] if ends.search(t))
                self.assertGreater(aligned / max(len(texts) - 1, 1), 0.5)