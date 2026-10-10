"""Unit tests for the paired-window (YouTube-look) subtitle layout.

Run from the repo root:  python3 -m unittest temp.test_subtitle_rolling -v

Pure (ytdl/subtitle_layout.py, no Qt, no filesystem), exercised against the
same seven real YouTube ASR captures as test_subtitle_layout.py.

What this stage is for: the ASR file already contains the display. Each window
is a short phrase, every window overlaps the next, and the file has no line
breaks at all - the two lines a viewer sees in the YouTube player are two
consecutive windows. rolling_cues() groups those windows in twos, so each cue is
one ASR window per line, shown whole:

    SET 1   the Peruvian skulls and              <- window 1
            No, before I thought Oh, go ahead.   <- window 2
    SET 2   Sorry.                               <- window 3
            No, go ahead.                        <- window 4

Nothing is repeated: every window appears exactly once, on the line it occupies
in the player.

The invariants asserted here are the ones that make the result faithful AND
watchable: the group starts at its FIRST window's ASR timestamp (never
interpolated, never delayed), no two cues overlap, each window keeps its own line
verbatim - never re-wrapped, however long it is - and no word is ever lost.
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


class TestRollingInvariants(unittest.TestCase):
    """Every real capture must come out faithful to the ASR and watchable."""

    def test_captures_exist(self):
        self.assertEqual(len(CAPTURES), 7, CAPTURES)

    def test_start_is_the_asr_timestamp_verbatim(self):
        # The whole point of the stage: the top line of a cue appears when the
        # ASR says its FIRST window opened - not at an interpolated word time,
        # not delayed for reading speed. This is what makes it match the player.
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                source = read_capture(path)
                out, _ = L.rolling_cues(source)
                for index, cue in enumerate(out):
                    expected = source[index * 2]["start"]
                    self.assertAlmostEqual(cue["start"], expected, places=3)

    def test_two_windows_per_cue(self):
        # Windows are paired, so the cue count is half the window count (the
        # last cue may hold a lone window when the total is odd).
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                source = read_capture(path)
                out, stats = L.rolling_cues(source)
                self.assertEqual(stats["windows"], len(source))
                self.assertEqual(len(out), -(-len(source) // 2))

    def test_no_cue_overlaps_the_next(self):
        # A cue ends where the next set's first window opens, so only one
        # two-line cue is ever on screen. Overlapping cues would be drawn on top
        # of each other by a player - the defect this layout avoids.
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                out, _ = L.rolling_cues(read_capture(path))
                for a, b in zip(out, out[1:]):
                    self.assertLessEqual(a["end"], b["start"] + 1e-6,
                                         f"{a['text']!r} / {b['text']!r}")

    def test_exactly_one_line_per_window(self):
        # A cue carries one line per window - never re-wrapped, never a third
        # line. The ASR's own long windows stay one long line, exactly as the
        # player shows them, so the line count is the window count.
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                source = read_capture(path)
                out, _ = L.rolling_cues(source)
                for index, cue in enumerate(out):
                    windows = source[index * 2:(index * 2) + 2]
                    self.assertEqual(cue["text"].split("\n"),
                                     [w["text"] for w in windows])

    def test_every_cue_has_a_duration(self):
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                out, _ = L.rolling_cues(read_capture(path))
                for cue in out:
                    self.assertGreater(cue["end"], cue["start"], cue["text"])

    def test_each_window_keeps_its_own_line(self):
        # A cue is one window per line, in source order - the pairing is the
        # whole point, so a window must never be merged into its neighbour.
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                source = read_capture(path)
                out, _ = L.rolling_cues(source)
                for index, cue in enumerate(out):
                    expected = source[index * 2:(index * 2) + 2]
                    lines = cue["text"].split("\n")
                    # A wrapped window adds lines; compare the window texts
                    # against the cue text in order.
                    flat = cue["text"].replace("\n", " ")
                    for window in expected:
                        self.assertIn(
                            window["text"].replace(" ", ""),
                            flat.replace(" ", ""),
                            f"cue at {cue['start']} lost words from {window['text']!r}")
                    # Unwrapped case: exactly one line per window.
                    if len(lines) == len(expected):
                        for line, window in zip(lines, expected):
                            self.assertEqual(line, window["text"])

    def test_nothing_is_repeated(self):
        # The whole point of pairing instead of carrying: a cue's lines are its
        # own windows and nothing else. No line is ever echoed from the previous
        # cue, so the concatenated output has exactly the source's words in the
        # source's order - no more, no fewer.
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                source = read_capture(path)
                out, _ = L.rolling_cues(source)
                # Every unwrapped cue's lines are exactly its source windows,
                # in order - which also proves no window appears twice.
                for index, cue in enumerate(out):
                    windows = source[index * 2:(index * 2) + 2]
                    lines = cue["text"].split("\n")
                    if len(lines) == len(windows):
                        self.assertEqual(lines, [w["text"] for w in windows])
                # And the word stream is preserved exactly.
                before = " ".join(c["text"] for c in source).split()
                after = " ".join(
                    c["text"].replace("\n", " ") for c in out).split()
                self.assertEqual(before, after)

    def test_no_word_is_ever_lost(self):
        # The flattened output must reproduce the source word stream exactly -
        # the same losslessness oracle the sentence-aware layout is held to.
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                source = read_capture(path)
                out, _ = L.rolling_cues(source)
                before = " ".join(c["text"] for c in source).split()
                after = " ".join(
                    c["text"].replace("\n", " ") for c in out).split()
                self.assertEqual(before, after)

    def test_most_cues_hold_two_windows(self):
        # The look only works if the pair is the norm, not the exception. Only a
        # capture with an odd window count ends in a one-window cue.
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                source = read_capture(path)
                out, _ = L.rolling_cues(source)
                full = sum(1 for c in out if len(c["text"].split("\n")) >= 2)
                self.assertGreater(full, len(out) * 0.95,
                                   f"only {full}/{len(out)} cues held two windows")


class TestRollingEdges(unittest.TestCase):
    """The stage on inputs small enough to reason about by hand."""

    def _cues(self, *windows):
        return [{"start": s, "end": e, "text": t} for s, e, t in windows]

    def test_empty_input(self):
        self.assertEqual(L.rolling_cues([])[0], [])
        self.assertEqual(L.rolling_cues([{"start": 0, "end": 0, "text": ""}])[0], [])

    def test_single_window(self):
        # Nothing to pair with, so it stands alone on one line.
        out, _ = L.rolling_cues(self._cues((1.0, 3.0, "only line")))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "only line")
        self.assertEqual(out[0]["start"], 1.0)
        self.assertEqual(out[0]["end"], 3.0)

    def test_the_youtube_look(self):
        # The example the layout exists for: two consecutive ASR windows become
        # one two-line cue, nothing repeated, starting at the first window's time.
        out, _ = L.rolling_cues(self._cues(
            (0.0, 2.0, "the Peruvian skulls and"),
            (1.5, 4.0, "No, before I thought Oh, go ahead."),
            (3.0, 5.0, "Sorry."),
            (3.5, 6.0, "No, go ahead."),
        ))
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["text"],
                         "the Peruvian skulls and\nNo, before I thought Oh, go ahead.")
        self.assertEqual(out[1]["text"], "Sorry.\nNo, go ahead.")
        self.assertEqual(out[0]["start"], 0.0)
        self.assertEqual(out[1]["start"], 3.0)

    def test_an_odd_window_count_ends_in_a_lone_window(self):
        out, _ = L.rolling_cues(self._cues(
            (0.0, 1.0, "one"),
            (1.0, 2.0, "two"),
            (2.0, 3.0, "three"),
        ))
        self.assertEqual(len(out), 2)
        self.assertEqual(out[1]["text"], "three")
        self.assertNotIn("\n", out[1]["text"])

    def test_end_is_the_next_sets_first_window(self):
        # The cue changes exactly when the next pair opens, so nothing overlaps.
        out, _ = L.rolling_cues(self._cues(
            (0.0, 9.0, "w1"),
            (2.0, 9.0, "w2"),
            (4.0, 9.0, "w3"),
            (6.0, 9.0, "w4"),
        ))
        self.assertEqual(out[0]["end"], 4.0)
        self.assertEqual(out[1]["end"], 9.0)   # last set keeps its own end

    def test_a_long_window_stays_one_long_line(self):
        # The ASR's own long line is written verbatim, however wide it is - the
        # player wraps it if it wants to, and second-guessing that here would make
        # the file differ from the captions for no gain.
        long_text = "a very long single window that cannot possibly fit " \
                    "on one line at all so it must wrap"
        out, _ = L.rolling_cues(self._cues(
            (0.0, 1.0, "short"),
            (1.0, 5.0, long_text),
        ))
        self.assertEqual(out[0]["text"], f"short\n{long_text}")
        self.assertEqual(out[0]["text"].split("\n"), ["short", long_text])
        self.assertEqual(len(long_text), len(out[0]["text"].split("\n")[1]))

    def test_two_wide_windows_stay_two_long_lines(self):
        # Windows are never merged or dropped to fit a budget; each keeps its own
        # line at its own length.
        long_a = " ".join(f"alpha{index}" for index in range(10))
        long_b = " ".join(f"bravo{index}" for index in range(10))
        out, _ = L.rolling_cues(self._cues(
            (0.0, 1.0, long_a),
            (1.0, 5.0, long_b),
        ))
        self.assertEqual(out[0]["text"].split("\n"), [long_a, long_b])

    def test_zero_length_window_still_displays(self):
        out, _ = L.rolling_cues(self._cues(
            (0.0, 0.0, "first"),
            (0.0, 0.0, "second"),
        ))
        self.assertGreater(out[0]["end"], out[0]["start"])

    def test_blank_windows_are_skipped(self):
        # A blank window must not claim a line of its own, so the windows either
        # side of it become a pair - exactly as if it had never been there.
        out, _ = L.rolling_cues(self._cues(
            (0.0, 1.0, "one"),
            (1.0, 2.0, "   "),
            (2.0, 3.0, "three"),
        ))
        self.assertEqual([c["text"] for c in out], ["one\nthree"])

    def test_windows_per_cue_is_configurable(self):
        cues = self._cues(*[(float(i), float(i) + 1.0, f"w{i}") for i in range(6)])
        out, _ = L.rolling_cues(cues, windows_per_cue=3)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["text"], "w0\nw1\nw2")

    def test_stats_describe_the_run(self):
        out, stats = L.rolling_cues(self._cues(
            (0.0, 2.0, "one"),
            (2.0, 4.0, "two"),
        ))
        self.assertEqual(stats["cues"], 1)
        self.assertEqual(stats["two_line"], 1)
        self.assertEqual(stats["windows"], 2)


if __name__ == "__main__":
    unittest.main()


