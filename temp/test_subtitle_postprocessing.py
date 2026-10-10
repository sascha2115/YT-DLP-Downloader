"""Regression tests for safe subtitle post-processing."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app  # noqa: E402
from ytdl.subtitle_layout import rolling_cues  # noqa: E402
from ytdl.utils import format_srt_time, format_subtitle_stats  # noqa: E402


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _SubtitleHarness(app.SubtitleMixin):
    def __init__(self):
        self.video_state = {}
        self.signals = type("Signals", (), {"append_output": _Signal()})()


class TestFormatSubtitleStats(unittest.TestCase):
    """The summary line is the only report a layout run leaves behind."""

    def test_base_form_omits_zero_loss_counters(self):
        self.assertEqual(
            format_subtitle_stats(2957, 1787, {"two_line": 1028}),
            "2957 in → 1787 out · 1028 two-line",
        )

    def test_loss_counters_are_appended_when_non_zero(self):
        self.assertEqual(
            format_subtitle_stats(2957, 1787, {
                "two_line": 1028, "no_text": 8, "too_short": 2, "dropped": 1,
            }),
            "2957 in → 1787 out · 1028 two-line · 8 no text · 2 too short · 1 dropped",
        )

    def test_rate_and_overshoot_are_reported(self):
        # The layout reports the rate it used and how many cues could not be
        # given their reading time, so a dense video is distinguishable from a
        # packing failure. "fast-paced" is deliberately not worded like a fault:
        # it is a fact about the speaker, not something the run got wrong.
        self.assertEqual(
            format_subtitle_stats(417, 152, {
                "two_line": 147, "wps": 3.2, "over_target": 84,
            }),
            "417 in → 152 out · 147 two-line · 3.2 wps · 84 fast-paced",
        )

    def test_name_prefix_identifies_the_file(self):
        self.assertTrue(
            format_subtitle_stats(10, 5, name="Title.srt").startswith("Title.srt: ")
        )


class TestNoTextIsEverLost(unittest.TestCase):
    """The overlap guard must never drop a cue on real material.

    The guard prefers shifting a cue over deleting it, so a drop means the cue
    could not be placed even after shifting - text that would be missing from
    the file with nothing to say so. It is zero on every capture; this test is
    the tripwire that says so on every run.
    """

    def test_no_capture_loses_a_cue(self):
        from temp.test_subtitle_layout import CAPTURES, read_capture

        self.assertTrue(CAPTURES, "no subtitle captures found")
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                harness = _SubtitleHarness()
                _cues, stats = harness._format_subtitles_for_display(read_capture(path))
                self.assertEqual(stats.get("dropped", 0), 0, path)
                self.assertEqual(stats.get("dropped_cues", []), [], path)

    def test_a_lost_cue_is_reported_by_name(self):
        # The warning path: if a cue is ever lost, the panel must name the file
        # and the count, and the log must carry the text.
        harness = _SubtitleHarness()
        lost = [{"start": 12.5, "end": 13.0, "text": "ein verlorener Satz"}]
        with self.assertLogs("ytdl.subtitles", level="WARNING") as captured:
            harness._warn_about_dropped_cues(lost, "Title.srt")
        self.assertTrue(any("ein verlorener Satz" in line for line in captured.output))
        panel = [args[0] for args in harness.signals.append_output.emitted]
        self.assertTrue(
            any("⚠️" in line and "Title.srt" in line for line in panel), panel
        )


class TestSubtitlePostProcessing(unittest.TestCase):
    def setUp(self):
        self.gui = _SubtitleHarness()

    def test_overlap_is_clamped_to_next_start(self):
        subtitles = [
            {"start": 10.0, "end": 10.2, "text": "first"},
            {"start": 10.05, "end": 10.3, "text": "second"},
        ]
        fixed, _dropped = self.gui._fix_subtitle_time_overlaps(subtitles)
        self.assertEqual(fixed[0]["end"], 10.05)
        self.assertLessEqual(fixed[0]["end"], fixed[1]["start"])

    def test_out_of_order_cue_is_shifted_not_dropped(self):
        # A subtitle that appears a little late is much better than a missing
        # sentence, so the guard moves the cue behind its predecessor and keeps
        # the text. Dropping is the last resort, not the first.
        subtitles = [
            {"start": 2.0, "end": 3.0, "text": "first"},
            {"start": 1.0, "end": 4.0, "text": "out-of-order"},
        ]
        fixed, dropped = self.gui._fix_subtitle_time_overlaps(subtitles)
        self.assertEqual(dropped, [])
        self.assertEqual([item["text"] for item in fixed],
                         ["first", "out-of-order"])
        self.assertEqual(fixed[1]["start"], 3.0)
        self.assertGreater(fixed[1]["end"], fixed[1]["start"])

    def test_cue_without_a_duration_keeps_its_text(self):
        fixed, dropped = self.gui._fix_subtitle_time_overlaps(
            [{"start": 5.0, "end": 5.0, "text": "no duration"}]
        )
        self.assertEqual(dropped, [])
        self.assertEqual(fixed[0]["text"], "no duration")
        self.assertGreater(fixed[0]["end"], fixed[0]["start"])

    def test_written_file_ends_with_a_newline(self):
        # A parser can drop a trailing block that has none; the SRT ends with a
        # newline. Pinned because the file is written in one place and the
        # detail is invisible until someone's player eats the last line.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "Title.en.srt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:02,000\nHallo Welt.\n\n")
            self.assertTrue(self.gui.resync_subtitles(path, path))
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertTrue(content.endswith("\n"))
            self.assertFalse(content.endswith("\n\n\n"))
            self.assertIn("Hallo Welt.", content)

    def test_malformed_srt_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "Title.en.srt")
            original = "not a subtitle file\n"
            with open(path, "w", encoding="utf-8") as f:
                f.write(original)

            result = self.gui.resync_subtitles(path, path)

            self.assertFalse(result)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), original)
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_summary_reports_what_the_layout_did(self):
        # The summary must describe the run that actually happened, so the
        # counts are checked against the layout module rather than hard-coded:
        # a layout change then shows up here instead of silently rewriting the
        # expectation.
        source_cues = [
            (0.0, 2.5, "Du bist ja erfolgreicher Junge"),
            (1.2, 4.0, "Unternehmer. Wieso sagst du dann, hier"),
            (3.9, 6.0, "läuft was falsch? Weil offensichtlich"),
            (5.8, 8.0, "bist du ja erfolgreich in diesem System"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "Title.en.srt")
            with open(path, "w") as fh:
                for i, (start, end, text) in enumerate(source_cues, 1):
                    fh.write(
                        f"{i}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}\n\n"
                    )
            self.assertTrue(self.gui.resync_subtitles(path, path))

            expected, _ = rolling_cues(
                [{"start": s, "end": e, "text": t} for s, e, t in source_cues]
            )
            stats = [
                args[0]
                for args in self.gui.signals.append_output.emitted
                if args and "in →" in str(args[0])
            ]
            self.assertEqual(len(stats), 1, self.gui.signals.append_output.emitted)
            line = stats[0]
            self.assertIn(f"4 in → {len(expected)} out", line)
            self.assertIn("Title.en.srt", line)
            self.assertIn("two-line", line)

    def test_valid_crlf_srt_is_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "Title.a.en.srt")
            output = os.path.join(temp_dir, "Title.en.srt")
            with open(source, "w", encoding="utf-8", newline="") as f:
                f.write(
                    "1\r\n00:00:00,000 --> 00:00:01,000\r\n"
                    "Hello\r\n\r\n"
                )

            result = self.gui.resync_subtitles(source, output)

            self.assertTrue(result)
            self.assertTrue(os.path.isfile(output))
            with open(output, encoding="utf-8") as f:
                self.assertIn("Hello", f.read())
            self.assertFalse(os.path.exists(output + ".tmp"))


if __name__ == "__main__":
    unittest.main()
