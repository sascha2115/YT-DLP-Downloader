# End-to-end unittest for get_video_info() against a captured Rumble info JSON.
# Replays the real captured yt-dlp --print-json output through the real
# get_video_info() bound to a lightweight harness (no Qt event loop needed).
#
# Run from the repo root:  python3 -m unittest temp.test_rumble_info_parse -v

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app

RUMBLE_URL = (
    "https://rumble.com/v7fto6c-president-trump-released-plan-to-dismantle-"
    "deep-state-most-important-three-.html?pri=3JgZNpYkTks5WcyY9S7mVglx7Ql"
)
CAPTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rumble-info-capture.json")


class Harness(app.InfoFetchMixin):
    """Minimal stand-in for YTDLPDownloaderGUI around get_video_info.

    Inherits the real InfoFetchMixin so new helper methods are picked up
    automatically; only shared GUI state/overrides are provided here.
    """

    def __init__(self):
        self.video_state = {
            "url": RUMBLE_URL,
            "clean_url": RUMBLE_URL,
            "video_id": "v7fto6c",
            "site": "rumble",
        }
        self.signals = app.SignalEmitter()
        self.yt_dlp_bin = "yt-dlp"
        self.deno_bin = None
        self.sb_calls = []
        self.description_summaries = []

    update_video_state = app.YTDLPDownloaderGUI.update_video_state

    def _emit_description_summary(self):
        self.description_summaries.append(True)


class TestRumbleInfoParse(unittest.TestCase):
    def setUp(self):
        with open(CAPTURE_PATH, encoding="utf-8") as fh:
            self.capture = json.load(fh)

    def _run(self, info=None):
        h = Harness()
        captured = []
        h.signals.append_output.connect(captured.append)
        results = []
        h.signals.title_fetch_complete.connect(lambda r: results.append(r))

        payload = json.dumps(info if info is not None else self.capture)
        fake_result = SimpleNamespace(returncode=0, stdout=payload, stderr="")
        with mock.patch.object(app.subprocess, "run", return_value=fake_result):
            h.get_video_info(RUMBLE_URL)

        return h, captured, (results[-1] if results else {})

    def test_real_rumble_capture_parses(self):
        h, captured, status = self._run()
        text = "\n".join(captured)

        # The old failure must be gone
        self.assertNotIn("no audio formats", text)
        self.assertFalse(status.get("error"))

        # Title processed and emitted
        self.assertIn("Audio Codecs: AAC", text)
        self.assertIn("Subtitles: en (auto)", text)
        titles = []
        h.signals.update_title.connect(titles.append)
        # update_title was emitted during get_video_info; re-check via state
        self.assertEqual(h.video_state.get("original_title"), self.capture["title"])

        # SponsorBlock skipped for rumble
        self.assertEqual(h.sb_calls, [])
        self.assertIn("SponsorBlock: Not available for this site", text)

    def test_audio_codec_aac_recognized(self):
        h, captured, status = self._run()
        self.assertIn("Audio Codecs: AAC", "\n".join(captured))

    def test_resolution_list_excludes_audio_height_junk(self):
        h, captured, status = self._run()
        text = "\n".join(captured)
        # "Resolutions:" line must not contain the bogus 192p from audio-192p
        res_line = [line for line in captured if line.startswith("Resolutions:")][0]
        self.assertNotIn("192p", res_line)
        self.assertIn("480p", res_line)

    def test_muxed_only_still_parses(self):
        # A Rumble video with only unknown-codec HLS formats must not error
        info = json.loads(json.dumps(self.capture))
        info["formats"] = [f for f in info["formats"] if f["format_id"].startswith("hls-")]
        h, captured, status = self._run(info)
        text = "\n".join(captured)
        self.assertNotIn("no audio formats", text)
        self.assertFalse(status.get("error"))
        self.assertIn(h.video_state.get("original_title"), (self.capture["title"],))
        self.assertEqual(h.video_state["original_title"], self.capture["title"])

    def test_truly_audioless_is_error(self):
        # All formats explicitly acodec="none" -> hard error (old behavior)
        info = json.loads(json.dumps(self.capture))
        for f in info["formats"]:
            f["acodec"] = "none"
        h, captured, status = self._run(info)
        self.assertIn("no audio formats", "\n".join(captured))
        self.assertTrue(status.get("error"))

    def test_info_command_skips_deno_for_rumble(self):
        # Even with deno installed, the Rumble info-fetch command must not
        # carry --js-runtimes (the Rumble extractor needs no JS runtime).
        h = Harness()
        h.deno_bin = "deno"
        fake_result = SimpleNamespace(returncode=0, stdout=json.dumps(self.capture), stderr="")
        with mock.patch.object(
            app.shutil, "which", return_value="/usr/bin/deno"
        ), mock.patch.object(app.subprocess, "run", return_value=fake_result) as m:
            h.get_video_info(RUMBLE_URL)
        cmd = m.call_args.args[0]
        self.assertNotIn("--js-runtimes", cmd)


if __name__ == "__main__":
    unittest.main()
