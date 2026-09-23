# End-to-end unittest for get_video_info() against a captured Odysee info JSON.
# Replays the real captured yt-dlp --print-json output through the real
# get_video_info() bound to a lightweight harness (no Qt event loop needed).
#
# Run from the repo root:  python3 -m unittest temp.test_odysee_info_parse -v

import json
import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app
import ytdl.info_fetch as info_fetch

ODYSEE_URL = "https://odysee.com/@Mantega:1/First-day-LBRY:1"
CAPTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "odysee-info-capture.json"
)


class Harness(app.InfoFetchMixin):
    """Minimal stand-in for YTDLPDownloaderGUI around get_video_info.

    Inherits the real InfoFetchMixin so new helper methods are picked up
    automatically; only shared GUI state/overrides are provided here.
    """

    def __init__(self):
        self.video_state = {
            "url": ODYSEE_URL,
            "clean_url": ODYSEE_URL,
            "video_id": "17f983b61f53091fb8ea58a9c56804e4ff8cff4d",
            "site": "odysee",
        }
        self.signals = app.SignalEmitter()
        self.yt_dlp_bin = "yt-dlp"
        self.deno_bin = None
        self.sb_calls = []
        self.description_summaries = []

    update_video_state = app.YTDLPDownloaderGUI.update_video_state

    def _emit_description_summary(self):
        self.description_summaries.append(True)


class TestOdyseeInfoParse(unittest.TestCase):
    def setUp(self):
        with open(CAPTURE_PATH, encoding="utf-8") as fh:
            self.capture = json.load(fh)

    def _run(self):
        h = Harness()
        captured = []
        h.signals.append_output.connect(captured.append)
        statuses = []
        h.signals.title_fetch_complete.connect(lambda r: statuses.append(r))

        payload = json.dumps(self.capture)
        fake_result = SimpleNamespace(returncode=0, stdout=payload, stderr="")
        with mock.patch.object(app.subprocess, "run", return_value=fake_result), \
             mock.patch.object(app.shutil, "which", return_value="/usr/bin/deno"):
            h.get_video_info(ODYSEE_URL)

        return h, captured, (statuses[-1] if statuses else {})

    def test_real_odysee_capture_parses(self):
        # NOTE: the fixture deliberately carries "subtitles": null /
        # "automatic_captions": null (sites without subs) - the parser must
        # treat null like absent instead of crashing the subtitle block.
        h, captured, status = self._run()
        text = "\n".join(captured)

        self.assertNotIn("Error parsing subtitle info", text)
        self.assertNotIn("no audio formats", text)
        self.assertFalse(status.get("error"))
        self.assertEqual(h.video_state.get("original_title"), self.capture["title"])
        # formats/info lines from the capture
        self.assertIn("Resolutions:", text)
        self.assertIn("Audio Codecs: AAC", text)
        self.assertIn("Subtitles: Not available on Odysee", text)

    def test_sponsorblock_skipped_for_odysee(self):
        h, captured, status = self._run()
        self.assertEqual(h.sb_calls, [])
        self.assertIn("SponsorBlock: Not available for this site", "\n".join(captured))

    def test_info_command_has_no_js_runtime(self):
        # Odysee needs no deno even when it is installed
        h = Harness()
        h.deno_bin = "deno"
        fake_result = SimpleNamespace(returncode=0, stdout=json.dumps(self.capture), stderr="")
        with mock.patch.object(app.shutil, "which", return_value="/usr/bin/deno"), \
             mock.patch.object(app.subprocess, "run", return_value=fake_result) as m:
            h.get_video_info(ODYSEE_URL)
        self.assertNotIn("--js-runtimes", m.call_args.args[0])

    def test_upload_date_present(self):
        # Guard against the SxxExxxx title prefix silently breaking: the
        # LBRY extractor derives upload_date from the claim timestamp
        self.assertEqual(self.capture["upload_date"], "20200725")

    def test_timeout_reports_site_specific_budget(self):
        # Reported bug: a slow Odysee resolve (~40s) was killed by the old
        # hardcoded 15s budget and reported as a timeout
        h = Harness()
        captured = []
        h.signals.append_output.connect(captured.append)
        with mock.patch.object(
            app.subprocess,
            "run",
            side_effect=app.subprocess.TimeoutExpired(cmd="yt-dlp", timeout=90),
        ):
            h.get_video_info(ODYSEE_URL)
        self.assertIn(
            "Timeout fetching video info (yt-dlp killed after 90s)",
            "\n".join(captured),
        )

    def test_wait_hint_text_is_site_aware(self):
        h = Harness()
        self.assertIn("Odysee's LBRY API", h._info_wait_hint_text(30))
        self.assertIn("Still fetching video info (30s)", h._info_wait_hint_text(30))
        h.video_state["site"] = "youtube"
        self.assertIn("responding slowly", h._info_wait_hint_text(30))

    def test_wait_hints_emitted_while_waiting(self):
        # A long-running info fetch must report progress instead of looking
        # frozen. NOTE: signals emitted from a worker thread are queued to the
        # main thread and only delivered while the Qt event loop runs, so the
        # monitor is driven from the main thread here (timings patched down).
        import threading

        h = Harness()
        captured = []
        h.signals.append_output.connect(captured.append)
        stop_event = threading.Event()
        threading.Timer(0.3, stop_event.set).start()

        with mock.patch.object(info_fetch, "INFO_FETCH_HINT_AFTER_SECONDS", 0.05), \
             mock.patch.object(info_fetch, "INFO_FETCH_HINT_EVERY_SECONDS", 0.05):
            h._info_wait_monitor(stop_event)

        hints = [line for line in captured if "Still fetching video info" in line]
        self.assertTrue(hints, captured)
        self.assertIn("Odysee", hints[0])
        self.assertGreaterEqual(len(hints), 2, "hint should repeat while waiting")

    def test_run_info_command_wraps_call_with_wait_monitor(self):
        # get_video_info() must go through _run_info_command so the monitor
        # starts for every info fetch (wiring check; no threads needed)
        h = Harness()
        started = []
        h._info_wait_monitor = lambda stop_event: started.append(stop_event)
        fake = SimpleNamespace(returncode=0, stdout="{}", stderr="")
        with mock.patch.object(app.subprocess, "run", return_value=fake):
            result = h._run_info_command(["yt-dlp", "--print-json"], 15)
        self.assertIs(result, fake)
        self.assertEqual(len(started), 1)


if __name__ == "__main__":
    unittest.main()
