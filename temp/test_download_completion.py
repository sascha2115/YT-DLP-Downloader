"""Regression tests for download completion and failure progress state.

These tests exercise the real DownloadMixin.run_download() finalization path
with a fake yt-dlp process and no Qt event loop.
"""

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


class _Signals:
    def __init__(self):
        for name in (
            "append_output",
            "update_last_line",
            "set_indeterminate",
            "update_download_progress",
            "update_dock_progress",
            "clear_dock_progress",
            "set_download_button_label",
            "set_download_button_status",
            "set_cancel_button_visible",
            "enable_button",
            "update_dock_tile",
        ):
            setattr(self, name, _Signal())


class _Process:
    def __init__(self, lines, returncode):
        self.stdout = iter(lines)
        self.returncode = returncode
        self.pid = 4321

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def wait(self):
        return self.returncode


class _DownloadHarness(app.DownloadMixin):
    def __init__(self, base_path, media_type="video", fail_metadata=False):
        self.video_state = {
            "media_type": media_type,
            "is_download_running": True,
        }
        self.signals = _Signals()
        self.simulate_download_error = False
        self.cached_video_metadata = None
        self.base_path = base_path
        self.fail_metadata = fail_metadata
        self.direct_clear_dock_calls = 0
        self.sidecar_attempts = 0
        self.video_state["base_filename"] = "Title"
        # Cancellation state: run_download() sets _proc and clears both of
        # these in its finally block.
        self._proc = None
        self._cancelled = False
        self._shutting_down = False

    def get_full_path(self, extension=""):
        return self.base_path + extension

    def get_file_metadata(self, _filename):
        if self.fail_metadata:
            raise RuntimeError("post-processing failure")
        return {}

    def _analyze_downloaded_file(self, _filename):
        pass

    def create_edl_file(self, _filename):
        # The success path writes sidecars through create_edl_file/create_nfo_file
        # directly; the failure path through _try_create_sidecar_files. Count
        # both so a test can tell whether the failure path re-entered after
        # the success path already wrote them.
        self.sidecar_attempts += 1
        return []

    def create_nfo_file(self, _filename):
        self.sidecar_attempts += 1

    def _try_create_sidecar_files(self):
        self.sidecar_attempts += 1

    def cleanup_files(self, _selected_langs):
        pass

    def clearDockProgress(self):
        self.direct_clear_dock_calls += 1


