"""Regression tests for safe subtitle post-processing."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app  # noqa: E402


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _SubtitleHarness(app.SubtitleMixin):
    def __init__(self):
        self.video_state = {}
        self.signals = type("Signals", (), {"append_output": _Signal()})()


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
