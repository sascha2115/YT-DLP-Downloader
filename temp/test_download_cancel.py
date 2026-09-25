"""
Regression tests for download cancellation.

Cancellation is deliberately minimal: the worker stores the yt-dlp Popen in
self._proc, and cancel_download() SIGTERMs that process group (so a running
ffmpeg child dies too). Terminating the process makes the output loop end on
EOF, so the EXISTING finally block performs every bit of UI cleanup — no
parallel cleanup path was added.

These tests are synchronous and use a fake process: no threads, no events, no
sleeping, so they cannot flake. The two halves are checked separately:
cancel_download() terminates, and the finalization reports "Cancelled" rather
than "Error".

Run from the repo root:
    python3 -m unittest temp.test_download_cancel -v
"""
import os
import signal
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app


class FakeProcess:
    """Stands in for subprocess.Popen — no process is actually spawned."""

    def __init__(self, pid=4321, returncode=-15):
        self.pid = pid
        self._returncode = None
        self._final = returncode
        self.stdout = iter([])
        self.terminated = False
        self.killpg_calls = []

    def poll(self):
        return self._returncode

    def terminate(self):
        self.terminated = True
        self._returncode = self._final

    def wait(self):
        return self._final


class CancelHarness(app.DownloadMixin):
    """Minimal stand-in exposing only the cancellation surface."""

    def __init__(self, proc=None):
        self._proc = proc
        self._cancelled = False
        self._shutting_down = False
        self.signals = SimpleNamespace(
            append_output=SimpleNamespace(emit=self._log),
            set_cancel_button_visible=SimpleNamespace(emit=self._record_visible),
        )
        self.output_lines = []
        self.visible_calls = []

    def _log(self, *args):
        self.output_lines.append(args)

    def _record_visible(self, *args):
        self.visible_calls.append(args)


class TestCancelTerminatesProcessTree(unittest.TestCase):
    def setUp(self):
        self.proc = FakeProcess()
        self.h = CancelHarness(self.proc)

    def test_cancel_terminates_and_sets_flag(self):
        self.assertTrue(self.h.cancel_download())
        self.assertTrue(self.h._cancelled)

    def test_cancel_kills_the_whole_process_group(self):
        with mock.patch.object(app.os, "killpg") as killpg:
            with mock.patch.object(app.os, "getpgid", return_value=99):
                self.h.cancel_download()
        # yt-dlp and any ffmpeg child share this group, so both get SIGTERM.
        killpg.assert_called_once_with(99, signal.SIGTERM)

    def test_cancel_emits_user_feedback(self):
        self.h.cancel_download()
        logged = " ".join(" ".join(a) for a in self.h.output_lines)
        self.assertIn("Cancelling download", logged)
        # Hiding immediately makes a second click a no-op.
        self.assertEqual(self.h.visible_calls[-1], (False,))

    def test_falls_back_to_terminate_when_group_is_gone(self):
        def boom(_pgid, _sig):
            raise ProcessLookupError()

        with mock.patch.object(app.os, "killpg", side_effect=boom):
            self.h.cancel_download()
        self.assertTrue(self.proc.terminated)

    def test_no_process_is_a_noop(self):
        """Cancel before Popen records the intent; the worker acts on it."""
        h = CancelHarness(None)
        self.assertFalse(h.cancel_download())
        # The flag is still set so the worker's post-Popen check terminates.
        self.assertTrue(h._cancelled)
        self.assertEqual(h.visible_calls, [])

    def test_exited_process_is_a_noop_and_does_not_relabel_success(self):
        """Post-processing window: the process is gone, so decline the click.

        Returning False is not enough — _cancelled must stay False, or the
        worker's finally would report a finished download as "Cancelled" and
        zero its progress bars.
        """
        proc = FakeProcess()
        proc._returncode = 0  # exited normally; post-processing is running
        h = CancelHarness(proc)
        self.assertFalse(h.cancel_download())
        self.assertFalse(h._cancelled, "must not mark a finished run as cancelled")
        self.assertFalse(proc.terminated)
        logged = " ".join(" ".join(a) for a in h.output_lines)
        self.assertIn("nothing left to cancel", logged)
        # The button stays: post-processing still has to finish.
        self.assertEqual(h.visible_calls, [])


