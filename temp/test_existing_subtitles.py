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


class _StartHarness(app.DownloadMixin, app.SubtitleMixin):
    # start_download() asks SubtitleMixin whether a language-less subtitle
    # ("Title.srt") can stand in for a language, so both mixins are needed
    # here just like in the assembled YTDLPDownloaderGUI.

    def __init__(self, output_dir, available=None, selected=("en",)):
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
            "available_subtitles": available or {},
        }
        self.selected = list(selected)
        self.build_called = False
        self._cancelled = False
        self._shutting_down = False

    def get_selected_subtitle_codes(self):
        return list(self.selected)

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


def _run_start_download(filename, available=None, selected=("en",)):
    """Lay down <output>/Title/<filename> and run the real start_download()."""
    output_dir = tempfile.mkdtemp()
    os.makedirs(os.path.join(output_dir, "Title"))
    with open(os.path.join(output_dir, "Title", filename), "w", encoding="utf-8") as f:
        f.write("subtitle")
    harness = _StartHarness(output_dir, available=available, selected=selected)
    harness.start_download()
    return harness


class TestSubtitleExistencePrecheck(unittest.TestCase):
    def test_manual_vtt_is_recognized(self):
        harness = _run_start_download("Title.en.vtt")
        self.assertFalse(harness.build_called)
        self.assertIn(
            "✓ Subtitle exists: Title.en.vtt",
            [args[0] for args in harness.signals.append_output.emitted],
        )

    def test_auto_vtt_is_recognized(self):
        harness = _run_start_download("Title.a.en.vtt")
        self.assertFalse(harness.build_called)

    def test_skip_path_never_shows_the_cancel_button(self):
        """No download runs on the skip path, so no Cancel button may appear.

        Regression: the button used to be shown before the existence check, so
        "all files already exist" left it visible with nothing to cancel.
        """
        harness = _run_start_download("Title.en.srt")
        self.assertFalse(harness.build_called)
        self.assertNotIn(
            (True,),
            harness.signals.set_cancel_button_visible.emitted,
        )


class TestLanguageLessSubtitlePrecheck(unittest.TestCase):
    """A subtitle stored as "Title.srt" must still count as downloaded.

    Otherwise re-running a single-subtitle video would fetch the media file
    all over again, because "all requested subtitles exist" never holds.
    """

    def test_bare_name_satisfies_the_only_offered_language(self):
        harness = _run_start_download("Title.srt", available={"en": "auto"})
        self.assertFalse(harness.build_called)
        self.assertIn(
            "✓ Subtitle exists: Title.srt",
            [args[0] for args in harness.signals.append_output.emitted],
        )

    def test_bare_name_covers_only_one_of_several_requested_languages(self):
        # The bare file can stand for one requested language, so it is credited
        # to that one - but German is still missing, so the run goes ahead.
        harness = _run_start_download(
            "Title.srt", available={"en": "auto", "de": "real"}, selected=("en", "de")
        )
        self.assertTrue(harness.build_called)
        self.assertIn(
            "✓ Subtitle exists: Title.srt",
            [args[0] for args in harness.signals.append_output.emitted],
        )

    def test_bare_name_is_not_credited_to_an_ambiguous_video(self):
        # German is offered but not requested, so the bare file may be German:
        # crediting it to English would skip the download and leave the wrong
        # language on disk.
        harness = _run_start_download(
            "Title.srt", available={"en": "auto", "de": "real"}
        )
        self.assertTrue(harness.build_called)

    def test_bare_name_does_not_cover_another_language(self):
        harness = _run_start_download(
            "Title.srt", available={"en": "auto"}, selected=("de",)
        )
        self.assertTrue(harness.build_called)

    def test_bare_vtt_is_recognized_too(self):
        # A leftover .vtt (conversion unavailable) uses the same bare name.
        harness = _run_start_download("Title.vtt", available={"en": "real"})
        self.assertFalse(harness.build_called)

    def test_bare_name_is_ignored_without_info_availability(self):
        # No info fetch: the app cannot know which language it holds, so it
        # keeps the old (safe) behavior and re-requests the subtitle.
        harness = _run_start_download("Title.srt", available={})
        self.assertTrue(harness.build_called)


if __name__ == "__main__":
    unittest.main()
