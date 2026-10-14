# Regression tests for the thumbnail dialog's metadata consistency.
#
# open_thumbnail_dialog() fetches the image on a daemon worker thread, so by
# the time the image arrives the user may have switched videos. The worker
# used to capture only the URL; _show_thumbnail_dialog() then read the
# description from live video_state, pairing an old image with a new video's
# description. The fix captures the description with the URL and re-checks the
# URL in the receiver, so a response for a superseded video is dropped.
#
# Run from the repo root:  python3 -m unittest temp.test_thumbnail_dialog -v
#
# Pure: no Qt event loop. open_thumbnail_dialog and _show_thumbnail_dialog are
# bound from the real mixin to a fake that carries a recorded thumbnail_ready
# signal and a stubbed requests module.

import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app
import ytdl.ui_build as ui_build

open_thumbnail_dialog = app.YTDLPDownloaderGUI.open_thumbnail_dialog
show_thumbnail_dialog = app.YTDLPDownloaderGUI._show_thumbnail_dialog


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class Harness:
    def __init__(self, url, description, image=b"\x89PNG"):
        self.video_state = {
            "thumbnail_url": url,
            "description": description,
        }
        self.thumbnail_ready = _Signal()
        self.thumbnail_error = _Signal()
        self.append_output = _Signal()
        # The real slot's except branch emits on signals.append_output, so the
        # harness needs a stand-in for it when the dialog path is exercised.
        self.signals = _Signal()
        self.signals.append_output = _Signal()
        self._fetching_thumbnail = False
        self._thumbnail_request_token = 0
        self._image = image

    def _emit(self, name, *args):
        getattr(self, name).emit(*args)

    def append_output_text(self, text):
        pass

    def _requests_get(self, url, timeout=None):
        # Simulate a slow fetch: block until the test releases the gate.
        self._gate.wait()
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            content=self._image,
        )


