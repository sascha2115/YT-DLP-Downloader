"""
Regression tests for the video description across failed info fetches.

video_state["description"] was written only on the success path of
get_video_info() (via update_video_state), but _emit_description_summary()
reads it on EVERY path. A failed fetch therefore re-printed the PREVIOUS
video's description in the output panel, and the stale text also leaked into
the thumbnail dialog and the NFO sidecar.

get_video_info() now resets the description before any yt-dlp call, next to
the existing available_subtitles reset. No Qt event loop is needed.

Run from the repo root:
    python3 -m unittest temp.test_description_state_reset -v
"""
import json
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app
import ytdl.info_fetch as info_fetch

URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
PREVIOUS_DESCRIPTION = (
    "This is the description of the previous video, which is long enough "
    "to survive the summary heuristics and would be printed on failure."
)


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)

    def texts(self):
        return [a[0] for a in self.emitted if a and isinstance(a[0], str)]


class _Signals:
    NAMES = (
        "update_title", "append_output", "update_last_line", "set_indeterminate",
        "update_download_progress", "update_dock_progress",
        "update_subtitle_checkboxes", "update_dock_tile", "clear_dock_progress",
        "update_sb_bar", "set_download_button_label", "set_download_button_status",
        "thumbnail_ready", "title_fetch_complete", "sb_log",
    )

    def __init__(self):
        for n in self.NAMES:
            setattr(self, n, _Signal())


class Harness(app.InfoFetchMixin):
    """Stand-in that records emissions and keeps the real summary method."""

    def __init__(self, description=PREVIOUS_DESCRIPTION):
        self.video_state = {
            "url": URL, "clean_url": URL, "video_id": "jNQXAC9IVRw",
            "site": "youtube", "available_subtitles": {}, "description": description,
        }
        self.signals = _Signals()
        self.yt_dlp_bin = "yt-dlp"
        self.deno_bin = None

    update_video_state = app.YTDLPDownloaderGUI.update_video_state

    def _emit(self, name, *args):
        getattr(self.signals, name).emit(*args)

    def lines(self):
        return self.signals.append_output.texts()

    def description_lines(self):
        return [line for line in self.lines() if "Description:" in line]


class Requests:
    exceptions = SimpleNamespace(RequestException=Exception)

    def get(self, *a, **k):
        return SimpleNamespace(status_code=200, json=lambda: [])


def payload(**overrides):
    data = {
        "id": "jNQXAC9IVRw", "title": "T", "channel": "C", "uploader": "C",
        "upload_date": "20260110", "duration_string": "10:00", "duration": 600,
        "extractor": "youtube", "webpage_url": URL, "description": "d",
        "thumbnail": "t", "language": "en",
        "formats": [
            {"format_id": "1", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
             "height": 1080, "filesize": 1},
            {"format_id": "2", "ext": "m4a", "vcodec": "none",
             "acodec": "mp4a", "filesize": 1},
        ],
        "subtitles": {"en": [{"ext": "srt"}]},
        "automatic_captions": {},
    }
    data.update(overrides)
    return data


def run(harness, payload_data=None, returncode=0, timeout_exc=None):
    if payload_data is None:
        payload_data = payload()
    result = SimpleNamespace(
        returncode=returncode,
        stdout=json.dumps(payload_data) if payload_data is not None else "",
        stderr="",
    )
    run_impl = mock.Mock(return_value=result)
    if timeout_exc is not None:
        run_impl.side_effect = timeout_exc
    with mock.patch.object(app.subprocess, "run", run_impl):
        with mock.patch.object(info_fetch, "requests", Requests()):
            harness.get_video_info(URL)


class TestDescriptionOnSuccess(unittest.TestCase):
    def test_successful_fetch_shows_the_new_description(self):
        h = Harness()
        run(h, payload(
            description=(
                "A brand new description for this particular video, with enough "
                "words in it to survive the summary heuristics used by the app."
            )
        ))
        self.assertNotEqual(h.video_state.get("description"), "")
        # The summary is only printed when it survives the heuristics.
        self.assertTrue(h.description_lines())


class TestDescriptionResetOnFailure(unittest.TestCase):
    """Every failure path must clear the previous video's description."""

    def test_nonzero_exit_clears_description(self):
        h = Harness()
        run(h, None, returncode=1)
        self.assertEqual(h.video_state.get("description"), "")
        self.assertEqual(
            h.description_lines(), [],
            "the previous video's description was printed after a failed fetch",
        )

    def test_timeout_clears_description(self):
        h = Harness()
        run(h, None, timeout_exc=subprocess.TimeoutExpired(cmd=["yt-dlp"], timeout=15))
        self.assertEqual(h.video_state.get("description"), "")
        self.assertEqual(h.description_lines(), [])

    def test_invalid_json_clears_description(self):
        h = Harness()
        result = SimpleNamespace(returncode=0, stdout="{not json", stderr="")
        with mock.patch.object(app.subprocess, "run", return_value=result):
            h.get_video_info(URL)
        self.assertEqual(h.video_state.get("description"), "")
        self.assertEqual(h.description_lines(), [])

    def test_no_audio_formats_clears_description(self):
        h = Harness()
        run(h, payload(formats=[{"format_id": "1", "ext": "mp4",
                                 "vcodec": "avc1", "acodec": "none",
                                 "height": 1080, "filesize": 1}]))
        self.assertEqual(h.video_state.get("description"), "")
        self.assertEqual(h.description_lines(), [])

    def test_missing_title_clears_description(self):
        h = Harness()
        run(h, payload(title=""))
        self.assertEqual(h.video_state.get("description"), "")
        self.assertEqual(h.description_lines(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
