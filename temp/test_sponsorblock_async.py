"""
Regression tests proving the SponsorBlock lookup cannot delay metadata
readiness.

title_fetch_complete used to be emitted only after check_sponsorblock()
returned, and that call did up to 3 requests with a 15s timeout each plus two
2s retry sleeps — a failing API held the UI (Download/Info buttons disabled)
for roughly 49s. The lookup is now optional enrichment handed to its own
daemon thread, so the hand-off must not wait on it.

No Qt event loop is needed: the completion signal is read from a recorder and
ytdl.info_fetch.requests is replaced by a blocking stub, so the test would
deadlock (and fail on timeout) if the call were still inline.

Run from the repo root:
    python3 -m unittest temp.test_sponsorblock_async -v
"""
import inspect
import json
import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app
import ytdl.info_fetch as info_fetch
from ytdl.config import SPONSORBLOCK_TIMEOUT_SECONDS

URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def make_info():
    return {
        "id": "jNQXAC9IVRw",
        "title": "Some Video Title",
        "channel": "Some Channel",
        "uploader": "Some Channel",
        "upload_date": "20260110",
        "duration_string": "00:10:00",
        "duration": 600,
        "extractor": "youtube",
        "webpage_url": URL,
        "description": "A description long enough to produce a summary.",
        "thumbnail": "https://example.invalid/t.jpg",
        "language": "en",
        "formats": [
            {
                "format_id": "137",
                "ext": "mp4",
                "vcodec": "avc1.640028",
                "acodec": "none",
                "height": 1080,
                "filesize": 1000,
            },
            {
                "format_id": "140",
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "filesize": 100,
            },
        ],
        "subtitles": {},
        "automatic_captions": {},
    }


class _Signal:
    """Records emit() calls in order (no Qt event loop, no queueing)."""

    def __init__(self, events=None, name=None):
        self.emitted = []
        self.events = events if events is not None else []
        self.name = name

    def emit(self, *args):
        self.emitted.append(args)
        if self.name:
            self.events.append(self.name)


class _Signals:
    """Stand-in for SignalEmitter that records emissions in call order.

    ``events`` is shared with the requests stub so a test can assert the
    relative order of the metadata hand-off and the SponsorBlock call.
    """

    NAMES = (
        "update_title",
        "append_output",
        "update_last_line",
        "set_indeterminate",
        "update_download_progress",
        "update_dock_progress",
        "update_subtitle_checkboxes",
        "update_dock_tile",
        "clear_dock_progress",
        "update_sb_bar",
        "set_download_button_label",
        "set_download_button_status",
        "thumbnail_ready",
        "title_fetch_complete",
    )

    def __init__(self, events=None):
        self.events = events if events is not None else []
        for name in self.NAMES:
            setattr(self, name, _Signal(self.events, name))

    def order(self):
        """Signal names in the order they were emitted."""
        return [name for name in self.events]


class Harness(app.InfoFetchMixin):
    """Minimal stand-in for YTDLPDownloaderGUI around get_video_info."""

    def __init__(self, events=None):
        self.video_state = {
            "url": URL,
            "clean_url": URL,
            "video_id": "jNQXAC9IVRw",
            "site": "youtube",
        }
        self.signals = _Signals(events)
        self.yt_dlp_bin = "yt-dlp"
        self.deno_bin = None
        self.sb_calls = []
        self.description_summaries = []

    update_video_state = app.YTDLPDownloaderGUI.update_video_state

    def _emit_description_summary(self):
        self.description_summaries.append(True)

    # -- recorders -------------------------------------------------------
    def completions(self):
        return [args[0] for args in self.signals.title_fetch_complete.emitted]

    def output_lines(self):
        return [args[0] for args in self.signals.append_output.emitted]


class RecordingRequests:
    """Stands in for the requests module inside ytdl.info_fetch.

    ``events`` is shared with the harness's signals so a test can assert that
    title_fetch_complete was emitted before the API call was even issued.
    """

    def __init__(self, gate, result=None, exc=None, events=None):
        self.gate = gate
        self.result = result
        self.exc = exc
        self.calls = []
        self.events = events if events is not None else []
        self.exceptions = SimpleNamespace(RequestException=Exception)

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        self.events.append("sponsorblock_request")
        # Blocks until released: an inline call would never return.
        self.gate.wait(timeout=10)
        if self.exc is not None:
            raise self.exc
        return self.result


def wait_for(predicate, timeout=5):
    """Poll until predicate() is true; return whether it became true."""
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


def join_sponsorblock_threads(timeout=5):
    for thread in list(threading.enumerate()):
        if thread.name == "sponsorblock-lookup":
            thread.join(timeout=timeout)


def run_fetch(harness, requests_stub, info=None):
    payload = json.dumps(info if info is not None else make_info())
    run_result = SimpleNamespace(returncode=0, stdout=payload, stderr="")
    with mock.patch.object(app.subprocess, "run", return_value=run_result):
        with mock.patch.object(info_fetch, "requests", requests_stub):
            harness.get_video_info(URL)