class TestTerminateProcessTreeGuards(unittest.TestCase):
    def test_none_is_safe(self):
        CancelHarness()._terminate_process_tree(None)  # must not raise

    def test_already_exited_is_skipped(self):
        proc = FakeProcess()
        proc._returncode = 0  # already exited
        h = CancelHarness(proc)
        with mock.patch.object(app.os, "killpg") as killpg:
            h._terminate_process_tree(proc)
        killpg.assert_not_called()
        self.assertFalse(proc.terminated)

    def test_object_without_poll_is_safe(self):
        h = CancelHarness()
        h._terminate_process_tree(SimpleNamespace())  # must not raise


class TestCloseEventCancels(unittest.TestCase):
    """Closing the window must cancel the download (review item 13, part 2).

    run_download() is NOT joined here on purpose: the worker is a daemon, and
    adding a join() to survive interpreter shutdown is the machinery this
    feature deliberately avoids. The guarantee is that yt-dlp (and ffmpeg) are
    terminated rather than orphaned, which this asserts directly.
    """

    class _WindowBase:
        """Stands in for QMainWindow: the tail of the real MRO."""

        base_close_called = False

        def closeEvent(self, event):
            type(self).base_close_called = True

    class _Window(app.DownloadMixin, app.UiBuildMixin, _WindowBase):
        closeEvent = app.YTDLPDownloaderGUI.closeEvent

    def setUp(self):
        self._Window.base_close_called = False

    def _window(self, proc):
        w = self._Window()
        w._proc = proc
        w._cancelled = False
        w._shutting_down = False
        w.output_lines = []
        w.signals = SimpleNamespace(
            append_output=SimpleNamespace(emit=lambda *a: w.output_lines.append(a)),
            set_cancel_button_visible=SimpleNamespace(emit=lambda *a: None),
        )
        return w

    def test_closing_window_terminates_the_process_group(self):
        proc = FakeProcess()
        w = self._window(proc)
        with mock.patch.object(app.os, "getpgid", return_value=77):
            with mock.patch.object(app.os, "killpg") as killpg:
                w.closeEvent(SimpleNamespace())
        self.assertTrue(w._cancelled)
        killpg.assert_called_with(77, signal.SIGTERM)

    def test_closing_with_no_download_is_safe(self):
        w = self._window(None)
        w.closeEvent(SimpleNamespace())  # must not raise
        # _cancelled is set unconditionally by design (it must be, so a cancel
        # landing before Popen is still honoured), but nothing was terminated:
        # the handler is a no-op without a process.
        self.assertTrue(w._cancelled)
        self.assertTrue(self._Window.base_close_called)

    def test_close_still_proceeds_to_the_base_handler(self):
        """Cancelling must not swallow the normal window close."""
        w = self._window(None)
        w.closeEvent(SimpleNamespace())
        self.assertTrue(self._Window.base_close_called)

    def test_close_event_still_reports_the_cancellation(self):
        """The Cancel feedback must survive closeEvent's _shutting_down.

        cancel_download() emits directly on purpose: it is main-thread-only, so
        the SignalEmitter is still alive. Routing it through _emit() would make
        this line vanish precisely when the user closes the window, which is
        exactly the kind of "consistency" refactor that breaks silently.
        """
        proc = FakeProcess()
        w = self._window(proc)
        w.output_lines = []
        with mock.patch.object(app.os, "getpgid", return_value=77):
            with mock.patch.object(app.os, "killpg"):
                w.closeEvent(SimpleNamespace())
        self.assertTrue(w._shutting_down)
        self.assertIn(("Cancelling download…",), w.output_lines)


if __name__ == "__main__":
    unittest.main(verbosity=2)