class TestThumbnailDialogMetadata(unittest.TestCase):
    def setUp(self):
        self.gate = threading.Event()

    def _harness(self, url, description):
        h = Harness(url, description)
        h._gate = self.gate
        return h

    def _wait_for_emission(self, harness):
        for _ in range(400):
            if harness.thumbnail_ready.emitted:
                return True
            time.sleep(0.005)
        return False

    def test_description_travels_with_the_image(self):
        """The dialog gets the description captured at click time, not live."""
        h = self._harness("https://example.com/a.png", "Description of video A")
        with mock.patch.object(ui_build.requests, "get", side_effect=h._requests_get):
            open_thumbnail_dialog(h)
            self.gate.set()
            self.assertTrue(
                self._wait_for_emission(h),
                "thumbnail_ready was never emitted",
            )

        image, description, snapshot_url, token = h.thumbnail_ready.emitted[0]
        self.assertEqual(image, h._image)
        self.assertEqual(description, "Description of video A")
        self.assertEqual(snapshot_url, "https://example.com/a.png")
        self.assertEqual(token, 1)

    def test_a_superseded_url_is_discarded_at_the_receiver(self):
        """A response for a video the user already switched away from is dropped."""
        h = self._harness("https://example.com/a.png", "Description of video A")
        with mock.patch.object(ui_build.requests, "get", side_effect=h._requests_get):
            open_thumbnail_dialog(h)
            # The user switches videos before the image arrives.
            h.video_state["thumbnail_url"] = "https://example.com/b.png"
            h.video_state["description"] = "Description of video B"
            self.gate.set()
            self.assertTrue(
                self._wait_for_emission(h),
                "thumbnail_ready was never emitted",
            )

        image, description, snapshot_url, token = h.thumbnail_ready.emitted[0]
        # The receiver compares the captured URL against the current one and
        # returns early when they differ — before any Qt construction. Drive
        # the real method on a harness whose current URL no longer matches,
        # with QDialog stubbed so any construction would be recorded.
        constructed = []

        class _Harness:
            def __init__(self, current_url, token):
                self.video_state = {"thumbnail_url": current_url}
                self._fetching_thumbnail = True
                self._thumbnail_request_token = token

            def append_output_text(self, text):
                pass

        original_dialog = ui_build.QDialog

        class _RecordingDialog:
            def __init__(self, *a, **k):
                constructed.append(True)

            def setWindowTitle(self, _):
                pass

            def exec(self):
                pass

        ui_build.QDialog = _RecordingDialog
        try:
            show_thumbnail_dialog(
                _Harness("https://example.com/b.png", token),
                image,
                description,
                snapshot_url,
                token,
            )
        finally:
            ui_build.QDialog = original_dialog
        self.assertEqual(
            constructed,
            [],
            "a superseded thumbnail response must not construct a dialog",
        )

    def test_a_stale_request_cannot_clear_a_newer_request_in_flight_guard(self):
        """The receiver resets the guard only for the matching request token.

        The worker used to reset _fetching_thumbnail unconditionally in its
        finally, and the receiver did the same. If a newer request started
        while an older one was in flight, the older result's receiver would
        reset the flag again — even when it rejected the result as stale —
        clearing the newer request's guard. The token is now checked on both
        sides, so a stale delivery cannot touch it.
        """
        # Stub the Qt classes so the real method can run without a QApplication.
        original_dialog = ui_build.QDialog
        original_browser = ui_build.QTextBrowser
        original_label = ui_build.QLabel
        original_shortcut = ui_build.QShortcut
        original_pixmap = ui_build.QPixmap

        class _Stub:
            def __init__(self, *a, **k):
                pass

            def setWindowTitle(self, _):
                pass

            def exec(self):
                pass

        ui_build.QDialog = _Stub
        ui_build.QTextBrowser = _Stub
        ui_build.QLabel = _Stub
        ui_build.QShortcut = _Stub
        ui_build.QPixmap = _Stub
        try:
            # Drive the receiver directly with a stale token but a matching
            # URL: the URL check alone would not catch this case, so the token
            # check is what must keep the guard live.
            guard_harness = Harness("https://example.com/b.png", "Description of video B")
            guard_harness._thumbnail_request_token = 2
            guard_harness._fetching_thumbnail = True
            show_thumbnail_dialog(
                guard_harness, b"\x89PNG", "Description of video A", "https://example.com/b.png", 1
            )
            self.assertTrue(
                guard_harness._fetching_thumbnail,
                "a stale request must not clear a newer request's in-flight guard",
            )

            # And a matching token must reset it, so a finished request does
            # not leave the button unclickable.
            guard_harness._fetching_thumbnail = True
            show_thumbnail_dialog(
                guard_harness,
                b"\x89PNG",
                "Description of video B",
                "https://example.com/b.png",
                2,
            )
            self.assertFalse(
                guard_harness._fetching_thumbnail,
                "a matching request must reset the flight guard",
            )
        finally:
            ui_build.QDialog = original_dialog
            ui_build.QTextBrowser = original_browser
            ui_build.QLabel = original_label
            ui_build.QShortcut = original_shortcut
            ui_build.QPixmap = original_pixmap

    def test_the_flight_guard_is_reset_even_if_the_emit_raises(self):
        """A failed delivery must not leave the Info button unclickable.

        The emit sat outside the worker's try block, so a raise there would
        skip the result and leave _fetching_thumbnail stuck — no later click
        could fetch again. It now sits inside a try/finally that resets the
        guard on every exit path.
        """
        h = self._harness("https://example.com/a.png", "Description of video A")

        class _BrokenSignal(_Signal):
            def emit(self, *args):
                raise RuntimeError("boom")

        h.thumbnail_ready = _BrokenSignal()
        with mock.patch.object(ui_build.requests, "get", side_effect=h._requests_get):
            open_thumbnail_dialog(h)
            self.gate.set()
            # Poll until the worker has finished and reset the guard.
            for _ in range(400):
                if not h._fetching_thumbnail:
                    break
                time.sleep(0.005)
        self.assertFalse(
            h._fetching_thumbnail,
            "the flight guard must be reset on every exit path",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)