class TestSponsorBlockDoesNotBlockCompletion(unittest.TestCase):
    """The metadata hand-off must not wait on the SponsorBlock network call."""

    def setUp(self):
        self.gate = threading.Event()  # starts blocked
        self.events = []
        self.harness = Harness(events=self.events)

    def _fetch_in_background(self, stub):
        worker = threading.Thread(
            target=run_fetch, args=(self.harness, stub), daemon=True
        )
        worker.start()
        emitted = wait_for(lambda: bool(self.harness.completions()))
        self.gate.set()  # release the blocked API call
        worker.join(timeout=5)
        join_sponsorblock_threads()
        return emitted

    def test_completion_is_emitted_before_the_api_call_is_even_issued(self):
        """The regression: the request used to be issued inline, first.

        This asserts the *order* of the two events, so putting the call back
        in front of the emit makes it fail regardless of the timing window.
        """
        stub = RecordingRequests(
            self.gate,
            result=SimpleNamespace(status_code=200, json=lambda: []),
            events=self.events,
        )
        emitted = self._fetch_in_background(stub)

        self.assertTrue(
            emitted, "title_fetch_complete waited for the SponsorBlock request"
        )
        self.assertIn("title_fetch_complete", self.events)
        self.assertIn("sponsorblock_request", self.events)
        self.assertLess(
            self.events.index("title_fetch_complete"),
            self.events.index("sponsorblock_request"),
            "SponsorBlock was queried before the metadata hand-off",
        )

    def test_completion_is_emitted_while_api_is_still_blocked(self):
        stub = RecordingRequests(
            self.gate,
            result=SimpleNamespace(status_code=200, json=lambda: []),
            events=self.events,
        )
        emitted = self._fetch_in_background(stub)

        self.assertTrue(
            emitted, "title_fetch_complete waited for the SponsorBlock request"
        )
        self.assertFalse(self.harness.completions()[0].get("error"))
        # The request did run — just not on the metadata path.
        self.assertTrue(stub.calls, "SponsorBlock request never issued")
        self.assertEqual(stub.calls[0]["params"]["videoID"], "jNQXAC9IVRw")

    def test_single_request_with_short_configured_timeout(self):
        """One attempt on the short budget replaces 3x15s + 2x2s of retrying."""
        stub = RecordingRequests(
            self.gate, result=SimpleNamespace(status_code=200, json=lambda: [])
        )
        self._fetch_in_background(stub)

        self.assertEqual(len(stub.calls), 1, "expected exactly one API attempt")
        self.assertEqual(stub.calls[0]["timeout"], SPONSORBLOCK_TIMEOUT_SECONDS)
        self.assertLess(SPONSORBLOCK_TIMEOUT_SECONDS, 15)

    def test_no_retry_loop_or_sleeps_remain(self):
        source = inspect.getsource(
            info_fetch.InfoFetchMixin._fetch_sponsorblock_segments
        )
        self.assertNotIn("time.sleep", source)
        self.assertNotIn("max_attempts", source)

    def test_description_summary_no_longer_waits_for_sponsorblock(self):
        """The summary moved off the SponsorBlock path, so it always shows."""
        stub = RecordingRequests(
            self.gate, result=SimpleNamespace(status_code=200, json=lambda: [])
        )
        self._fetch_in_background(stub)
        self.assertTrue(
            self.harness.description_summaries,
            "description summary was skipped when SponsorBlock blocked",
        )


class TestSponsorBlockGateStaysSynchronous(unittest.TestCase):
    """The local site gate reports inline, so its log line keeps its place."""

    def setUp(self):
        self.gate = threading.Event()  # never set: a network call would hang
        self.harness = Harness()

    def _gate(self, video_id, site):
        stub = RecordingRequests(self.gate)
        with mock.patch.object(info_fetch, "requests", stub):
            self.harness.check_sponsorblock(
                video_id=video_id, site=site, duration_sec=10
            )
        return stub

    def test_non_youtube_site_skips_without_network(self):
        stub = self._gate("abc", "rumble")
        self.assertEqual(stub.calls, [], "no API call for a non-YouTube site")
        self.assertIn(
            "SponsorBlock: Not available for this site",
            "\n".join(self.harness.output_lines()),
        )

    def test_missing_video_id_reports_without_network(self):
        stub = self._gate("", "youtube")
        self.assertEqual(stub.calls, [])
        self.assertIn(
            "Could not extract video ID", "\n".join(self.harness.output_lines())
        )

    def test_lookup_uses_the_snapshot_not_later_video_state(self):
        """The lookup thread must not depend on later video_state changes."""
        stub = self._gate("snapshot-id", "youtube")
        self.harness.video_state["video_id"] = "changed-after-the-fact"
        self.harness.video_state["duration_sec"] = 999
        self.gate.set()
        join_sponsorblock_threads()
        self.assertTrue(wait_for(lambda: bool(stub.calls)))
        self.assertEqual(stub.calls[0]["params"]["videoID"], "snapshot-id")


class TestSponsorBlockFailureIsNotAFetchError(unittest.TestCase):
    """A failing API is reported, but the metadata fetch still succeeds."""

    def test_request_error_is_reported_without_erroring_the_fetch(self):
        gate = threading.Event()
        gate.set()  # do not block
        harness = Harness()

        stub = RecordingRequests(gate, exc=RuntimeError("connection reset"))
        run_fetch(harness, stub)
        join_sponsorblock_threads()

        text = "\n".join(harness.output_lines())
        self.assertIn("SponsorBlock", text)
        self.assertIn("connection reset", text)
        self.assertTrue(harness.completions())
        self.assertFalse(
            harness.completions()[0].get("error"),
            "a SponsorBlock outage must not look like a metadata failure",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
