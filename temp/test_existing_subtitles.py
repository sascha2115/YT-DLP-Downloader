"""Regression tests for subtitle existence checks in start_download()."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app  # noqa: E402


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _Text:
    def __init__(self, value):
        self.value = value

    def text(self):
        return self.value


class _Button:
    def setText(self, _value):
        pass


class _StartHarness(app.DownloadMixin):
    def __init__(self, output_dir):
        self.output_dir = output_dir
        self.title_entry = _Text("Title")
        self.output_dir_entry = _Text(output_dir)
        self.download_button = _Button()
        self.url_entry = type("UrlEntry", (), {"setFocus": lambda self: None})()
        # start_download() shows the cancel button via this signal.
        self.signals = type(
            "Signals",
            (),
            {"append_output": _Signal(), "set_cancel_button_visible": _Signal()},
        )()
        self.video_state = {
            "channel": "",
            "media_type": "subtitles",
            "is_download_running": False,
        }
        self.build_called = False
        self._cancelled = False
        self._shutting_down = False

    def get_selected_subtitle_codes(self):
        return ["en"]

    def get_full_path(self, extension="", lang=None):
        output_dir = self.video_state.get("output_dir", self.output_dir)
        base = os.path.join(output_dir, "Title")
        if lang:
            base += f".{lang}"
        return base + extension

    def update_video_state(self, **kwargs):
        self.video_state.update(kwargs)

    def set_download_button_status(self, _status):
        pass

    def build_command(self, _selected_langs):
        self.build_called = True
        return []


class TestSubtitleExistencePrecheck(unittest.TestCase):
    def _run_with_subtitle(self, filename):
        with tempfile.TemporaryDirectory() as output_dir:
            os.makedirs(os.path.join(output_dir, "Title"))
            with open(os.path.join(output_dir, "Title", filename), "w", encoding="utf-8") as f:
                f.write("subtitle")
            harness = _StartHarness(output_dir)
            harness.start_download()
            return harness

    def test_manual_vtt_is_recognized(self):
        harness = self._run_with_subtitle("Title.en.vtt")
        self.assertFalse(harness.build_called)
        self.assertIn(
            "✓ Subtitle exists: Title.en.vtt",
            [args[0] for args in harness.signals.append_output.emitted],
        )

    def test_auto_vtt_is_recognized(self):
        harness = self._run_with_subtitle("Title.a.en.vtt")
        self.assertFalse(harness.build_called)

    def test_skip_path_never_shows_the_cancel_button(self):
        """No download runs on the skip path, so no Cancel button may appear.

        Regression: the button used to be shown before the existence check, so
        "all files already exist" left it visible with nothing to cancel.
        """
        harness = self._run_with_subtitle("Title.en.srt")
        self.assertFalse(harness.build_called)
        self.assertNotIn(
            (True,),
            harness.signals.set_cancel_button_visible.emitted,
        )


if __name__ == "__main__":
    unittest.main()
