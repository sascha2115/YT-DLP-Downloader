# End-to-end unittest for get_video_info() against a captured ZDF Mediathek
# info JSON. Replays the real captured yt-dlp -J output through the real
# get_video_info() bound to a lightweight harness (no Qt event loop needed).
#
# Run from the repo root:  python3 -m unittest temp.test_zdf_info_parse -v

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app

CAPTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "zdf-info-capture.json"
)


class Harness(app.InfoFetchMixin):
    """Minimal stand-in for YTDLPDownloaderGUI around get_video_info.

    Inherits the real InfoFetchMixin so new helper methods are picked up
    automatically; only shared GUI state/overrides are provided here.
    """

    def __init__(self, url):
        self.video_state = {
            "url": url,
            "clean_url": url,
            "video_id": "",
            "site": "zdf",
        }
        self.signals = app.SignalEmitter()
        self.yt_dlp_bin = "yt-dlp"
        self.deno_bin = None
        self.sb_calls = []
        self.description_summaries = []

    update_video_state = app.YTDLPDownloaderGUI.update_video_state

    def _emit_description_summary(self):
        self.description_summaries.append(True)


class TestZdfInfoParse(unittest.TestCase):
    def setUp(self):
        with open(CAPTURE_PATH, encoding="utf-8") as fh:
            self.capture = json.load(fh)
        self.url = self.capture.get("webpage_url") or (
            "https://www.zdf.de/video/talk/markus-lanz-114/"
            "markus-lanz-vom-22-september-2026-100"
        )

    def _run(self):
        h = Harness(self.url)
        captured = []
        h.signals.append_output.connect(captured.append)
        statuses = []
        h.signals.title_fetch_complete.connect(lambda r: statuses.append(r))

        payload = json.dumps(self.capture)
        fake_result = SimpleNamespace(returncode=0, stdout=payload, stderr="")
        with mock.patch.object(app.subprocess, "run", return_value=fake_result), \
             mock.patch.object(app.shutil, "which", return_value="/usr/bin/deno"):
            h.get_video_info(self.url)

        return h, captured, (statuses[-1] if statuses else {})

    def test_real_zdf_capture_parses(self):
        # The capture has 24 muxed formats (no audio-only stream) - the info
        # panel must report resolutions/audio codecs without parser errors.
        h, captured, status = self._run()
        text = "\n".join(captured)

        self.assertNotIn("Error parsing subtitle info", text)
        self.assertNotIn("no audio formats", text)
        self.assertFalse(status.get("error"))
        self.assertEqual(h.video_state.get("original_title"), self.capture["title"].strip())
        self.assertIn("Resolutions:", text)
        self.assertIn("Audio Codecs:", text)

    def test_deu_subtitles_map_to_german_checkbox(self):
        # ZDF captions are keyed "deu" (ISO 639-2); the German checkbox must
        # show them as available instead of "(none)".
        h, captured, status = self._run()
        text = "\n".join(captured)

        self.assertIn("Subtitles: de (real)", text)
        self.assertEqual(h.video_state.get("available_subtitles"), {"de": "real"})

    def test_sponsorblock_skipped_for_zdf(self):
        h, captured, status = self._run()
        self.assertEqual(h.sb_calls, [])
        self.assertIn("SponsorBlock: Not available for this site", "\n".join(captured))

    def test_info_command_has_no_js_runtime(self):
        # ZDF needs no deno even when it is installed
        h = Harness(self.url)
        h.deno_bin = "deno"
        fake_result = SimpleNamespace(
            returncode=0, stdout=json.dumps(self.capture), stderr=""
        )
        with mock.patch.object(app.shutil, "which", return_value="/usr/bin/deno"), \
             mock.patch.object(app.subprocess, "run", return_value=fake_result) as m:
            h.get_video_info(self.url)
        self.assertNotIn("--js-runtimes", m.call_args.args[0])

    def test_wait_hint_text_is_generic_for_zdf(self):
        # No per-site slow_hint -> the generic wording (ZDF resolves in ~1s)
        h = Harness(self.url)
        self.assertIn("Still fetching video info (30s)", h._info_wait_hint_text(30))
        self.assertIn("responding slowly", h._info_wait_hint_text(30))
        self.assertNotIn("LBRY", h._info_wait_hint_text(30))

    def test_german_media_sites_profile_flags(self):
        # The download side depends on these: no audio-only stream -> -x
        profile = app.SUPPORTED_SITES["zdf"]
        self.assertTrue(profile["always_extract_audio"])
        self.assertTrue(profile["supports_subtitles"])
        self.assertFalse(app.SUPPORTED_SITES["ard"]["always_extract_audio"])


if __name__ == "__main__":
    unittest.main()