class TestDownloadCompletion(unittest.TestCase):
    def _run(
        self,
        returncode,
        fail_metadata=False,
        create_output=True,
        include_destination=True,
        media_type="video",
        fallback_extension=None,
        cancelled=False,
        shutting_down=False,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = os.path.join(temp_dir, "Title")
            media_path = base_path + ".mp4"
            if create_output:
                with open(media_path, "wb") as output:
                    output.write(b"test")
            if fallback_extension:
                with open(base_path + fallback_extension, "wb") as output:
                    output.write(b"test")

            lines = []
            if include_destination:
                lines.append(f"[download] Destination: {media_path}")
            lines.append("[download] 100% of 1.00MiB in 00:00:01")

            harness = _DownloadHarness(
                base_path,
                media_type=media_type,
                fail_metadata=fail_metadata,
            )
            if cancelled:
                # Simulates the user pressing Cancel mid-download: the flag is
                # set, then the (fake) process dies with a SIGTERM code.
                harness._cancelled = True
            if shutting_down:
                # Simulates the window closing while the worker is still
                # running: closeEvent() set this before cancelling.
                harness._shutting_down = True
            process = _Process(lines, -15 if cancelled else returncode)
            with mock.patch.object(app.subprocess, "Popen", return_value=process):
                harness.run_download(["yt-dlp"], [])
            return harness

    @staticmethod
    def _output_lines(harness):
        return [args[0] for args in harness.signals.append_output.emitted if args]

    def test_worker_cleanup_uses_signal(self):
        harness = self._run(returncode=1)
        self.assertTrue(harness.signals.clear_dock_progress.emitted)
        self.assertEqual(harness.direct_clear_dock_calls, 0)

    def test_failed_process_resets_real_100_percent_progress(self):
        harness = self._run(returncode=1)
        progress = harness.signals.update_download_progress.emitted
        self.assertEqual(progress[-1], (0, 0))
        self.assertNotIn((1000, 1000), progress)
        self.assertEqual(harness.signals.set_download_button_status.emitted[-1], ("error",))

    def test_post_processing_failure_resets_progress(self):
        harness = self._run(returncode=0, fail_metadata=True)
        progress = harness.signals.update_download_progress.emitted
        self.assertEqual(progress[-1], (0, 0))
        self.assertEqual(harness.signals.set_download_button_status.emitted[-1], ("error",))

    def test_successful_muxed_download_keeps_audio_bar_empty(self):
        harness = self._run(returncode=0)
        progress = harness.signals.update_download_progress.emitted
        # Single muxed stream: both video and audio bars show 100%
        self.assertEqual(progress[-1], (1000, 1000))

    def test_missing_captured_output_is_failure(self):
        harness = self._run(returncode=0, create_output=False)
        self.assertIn(
            "🚩 yt-dlp exited successfully but no output file was found",
            self._output_lines(harness),
        )
        self.assertEqual(harness.signals.set_download_button_status.emitted[-1], ("error",))

    def test_fallback_output_is_used_before_success(self):
        harness = self._run(returncode=0, include_destination=False)
        self.assertTrue(harness.video_state.get("full_path", "").endswith("Title.mp4"))
        self.assertEqual(harness.signals.set_download_button_status.emitted[-1], ("success",))

    def test_audio_mode_rejects_video_fallback(self):
        harness = self._run(
            returncode=0,
            create_output=False,
            include_destination=False,
            media_type="audio",
            fallback_extension=".mp4",
        )
        self.assertIn(
            "🚩 yt-dlp exited successfully but no output file was found",
            self._output_lines(harness),
        )
        self.assertEqual(harness.signals.set_download_button_status.emitted[-1], ("error",))

    def test_cancelled_download_is_not_reported_as_an_error(self):
        """A user-requested stop reads as "Cancelled", not "Error"."""
        harness = self._run(returncode=0, cancelled=True)
        self.assertEqual(
            harness.signals.set_download_button_label.emitted[-1],
            ("Download Cancelled",),
        )
        # Neutral styling: a cancel is not a failure.
        self.assertEqual(
            harness.signals.set_download_button_status.emitted[-1], ("",)
        )
        self.assertIn("Download cancelled.", self._output_lines(harness))
        self.assertNotIn(
            "🚩 yt-dlp process failed with exit code -15", self._output_lines(harness)
        )

    def test_cancelled_download_resets_progress(self):
        harness = self._run(returncode=0, cancelled=True)
        progress = harness.signals.update_download_progress.emitted
        self.assertEqual(progress[-1], (0, 0))
        self.assertNotIn((1000, 1000), progress)

    def test_cancelled_download_hides_the_cancel_button(self):
        harness = self._run(returncode=0, cancelled=True)
        self.assertEqual(
            harness.signals.set_cancel_button_visible.emitted[-1], (False,)
        )

    def test_cancelled_download_clears_process_handle(self):
        """No stale PID may be left for a later cancel to signal."""
        harness = self._run(returncode=0, cancelled=True)
        self.assertIsNone(harness._proc)
        # The flag is reset too, so the NEXT run starts uncancelled.
        self.assertFalse(harness._cancelled)

    def test_closing_window_suppresses_final_ui_signals(self):
        """The worker's final UI batch must be skipped once the window closes.

        Those signals belong to a SignalEmitter that is being destroyed with
        the window; emitting into it mid-teardown raises (or, at interpreter
        shutdown, can crash the process).
        """
        harness = self._run(returncode=1, shutting_down=True)
        for name in (
            "set_download_button_label",
            "set_download_button_status",
            "enable_button",
            "update_dock_tile",
            "clear_dock_progress",
            "set_cancel_button_visible",
        ):
            self.assertEqual(
                harness.signals.__getattribute__(name).emitted,
                [],
                f"{name} was emitted while the window was closing",
            )
        # The cleanup that does NOT touch Qt still happened.
        self.assertFalse(harness.video_state["is_download_running"])
        self.assertIsNone(harness._proc)

    def test_closing_window_suppresses_body_signals_too(self):
        """Not just the final batch: the in-flight output lines as well.

        These are emitted from the try/except body while the worker winds down
        ("Download cancelled.", the subtitle step lines, ...), which is the
        other half of the teardown race.
        """
        harness = self._run(returncode=0, cancelled=True, shutting_down=True)
        self.assertEqual(harness.signals.append_output.emitted, [])
        self.assertEqual(harness.signals.set_indeterminate.emitted, [])
        self.assertEqual(harness.signals.update_download_progress.emitted, [])

    def test_normal_run_still_emits_the_final_ui_batch(self):
        """Control case: without the flag, nothing is suppressed."""
        harness = self._run(returncode=0)
        self.assertTrue(harness.signals.clear_dock_progress.emitted)
        self.assertTrue(harness.signals.set_download_button_label.emitted)

    def test_plain_failure_still_says_error(self):
        harness = self._run(returncode=1)
        self.assertEqual(
            harness.signals.set_download_button_label.emitted[-1],
            ("Download Error 🚨",),
        )

    def test_failure_on_video_creates_sidecars(self):
        # A failed video job still has .info.json, so the metadata sidecars
        # (.edl, .nfo) are generated in the failure path.
        harness = self._run(returncode=1, media_type="video")
        self.assertEqual(harness.sidecar_attempts, 1)

    def test_failure_on_video_only_creates_sidecars(self):
        harness = self._run(returncode=1, media_type="video_only")
        self.assertEqual(harness.sidecar_attempts, 1)

    def test_failure_on_audio_does_not_create_sidecars(self):
        # Sidecars are video-oriented (an .edl jumps a media timeline, an .nfo
        # describes an episode); an audio-only failure has no video to attach
        # them to, so the failure path must not generate them.
        harness = self._run(returncode=1, media_type="audio")
        self.assertEqual(harness.sidecar_attempts, 0)
        self.assertTrue(
            any("no video sidecars" in line for line in self._output_lines(harness)),
            self._output_lines(harness),
        )

    def test_failure_on_subtitles_does_not_create_sidecars(self):
        # A subtitles-only job never produced media; generating an .nfo/.edl
        # for it would describe a video that does not exist.
        harness = self._run(returncode=1, media_type="subtitles")
        self.assertEqual(harness.sidecar_attempts, 0)

    def test_exception_after_download_creates_sidecars(self):
        # A Python exception raised after yt-dlp wrote its .info.json (e.g.
        # a post-processing failure) must still attempt sidecars — the same
        # nonzero-exit branch does not cover it.
        harness = self._run(returncode=0, fail_metadata=True, media_type="video")
        self.assertEqual(harness.sidecar_attempts, 1)

    def test_exception_on_audio_does_not_create_sidecars(self):
        harness = self._run(
            returncode=0, fail_metadata=True, media_type="audio"
        )
        self.assertEqual(harness.sidecar_attempts, 0)
        self.assertTrue(
            any("no video sidecars" in line for line in self._output_lines(harness)),
            self._output_lines(harness),
        )

    def test_exception_on_subtitles_does_not_create_sidecars(self):
        harness = self._run(
            returncode=0, fail_metadata=True, media_type="subtitles"
        )
        self.assertEqual(harness.sidecar_attempts, 0)

    def test_cancelled_run_never_attempts_sidecars(self):
        # A user stop is not a failure; nothing to finalize.
        harness = self._run(returncode=0, cancelled=True, media_type="video")
        self.assertEqual(harness.sidecar_attempts, 0)

    def test_sidecar_attempt_failure_is_not_retried(self):
        # If the sidecar step itself raises, the flag must stop a second
        # attempt — the except branch must not re-enter it.
        class _Harness(_DownloadHarness):
            def _try_create_sidecar_files(self):
                self.sidecar_attempts += 1
                raise RuntimeError("sidecar boom")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = os.path.join(temp_dir, "Title")
            media_path = base_path + ".mp4"
            with open(media_path, "wb") as output:
                output.write(b"test")
            lines = [
                f"[download] Destination: {media_path}",
                "[download] 100% of 1.00MiB in 00:00:01",
            ]
            harness = _Harness(base_path, media_type="video")
            process = _Process(lines, 1)
            with mock.patch.object(app.subprocess, "Popen", return_value=process):
                harness.run_download(["yt-dlp"], [])
            self.assertEqual(harness.sidecar_attempts, 1)
            self.assertTrue(
                any("Error creating sidecar files" in line
                    for line in self._output_lines(harness)),
                self._output_lines(harness),
            )

    def test_exception_after_sidecars_are_written_is_not_rewritten(self):
        # The nuance: an exception raised AFTER the success path wrote the
        # sidecars (here, during post-download analysis) must not rewrite them
        # — the success path marks them attempted, so the except branch skips
        # the failure path entirely. The media file is on disk, so the failure
        # line must not call it a media failure either.
        class _Harness(_DownloadHarness):
            def _analyze_downloaded_file(self, _filename):
                raise RuntimeError("analysis boom")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = os.path.join(temp_dir, "Title")
            media_path = base_path + ".mp4"
            with open(media_path, "wb") as output:
                output.write(b"test")
            lines = [
                f"[download] Destination: {media_path}",
                "[download] 100% of 1.00MiB in 00:00:01",
            ]
            harness = _Harness(base_path, media_type="video")
            process = _Process(lines, 0)
            with mock.patch.object(app.subprocess, "Popen", return_value=process):
                harness.run_download(["yt-dlp"], [])
            # The success path wrote the sidecars once (EDL + NFO); the
            # except branch did not re-enter _try_create_sidecar_files.
            self.assertEqual(harness.sidecar_attempts, 2)
            output_lines = self._output_lines(harness)
            self.assertTrue(
                any("analysis boom" in line for line in output_lines),
                output_lines,
            )
            self.assertFalse(
                any("Media download failed" in line for line in output_lines),
                output_lines,
            )


class _ProcessManyLines(_Process):
    """A process that emits one error line per read, to drive the bound."""

    def __init__(self, lines, returncode, n):
        self._lines = list(lines)
        self._extra = n
        self.returncode = returncode
        self.pid = 4321
        # run_download() iterates process.stdout, so expose the iterator here.
        self.stdout = self

    def __iter__(self):
        return self

    def __next__(self):
        if self._lines:
            return self._lines.pop(0)
        if self._extra > 0:
            self._extra -= 1
            return "error: something went wrong again"
        raise StopIteration


class TestCapturedErrorOutput(unittest.TestCase):
    """The failure log keeps a bounded tail and redacts secrets."""

    def _run_many(self, n, returncode=1, media_type="video"):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = os.path.join(temp_dir, "Title")
            media_path = base_path + ".mp4"
            with open(media_path, "wb") as output:
                output.write(b"test")
            lines = [
                f"[download] Destination: {media_path}",
                "[download] 100% of 1.00MiB in 00:00:01",
            ]
            harness = _DownloadHarness(base_path, media_type=media_type)
            process = _ProcessManyLines(lines, returncode, n)
            with mock.patch.object(app.subprocess, "Popen", return_value=process):
                with self.assertLogs("ytdl.download", level="ERROR") as captured:
                    harness.run_download(["yt-dlp"], [])
            return harness, captured.output

    def test_capture_is_bounded(self):
        # yt-dlp can emit thousands of error lines on a long download; the
        # failure log keeps a bounded tail instead of growing without limit.
        harness, logs = self._run_many(5000)
        self.assertTrue(any("captured output" in line for line in logs))
        # The tail is the last MAX_CAPTURED_OUTPUT_LINES lines, so the count
        # in the log must not exceed the bound.
        for line in logs:
            if "captured output" in line:
                self.assertIn("of 200+ lines", line)

    def test_truncation_is_announced(self):
        _, logs = self._run_many(5000)
        self.assertTrue(
            any("200+ lines" in line for line in logs),
            logs,
        )

    def test_untruncated_capture_says_nothing_about_truncation(self):
        # A short failure must not claim a tail that did not happen.
        _, logs = self._run_many(3)
        self.assertFalse(any("200+" in line for line in logs), logs)

    def test_sensitive_command_args_are_redacted(self):
        # Cookies, headers, passwords and extractor args carry secrets; the
        # logged command must show the flag but never its value.
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = os.path.join(temp_dir, "Title")
            media_path = base_path + ".mp4"
            with open(media_path, "wb") as output:
                output.write(b"test")
            lines = [
                f"[download] Destination: {media_path}",
                "[download] 100% of 1.00MiB in 00:00:01",
            ]
            harness = _DownloadHarness(base_path, media_type="video")
            process = _Process(lines, 1)
            cmd = [
                "yt-dlp",
                "--cookies-from-browser",
                "firefox:1abc.default-release",
                "--header",
                "Authorization: Bearer secret",
                "https://example.com/watch?v=abc",
            ]
            with mock.patch.object(app.subprocess, "Popen", return_value=process):
                with self.assertLogs("ytdl.download", level="ERROR") as captured:
                    harness.run_download(cmd, [])
            joined = "\n".join(captured.output)
            self.assertIn("***", joined)
            self.assertNotIn("1abc.default-release", joined)
            self.assertNotIn("Bearer secret", joined)
            # The URL identifies the failed job, so the scheme, host and path
            # are kept - but its query string can carry a token, so that is
            # masked.
            self.assertIn("https://example.com/watch?", joined)
            self.assertNotIn("v=abc", joined)

    def test_equals_form_sensitive_flags_are_redacted(self):
        # yt-dlp accepts "--flag value" and "--flag=value"; only the first
        # shape was handled, so a long header or extractor arg passed as one
        # token leaked its value into the log.
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = os.path.join(temp_dir, "Title")
            media_path = base_path + ".mp4"
            with open(media_path, "wb") as output:
                output.write(b"test")
            lines = [
                f"[download] Destination: {media_path}",
                "[download] 100% of 1.00MiB in 00:00:01",
            ]
            harness = _DownloadHarness(base_path, media_type="video")
            process = _Process(lines, 1)
            cmd = [
                "yt-dlp",
                "--header=Authorization: Bearer secret",
                "--extractor-args=token:abc123",
                "https://example.com/watch?v=abc",
            ]
            with mock.patch.object(app.subprocess, "Popen", return_value=process):
                with self.assertLogs("ytdl.download", level="ERROR") as captured:
                    harness.run_download(cmd, [])
            joined = "\n".join(captured.output)
            self.assertIn("--header=***", joined)
            self.assertIn("--extractor-args=***", joined)
            self.assertNotIn("Bearer secret", joined)
            self.assertNotIn("abc123", joined)

    def test_non_sensitive_equals_form_flags_are_kept(self):
        # The equals form is also used for ordinary flags; those must not be
        # redacted, or the log would hide what was actually requested.
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = os.path.join(temp_dir, "Title")
            media_path = base_path + ".mp4"
            with open(media_path, "wb") as output:
                output.write(b"test")
            lines = [
                f"[download] Destination: {media_path}",
                "[download] 100% of 1.00MiB in 00:00:01",
            ]
            harness = _DownloadHarness(base_path, media_type="video")
            process = _Process(lines, 1)
            cmd = [
                "yt-dlp",
                "--merge-output-format=mp4",
                "https://example.com/watch?v=abc",
            ]
            with mock.patch.object(app.subprocess, "Popen", return_value=process):
                with self.assertLogs("ytdl.download", level="ERROR") as captured:
                    harness.run_download(cmd, [])
            joined = "\n".join(captured.output)
            self.assertIn("--merge-output-format=mp4", joined)


if __name__ == "__main__":
    unittest.main()
