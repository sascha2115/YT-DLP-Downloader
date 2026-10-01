"""Regression tests for safe subtitle post-processing."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app  # noqa: E402
from ytdl.subtitles import (  # noqa: E402
    MAX_CUE_DURATION_S,
    MAX_LINE_CHARS,
    MAX_MERGE_GAP_S,
    PAUSE_THRESHOLD_S,
)
from ytdl.utils import format_subtitle_stats  # noqa: E402


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _SubtitleHarness(app.SubtitleMixin):
    def __init__(self):
        self.video_state = {}
        self.signals = type("Signals", (), {"append_output": _Signal()})()


class TestLayoutThresholds(unittest.TestCase):
    """The hoisted layout constants must keep their historical values.

    They were lifted out of three signatures so a future tuning pass has one
    place to change them. Retuning is a deliberate act with a visible effect on
    every subtitle file, so the current numbers are pinned here - this test
    failing means "somebody tuned the layout", which is what it is for.
    """

    def test_values_are_unchanged(self):
        self.assertEqual(MAX_LINE_CHARS, 50)
        self.assertEqual(MAX_MERGE_GAP_S, 1.0)
        self.assertEqual(MAX_CUE_DURATION_S, 6.0)
        self.assertEqual(PAUSE_THRESHOLD_S, 1.0)

    def test_wrapper_uses_the_layout_budget_not_smart_wraps_own_default(self):
        # _smart_wrap keeps its own 42-char default; the wrapper deliberately
        # passes MAX_LINE_CHARS (50). These 45 characters contain spaces, so
        # they would be split by the generic default and must not be here.
        gui = _SubtitleHarness()
        self.assertNotIn("\n", gui._wrap_subtitle_text("word " * 9))
        self.assertIn("\n", gui._wrap_subtitle_text("word " * 12))


class TestFormatSubtitleStats(unittest.TestCase):
    """The summary line is the only report a layout run leaves behind."""

    def test_base_form_omits_zero_loss_counters(self):
        self.assertEqual(
            format_subtitle_stats(2957, 1787, two_line=1028),
            "2957 in → 1787 out · 1028 two-line",
        )

    def test_loss_counters_are_appended_when_non_zero(self):
        self.assertEqual(
            format_subtitle_stats(
                2957, 1787, two_line=1028, no_text=8, too_short=2, dropped=1
            ),
            "2957 in → 1787 out · 1028 two-line · 8 no text · 2 too short · 1 dropped",
        )

    def test_name_prefix_identifies_the_file(self):
        self.assertTrue(
            format_subtitle_stats(10, 5, name="Title.srt").startswith("Title.srt: ")
        )


class TestSubtitlePostProcessing(unittest.TestCase):
    def setUp(self):
        self.gui = _SubtitleHarness()

    def test_overlap_is_clamped_to_next_start(self):
        subtitles = [
            {"start": 10.0, "end": 10.2, "text": "first"},
            {"start": 10.05, "end": 10.3, "text": "second"},
        ]
        fixed = self.gui._fix_subtitle_time_overlaps(subtitles)
        self.assertEqual(fixed[0]["end"], 10.05)
        self.assertLessEqual(fixed[0]["end"], fixed[1]["start"])

    def test_chronologically_invalid_cue_is_dropped(self):
        subtitles = [
            {"start": 2.0, "end": 3.0, "text": "first"},
            {"start": 1.0, "end": 4.0, "text": "out-of-order"},
        ]
        fixed = self.gui._fix_subtitle_time_overlaps(subtitles)
        self.assertEqual([item["text"] for item in fixed], ["first"])

    def test_malformed_srt_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "Title.en.srt")
            original = "not a subtitle file\n"
            with open(path, "w", encoding="utf-8") as f:
                f.write(original)

            result = self.gui.resync_subtitles(path, [], path)

            self.assertFalse(result)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), original)
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_no_segment_path_skips_time_map_and_preserves_timestamps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "Title.a.en.srt")
            output = os.path.join(temp_dir, "Title.en.srt")
            with open(source, "w", encoding="utf-8") as f:
                f.write(
                    "1\n00:00:00,000 --> 00:00:01,000\n"
                    "Hello\n\n"
                )

            with mock.patch.object(
                self.gui,
                "_build_time_map",
                side_effect=AssertionError("time map should not be built"),
            ):
                result = self.gui.resync_subtitles(source, [], output)

            self.assertTrue(result)
            with open(output, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("00:00:00,000 --> 00:00:01,000", content)

    def test_summary_reports_what_the_layout_did(self):
        # Realistic ASR lengths matter here: the boundary pass flattens the
        # text of every cue it visits (see _optimize_subtitle_pause_boundaries),
        # so a merged pair only stays two lines once its text exceeds
        # MAX_LINE_CHARS. The counters describe what lands on disk either way.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "Title.en.srt")
            with open(path, "w") as fh:
                fh.write(
                    "1\n00:00:00,000 --> 00:00:02,500\n"
                    "Du bist ja erfolgreicher Junge\n\n"
                    "2\n00:00:01,200 --> 00:00:04,000\n"
                    "Unternehmer. Wieso sagst du dann, hier\n\n"
                    "3\n00:00:03,900 --> 00:00:06,000\n"
                    "läuft was falsch? Weil offensichtlich\n\n"
                    "4\n00:00:05,800 --> 00:00:08,000\n"
                    "bist du ja erfolgreich in diesem System\n\n"
                )
            self.assertTrue(self.gui.resync_subtitles(path, [], path))

            stats = [
                args[0]
                for args in self.gui.signals.append_output.emitted
                if args and "in →" in str(args[0])
            ]
            self.assertEqual(len(stats), 1, self.gui.signals.append_output.emitted)
            self.assertIn("4 in → 2 out", stats[0])
            self.assertIn("2 two-line", stats[0])
            self.assertIn("Title.en.srt", stats[0])

    def test_valid_crlf_srt_is_processed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "Title.a.en.srt")
            output = os.path.join(temp_dir, "Title.en.srt")
            with open(source, "w", encoding="utf-8", newline="") as f:
                f.write(
                    "1\r\n00:00:00,000 --> 00:00:01,000\r\n"
                    "Hello\r\n\r\n"
                )

            result = self.gui.resync_subtitles(source, [], output)

            self.assertTrue(result)
            self.assertTrue(os.path.isfile(output))
            with open(output, encoding="utf-8") as f:
                self.assertIn("Hello", f.read())
            self.assertFalse(os.path.exists(output + ".tmp"))


if __name__ == "__main__":
    unittest.main()
