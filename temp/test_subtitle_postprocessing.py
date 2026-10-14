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
        # _emit() is only reached from the worker-thread paths
        # (_resync_subtitle_for_language / _run_subtitle_resync); the
        # post-processing tests call resync_subtitles() directly, which is
        # now Qt-free and returns its lines instead of emitting.
        self._shutting_down = False

    def _emit(self, name, *args):
        # Mirror the real DownloadMixin._emit(): drop worker-thread emissions
        # while the window is closing, so a worker winding down after close
        # cannot emit into a dying QObject.
        if getattr(self, "_shutting_down", False):
            return
        getattr(self.signals, name).emit(*args)


def _run(harness, path):
    """Call resync_subtitles() in place and return (ok, lines).

    resync_subtitles() is now Qt-free: it returns its panel output rather
    than emitting it, so the caller prints the lines. The post-processing
    tests read the summary line from the return value instead of from the
    signal emitter.
    """
    return harness.resync_subtitles(path, path)


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
        from temp.test_subtitle_rolling import CAPTURES, read_capture

        self.assertTrue(CAPTURES, "no subtitle captures found")
        for path in CAPTURES:
            with self.subTest(capture=os.path.basename(path)):
                harness = _SubtitleHarness()
                _cues, stats = harness._format_subtitles_for_display(read_capture(path))
                self.assertEqual(stats.get("dropped", 0), 0, path)
                self.assertEqual(stats.get("dropped_cues", []), [], path)

    def test_a_lost_cue_is_reported_by_name(self):
        # The warning path: if a cue is ever lost, the panel must name the file
        # and the count, and the log must carry the text. _warn_about_dropped_cues
        # is Qt-free and returns its panel lines, so the assertion reads the
        # return value rather than the signal emitter.
        harness = _SubtitleHarness()
        lost = [{"start": 12.5, "end": 13.0, "text": "ein verlorener Satz"}]
        with self.assertLogs("ytdl.subtitles", level="WARNING") as captured:
            panel = harness._warn_about_dropped_cues(lost, "Title.srt")
        self.assertTrue(any("ein verlorener Satz" in line for line in captured.output))
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
            ok, _lines = _run(self.gui, path)
            self.assertTrue(ok)
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

            ok, _lines = _run(self.gui, path)

            self.assertFalse(ok)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), original)
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_summary_reports_what_the_layout_did(self):
        # The summary must describe the run that actually happened, so the
        # counts are checked against the layout module rather than hard-coded:
        # a layout change then shows up here instead of silently rewriting the
        # expectation. resync_subtitles() is Qt-free and returns its panel
        # output, so the summary is read from the return value.
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
            ok, lines = _run(self.gui, path)
            self.assertTrue(ok)

            expected, _ = rolling_cues(
                [{"start": s, "end": e, "text": t} for s, e, t in source_cues]
            )
            stats = [line for line in lines if line and "in →" in str(line)]
            self.assertEqual(len(stats), 1, lines)
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

            ok, _lines = self.gui.resync_subtitles(source, output)

            self.assertTrue(ok)
            self.assertTrue(os.path.isfile(output))
            with open(output, encoding="utf-8") as f:
                self.assertIn("Hello", f.read())
            self.assertFalse(os.path.exists(output + ".tmp"))

    def _write_and_run(self, temp_dir, block_text, name="Title.srt"):
        source = os.path.join(temp_dir, name)
        output = os.path.join(temp_dir, name)
        with open(source, "w", encoding="utf-8") as f:
            f.write(block_text)
        ok, _lines = self.gui.resync_subtitles(source, output)
        return ok, source, output

    def test_block_without_a_cue_number_is_processed(self):
        # A valid SRT block may omit the cue number entirely; the parser must
        # find the timestamp line by content, not by assuming lines[1].
        with tempfile.TemporaryDirectory() as temp_dir:
            block = (
                "00:00:00,000 --> 00:00:01,000\n"
                "No cue number here.\n\n"
            )
            ok, source, output = self._write_and_run(temp_dir, block)
            self.assertTrue(ok, "block without a cue number was ignored")
            with open(output, encoding="utf-8") as f:
                self.assertIn("No cue number here", f.read())

    def test_block_with_a_cue_identifier_is_processed(self):
        # Some SRT writers put a cue identifier on the line before the
        # timestamp; the text after the arrow is still the cue text.
        with tempfile.TemporaryDirectory() as temp_dir:
            block = (
                "1\n"
                "cue-identifier-42\n"
                "00:00:00,000 --> 00:00:01,000\n"
                "Identifier before the timestamp.\n\n"
            )
            ok, source, output = self._write_and_run(temp_dir, block)
            self.assertTrue(ok, "block with a cue identifier was ignored")
            with open(output, encoding="utf-8") as f:
                self.assertIn("Identifier before the timestamp", f.read())

    def test_mixed_block_shapes_in_one_file(self):
        # A file mixing numbered, unnumbered and identifier-led blocks must
        # not lose any of them - the fixed-index assumption would silently
        # drop the unnumbered and identifier-led cues.
        with tempfile.TemporaryDirectory() as temp_dir:
            block = (
                "1\n00:00:00,000 --> 00:00:01,000\nFirst.\n\n"
                "00:00:01,000 --> 00:00:02,000\nSecond.\n\n"
                "2\nid-7\n00:00:02,000 --> 00:00:03,000\nThird.\n\n"
            )
            ok, source, output = self._write_and_run(temp_dir, block)
            self.assertTrue(ok)
            with open(output, encoding="utf-8") as f:
                content = f.read()
            for word in ("First.", "Second.", "Third."):
                self.assertIn(word, content, f"lost cue: {word}")


