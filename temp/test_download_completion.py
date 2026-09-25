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
            "enable_button",
            "update_dock_tile",
        ):
            setattr(self, name, _Signal())


class _Process:
    def __init__(self, lines, returncode):
        self.stdout = iter(lines)
        self.returncode = returncode

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

    def get_full_path(self):
        return self.base_path

    def get_file_metadata(self, _filename):
        if self.fail_metadata:
            raise RuntimeError("post-processing failure")
        return {}

    def _analyze_downloaded_file(self, _filename):
        pass

    def create_edl_file(self, _filename):
        return []

    def create_nfo_file(self, _filename):
        pass

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
            process = _Process(lines, returncode)
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
        self.assertEqual(progress[-1], (1000, 0))
        self.assertNotIn((1000, 1000), progress)

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


if __name__ == "__main__":
    unittest.main()
