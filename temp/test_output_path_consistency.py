"""
Regression tests for output-path consistency (review item 3).

start_download() stores the SANITIZED basename and creates the matching
directory, but build_command() used to write the raw title-entry text back
into video_state["base_filename"] (update_video_state maps title ->
base_filename). The result was a directory named "My Video - - Part 1" while
yt-dlp's -o template and get_full_path() pointed at "My Video!! / Part <1>".

A title made only of illegal characters also sanitized to an empty string,
which collapsed the path to the output root.

No Qt event loop is needed.

Run from the repo root:
    python3 -m unittest temp.test_output_path_consistency -v
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app
from ytdl.utils import sanitize_title


class TitleEntry:
    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text


class Harness(app.DownloadMixin):
    """Minimal stand-in around build_command() and the path helpers."""

    def __init__(self, title, channel=""):
        self.video_state = {
            "title": "", "base_filename": "", "output_dir": "/tmp",
            "channel": channel, "clean_url": "https://www.youtube.com/watch?v=x",
            "media_type": "video", "quality": "1080", "video_format": "best",
            "audio_format": "best", "video_codec": "best", "site": "youtube",
            "is_download_running": False,
        }
        self.title_entry = TitleEntry(title)
        self.output_dir_entry = SimpleNamespace(text=lambda: "/tmp")
        self.yt_dlp_bin = "yt-dlp"
        self.ffmpeg_bin = None
        self.deno_bin = None
        self.cached_video_metadata = None
        self.signals = SimpleNamespace(
            append_output=SimpleNamespace(emit=lambda *a: None),
            update_dock_tile=SimpleNamespace(emit=lambda *a: None),
        )
        self.download_button = SimpleNamespace(setEnabled=lambda v: None)
        self.url_entry = SimpleNamespace(setFocus=lambda: None)

    update_video_state = app.YTDLPDownloaderGUI.update_video_state

    def set_download_button_status(self, _s):
        pass

    def get_selected_subtitle_codes(self):
        return []

    def get_selected_sb_categories(self):
        return []

    def get_clean_url(self):
        return self.video_state["clean_url"]

    def get_full_path(self, extension="", lang=None):
        return os.path.join(
            self.video_state["output_dir"], self.video_state["base_filename"]
        )

    def get_filename_template(self):
        return self.get_full_path() + ".%(ext)s"


class TestBuildCommandKeepsSanitizedTitle(unittest.TestCase):
    def _build(self, title, channel=""):
        harness = Harness(title, channel)
        # What start_download() stores before build_command() runs.
        sanitized = sanitize_title(title) or "downloaded_video"
        harness.update_video_state(title=sanitized)
        harness.build_command([])
        return harness, sanitized

    def test_punctuation_title_stays_sanitized(self):
        """Regression: build_command() restored the raw, unsanitized text."""
        raw = "My Video!! / Part <1>"
        harness, sanitized = self._build(raw)
        self.assertEqual(harness.video_state["base_filename"], sanitized)
        self.assertNotEqual(harness.video_state["base_filename"], raw)

    def test_output_template_contains_no_path_separators(self):
        harness, _ = self._build("A/B: C")
        template = harness.get_filename_template()
        # Only the one separator between the dir and the basename may remain.
        self.assertEqual(
            template, os.path.join("/tmp", harness.video_state["base_filename"]) + ".%(ext)s"
        )
        self.assertNotIn("..", template)

    def test_all_illegal_characters_fall_back(self):
        """An empty sanitized name would collapse the path to the root.

        Note "/" is rewritten to "-" by sanitize_title, so a slash-only title
        is NOT empty; these inputs really do collapse to "".
        """
        for raw in ("..", "!?", "@@@", "<<>>", "\t"):
            with self.subTest(title=raw):
                self.assertEqual(sanitize_title(raw), "")
                harness, _ = self._build(raw)
                self.assertEqual(
                    harness.video_state["base_filename"], "downloaded_video"
                )
                self.assertTrue(harness.get_full_path().startswith("/tmp/"))

    def test_plain_title_is_unchanged(self):
        harness, sanitized = self._build("Normal Title")
        self.assertEqual(harness.video_state["base_filename"], "Normal Title")
        self.assertEqual(sanitized, "Normal Title")

    def test_yt_dlp_command_is_built(self):
        harness, _ = self._build("Some Title")
        self.assertTrue(harness.build_command([])[0].endswith("yt-dlp"))


class TestPathAgreesWithDirectory(unittest.TestCase):
    """The -o template's directory must be the one start_download created."""

    def test_template_dir_matches_created_dir(self):
        import tempfile

        raw = "My Video!! / Part <1>"
        with tempfile.TemporaryDirectory() as root:
            sanitized = sanitize_title(raw) or "downloaded_video"
            created = os.path.join(root, sanitized)
            os.makedirs(created, exist_ok=True)

            harness = Harness(raw)
            harness.output_dir_entry = SimpleNamespace(text=lambda: root)
            harness.update_video_state(title=sanitized, output_dir=created)
            harness.build_command([])

            template_dir = os.path.dirname(harness.get_filename_template())
            self.assertEqual(
                os.path.realpath(template_dir),
                os.path.realpath(created),
                "yt-dlp would write outside the directory that was created",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