class TestStandaloneSubtitleWorker(unittest.TestCase):
    """The Subs button handler runs resync_subtitles() on a worker thread.

    resync_subtitles() is Qt-free and returns its panel output; the worker
    (_run_subtitle_resync) prints the lines through _emit() and emits
    subtitle_processing_complete(ok). These tests exercise that path without
    a Qt event loop, so they drive the worker directly and assert on the
    signal emitter - which is exactly what the main thread's slot does.
    """

    def _harness(self, path):
        h = _SubtitleHarness()
        h.signals.append_output = _Signal()
        h.signals.subtitle_processing_complete = _Signal()
        return h

    def test_worker_emits_completion_and_lines(self):
        # A valid file: the worker emits the panel lines and signals ok=True.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "Title.srt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:02,000\nHello world.\n\n")
            h = self._harness(path)
            h._run_subtitle_resync(path)
            self.assertTrue(h.signals.subtitle_processing_complete.emitted[-1][0])
            self.assertTrue(
                any("📊" in str(a[0]) for a in h.signals.append_output.emitted)
            )

    def test_worker_signals_false_on_failure(self):
        # A malformed file: the worker still emits, but ok=False, and the
        # original file is left untouched (no .tmp).
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "Title.srt")
            original = "not a subtitle file\n"
            with open(path, "w", encoding="utf-8") as f:
                f.write(original)
            h = self._harness(path)
            h._run_subtitle_resync(path)
            self.assertFalse(h.signals.subtitle_processing_complete.emitted[-1][0])
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), original)
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_worker_drops_ui_lines_when_window_is_closing(self):
        # _emit() no-ops while _shutting_down is set, so a worker still winding
        # down after the window closes cannot emit into a dying QObject. Both
        # the panel lines and the completion handshake go through _emit(), so
        # a closing window drops both - the controls are being destroyed with
        # it and nobody is waiting for the slot.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "Title.srt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:02,000\nHello world.\n\n")
            h = self._harness(path)
            h._shutting_down = True
            h._run_subtitle_resync(path)
            # Everything the worker tried to emit was dropped by _emit().
            self.assertEqual(h.signals.append_output.emitted, [])
            self.assertEqual(h.signals.subtitle_processing_complete.emitted, [])


if __name__ == "__main__":
    unittest.main()
