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
        self.append_output = _Signal()
        self._fetching_thumbnail = False
        self._image = image

    def _emit(self, name, *args):
        getattr(self, name).emit(*args)

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

        image, description, snapshot_url = h.thumbnail_ready.emitted[0]
        self.assertEqual(image, h._image)
        self.assertEqual(description, "Description of video A")
        self.assertEqual(snapshot_url, "https://example.com/a.png")

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

        image, description, snapshot_url = h.thumbnail_ready.emitted[0]
        # The receiver compares the captured URL against the current one and
        # returns early when they differ — before any Qt construction. Drive
        # the real method on a harness whose current URL no longer matches,
        # with QDialog stubbed so any construction would be recorded.
        constructed = []

        class _Harness:
            def __init__(self, current_url):
                self.video_state = {"thumbnail_url": current_url}
                self._fetching_thumbnail = True

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
                _Harness("https://example.com/b.png"),
                image,
                description,
                snapshot_url,
            )
        finally:
            ui_build.QDialog = original_dialog
        self.assertEqual(
            constructed,
            [],
            "a superseded thumbnail response must not construct a dialog",
        )

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