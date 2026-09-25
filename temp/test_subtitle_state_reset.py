"""
Regression tests for subtitle-availability state across failed info fetches.

video_state["available_subtitles"] used to be written only on the success path
of the subtitle block, and _update_subtitle_checkboxes() only ran there too.
So any failure — a parse error, a nonzero yt-dlp exit, a timeout, or a video
with no audio — left the PREVIOUS video's languages in the state dict with
their checkboxes still enabled, and start_download() would then ask yt-dlp for
subtitles the new video does not have.

get_video_info() now resets the dict before any yt-dlp call and always refreshes
the checkboxes afterwards. No Qt event loop is needed.

Run from the repo root:
    python3 -m unittest temp.test_subtitle_state_reset -v
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
PREVIOUS = {"en": "real", "de": "auto"}


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
        "thumbnail_ready", "title_fetch_complete",
    )

    def __init__(self):
        for n in self.NAMES:
            setattr(self, n, _Signal())


class Harness(app.InfoFetchMixin):
    """Stand-in that records emissions and the availability state."""

    def __init__(self, available=PREVIOUS):
        self.video_state = {
            "url": URL, "clean_url": URL, "video_id": "jNQXAC9IVRw",
            "site": "youtube", "available_subtitles": dict(available),
        }
        self.signals = _Signals()
        self.yt_dlp_bin = "yt-dlp"
        self.deno_bin = None
        self.description_summaries = []

    update_video_state = app.YTDLPDownloaderGUI.update_video_state

    def _emit_description_summary(self):
        self.description_summaries.append(True)

    def lines(self):
        return self.signals.append_output.texts()

    def availability(self):
        return self.video_state.get("available_subtitles")


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
             "acodec": "mp4a.40.2", "filesize": 1},
        ],
        "subtitles": {"en": [{"ext": "srt", "url": "x"}]},
        "automatic_captions": {},
    }
    data.update(overrides)
    return data


def run(harness, data=None, returncode=0, timeout_exc=None):
    """Drive get_video_info() with yt-dlp stubbed out."""
    result = SimpleNamespace(
        returncode=returncode,
        stdout=json.dumps(data if data is not None else payload()),
        stderr="",
    )
    run_impl = mock.Mock(return_value=result)
    if timeout_exc is not None:
        run_impl.side_effect = timeout_exc
    with mock.patch.object(app.subprocess, "run", run_impl):
        with mock.patch.object(info_fetch, "requests", Requests()):
            harness.get_video_info(URL)


class TestAvailabilityResetOnSuccess(unittest.TestCase):
    def test_successful_fetch_populates_availability(self):
        h = Harness()
        run(h)
        self.assertEqual(h.availability(), {"en": "real"})
        self.assertIn(("en"), h.signals.update_subtitle_checkboxes.texts())

    def test_successful_fetch_without_subs_is_empty(self):
        h = Harness()
        run(h, payload(subtitles={}, automatic_captions={}))
        self.assertEqual(h.availability(), {})
        self.assertIn("Subtitles: None available", "\n".join(h.lines()))


class TestAvailabilityResetOnFailure(unittest.TestCase):
    """Every failure path must clear the previous video's availability."""

    def test_nonzero_exit_resets_availability(self):
        h = Harness()
        run(h, returncode=1)
        self.assertEqual(h.availability(), {}, "stale languages survived a failed fetch")
        self.assertTrue(h.signals.title_fetch_complete.emitted)
        self.assertTrue(h.signals.title_fetch_complete.emitted[-1][0]["error"])

    def test_no_audio_formats_resets_availability(self):
        h = Harness()
        # Video-only: every format has acodec="none".
        run(h, payload(formats=[{"format_id": "1", "ext": "mp4",
                                 "vcodec": "avc1", "acodec": "none",
                                 "height": 1080, "filesize": 1}]))
        self.assertEqual(h.availability(), {})
        self.assertIn("no audio formats", "\n".join(h.lines()))

    def test_timeout_resets_availability(self):
        h = Harness()
        run(h, timeout_exc=subprocess.TimeoutExpired(cmd=["yt-dlp"], timeout=15))
        self.assertEqual(h.availability(), {})

    def test_invalid_json_resets_availability(self):
        h = Harness()
        result = SimpleNamespace(returncode=0, stdout="{not json", stderr="")
        with mock.patch.object(app.subprocess, "run", return_value=result):
            h.get_video_info(URL)
        self.assertEqual(h.availability(), {})

    def test_malformed_subtitle_payload_resets_and_warns(self):
        """A parse error must not leave stale languages behind."""
        h = Harness()
        # `formats` inside automatic_captions is a dict, so formats[0] raises.
        broken = {"en": {"ext": "srt"}}  # not a list of format dicts
        run(h, payload(automatic_captions=broken))
        self.assertEqual(h.availability(), {})
        text = "\n".join(h.lines())
        self.assertIn("Could not read subtitle information", text)

    def test_parse_error_still_reports_the_warning_once(self):
        h = Harness()
        run(h, payload(automatic_captions={"en": {"ext": "srt"}}))
        warnings = [
            line for line in h.lines()
            if "Could not read subtitle information" in line
        ]
        self.assertEqual(len(warnings), 1)


class TestNoStaleCheckboxState(unittest.TestCase):
    """The checkbox refresh must happen even when nothing was learned."""

    def test_checkboxes_refreshed_on_failed_fetch(self):
        h = Harness()
        run(h, returncode=1)
        # update_subtitle_checkboxes is emitted after the reset, so the main
        # thread clears labels and disables the boxes.
        self.assertTrue(
            h.signals.update_subtitle_checkboxes.emitted,
            "checkboxes were never refreshed after a failed fetch",
        )

    def test_exception_clause_has_no_unreachable_json_name(self):
        """json.JSONDecodeError cannot occur there: the dicts are decoded."""
        import inspect

        source = inspect.getsource(info_fetch.InfoFetchMixin.get_video_info)
        self.assertNotIn("except (json.JSONDecodeError, Exception)", source)
        self.assertIn("except Exception as e:", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
