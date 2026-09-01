"""
Isolated tests for the subtitle-aware download progress parsing.

yt-dlp downloads subtitles BEFORE the media streams. These tests replay real
captured yt-dlp output through YTDLPDownloaderGUI._parse_download_output()
and verify that subtitle transfers no longer hijack the video/audio progress
bars, the dock fraction, or the captured media filename.

Run from the repo root:
    python3 -m unittest temp.test_subtitle_progress -v
or from temp/:
    python3 -m unittest test_subtitle_progress -v
"""

import os
import sys
import unittest

# Make the repo root (where app.py lives) importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402


class FakeSignal:
    """Records every emit() call."""

    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class FakeSignals:
    """Stand-in for SignalEmitter (no Qt event loop needed)."""

    def __init__(self):
        for name in (
            "append_output",
            "update_last_line",
            "set_indeterminate",
            "update_download_progress",
            "update_dock_progress",
        ):
            setattr(self, name, FakeSignal())


class ParserHarness:
    """
    Minimal stand-in that reuses the REAL parser methods from
    YTDLPDownloaderGUI without constructing any Qt widget. The methods only
    rely on `self.video_state`, `self.signals` and each other, all provided
    here.
    """

    _is_subtitle_path = app.YTDLPDownloaderGUI._is_subtitle_path
    _parse_download_output = app.YTDLPDownloaderGUI._parse_download_output
    _update_download_progress = app.YTDLPDownloaderGUI._update_download_progress

    def __init__(self, media_type="video"):
        self.video_state = {"media_type": media_type}
        self.signals = FakeSignals()


def make_gui(media_type="video"):
    """Build a lightweight harness with the real parser methods bound."""
    return ParserHarness(media_type)


def make_state(gui):
    """State dict exactly as initialized in run_download()."""
    return {
        "last_line_was_progress": False,
        "merged_filename": None,
        "downloading_subtitles": False,
        "progress_manager": app.DownloadProgressManager(
            media_type=gui.video_state["media_type"]
        ),
    }


def feed(gui, state, lines):
    for line in lines:
        state = gui._parse_download_output(line, state)
    return state


# Real yt-dlp output (2026.08.27) captured for a video + subtitle download.
# Note how the subtitle transfer comes first, before video and audio.
VIDEO_WITH_SUBS_LINES = [
    "[youtube] Extracting URL: https://www.youtube.com/watch?v=jNQXAC9IVRw",
    "[info] jNQXAC9IVRw: Downloading 1 format(s): 395+251",
    "[info] Writing video subtitles to: Me at the zoo [jNQXAC9IVRw].en.srt",
    "[download] Destination: Me at the zoo [jNQXAC9IVRw].en.srt",
    "[download]    416.00B at  Unknown B/s (00:00:00)",
    "[download] 100% of    416.00B in 00:00:00 at 5.07KiB/s",
    "[SubtitlesConvertor] Converting subtitles",
    "[download] Destination: Me at the zoo [jNQXAC9IVRw].f395.mp4",
    "[download]  42.5% of    2.00MiB at    1.00MiB/s ETA 00:01",
    "[download] 100% of    2.00MiB in 00:00:02 at  1.00MiB/s",
    "[download] Destination: Me at the zoo [jNQXAC9IVRw].f251.webm",
    "[download]  60.0% of  500.00KiB at  250.00KiB/s ETA 00:02",
    '[Merger] Merging formats into "Me at the zoo [jNQXAC9IVRw].mp4"',
    "Deleting original file Me at the zoo [jNQXAC9IVRw].f395.mp4 (pass -k to keep)",
]

# Real yt-dlp output (2026.08.31) captured on a flaky connection: the video
# stream hit network timeouts twice. On every "Got error ... Retrying (n/10)"
# yt-dlp RE-PRINTS the "[download] Destination:" line of the file it is
# RETRYING (shown as one terminal row because the chunks are \r-separated,
# which is how the app's universal-newlines reader yields them). Those
# re-prints must not count as the next stream (video -> audio).
RETRY_DEST = "Danny Jones/S26E0831 - Title.f399.mp4"
VIDEO_WITH_RETRIES_LINES = [
    "[youtube] Extracting URL: https://www.youtube.com/watch?v=0SW6vtFmxEg",
    "[info] 0SW6vtFmxEg: Downloading 1 format(s): 399+251",
    f"[download] Destination: {RETRY_DEST}",
    "",
    "[download]  23.2% of  801.55MiB at    1.28MiB/s ETA 08:02",
    "[download] Got error: HTTPSConnectionPool(host='rr5---sn-4g5lznlk.googlevideo.com', port=443): Read timed out.. Retrying (1/10)...",
    f"[download] Destination: {RETRY_DEST}",
    "",
    "[download]  56.5% of  801.55MiB at    1.96MiB/s ETA 02:58",
    "[download] Got error: HTTPSConnectionPool(host='rr5---sn-4g5lznlk.googlevideo.com', port=443): Read timed out.. Retrying (2/10)...",
    f"[download] Destination: {RETRY_DEST}",
    "",
    "[download]  98.4% of  801.55MiB at    2.07MiB/s ETA 00:06",
]


class TestSubtitlePathDetection(unittest.TestCase):
    def setUp(self):
        self.gui = make_gui()

    def test_subtitle_extensions_detected(self):
        for path in (
            "Title.en.srt",
            "Title.a.en.srt",
            "Title.en.vtt",
            "Title.en.vtt.part",
            "Title.en.srv3",
            "Title.en.json3",
            "Title.en.ttml",
            '"Title.en.srt"',
            "/some/dir/Title.en.srt",
        ):
            self.assertTrue(self.gui._is_subtitle_path(path), path)

    def test_media_paths_not_detected(self):
        for path in (
            "Title.f395.mp4",
            "Title.f251.webm",
            "Title.mp4",
            "Title.m4a",
            "Title.mp3",
            "Title.mp4.part",
            "Title.mp4.part-Frag0",
            '"Title.f395.mp4"',
        ):
            self.assertFalse(self.gui._is_subtitle_path(path), path)

    def test_empty_and_none(self):
        self.assertFalse(self.gui._is_subtitle_path(None))
        self.assertFalse(self.gui._is_subtitle_path(""))


class TestVideoWithSubtitles(unittest.TestCase):
    """The reported bug: video + subtitles messed up the progress bars."""

    def setUp(self):
        self.gui = make_gui("video")

    def test_subtitles_do_not_hijack_progress_bars(self):
        state = feed(self.gui, make_state(self.gui), VIDEO_WITH_SUBS_LINES)
        pm = state["progress_manager"]
        # Only the real media downloads may count as destinations
        self.assertEqual(pm.download_count, 2)  # video + audio
        # Video bar filled by the REAL video stream (100%), not the subtitles
        self.assertEqual(pm.video_progress, 1000)
        # Audio bar filled by the REAL audio stream (60%)
        self.assertEqual(pm.audio_progress, 600)

    def test_no_progress_during_subtitle_transfer(self):
        # Everything up to and including the subtitle conversion
        state = feed(self.gui, make_state(self.gui), VIDEO_WITH_SUBS_LINES[:7])
        pm = state["progress_manager"]
        self.assertEqual((pm.video_progress, pm.audio_progress), (0, 0))
        self.assertFalse(pm.is_started())
        # No bar / dock updates, and the spinner must still be running
        self.assertEqual(self.gui.signals.update_download_progress.emitted, [])
        self.assertEqual(self.gui.signals.update_dock_progress.emitted, [])
        self.assertEqual(self.gui.signals.set_indeterminate.emitted, [])
        # Subtitle lines must still be visible in the output log
        logged = [args[0] for args in self.gui.signals.append_output.emitted]
        self.assertTrue(any(".en.srt" in line for line in logged))

    def test_dock_fraction_zero_after_subtitles(self):
        # Before the fix the dock showed ~100% right after the tiny subtitles
        state = feed(self.gui, make_state(self.gui), VIDEO_WITH_SUBS_LINES[:7])
        self.assertEqual(state["progress_manager"].get_combined_fraction(), 0.0)

    def test_video_progress_lands_in_video_bar_after_subs(self):
        # ... up to and including the video 42.5% line
        state = feed(self.gui, make_state(self.gui), VIDEO_WITH_SUBS_LINES[:9])
        pm = state["progress_manager"]
        self.assertEqual((pm.video_progress, pm.audio_progress), (425, 0))
        self.assertEqual(
            self.gui.signals.update_download_progress.emitted[-1], (425, 0)
        )

    def test_media_filename_not_hijacked_by_subtitle(self):
        state = feed(self.gui, make_state(self.gui), VIDEO_WITH_SUBS_LINES)
        self.assertEqual(state["merged_filename"], "Me at the zoo [jNQXAC9IVRw].mp4")


class TestRetryAfterDownloadError(unittest.TestCase):
    """A mid-download network error makes yt-dlp re-print the destination of
    the file it retries; that re-print must not switch video -> audio."""

    def setUp(self):
        self.gui = make_gui("video")

    def test_retry_percentages_stay_in_video_bar(self):
        state = feed(self.gui, make_state(self.gui), VIDEO_WITH_RETRIES_LINES)
        pm = state["progress_manager"]
        # 23.2% → 56.5% → 98.4%: all of the SAME video stream
        self.assertEqual((pm.video_progress, pm.audio_progress), (984, 0))
        # The re-printed destinations are retries, not new files
        self.assertEqual(pm.download_count, 1)
        # Single-stream download: dock fraction is the plain video fraction
        self.assertAlmostEqual(pm.get_combined_fraction(), 0.984)

    def test_progress_signal_tracks_video_stream_through_retries(self):
        feed(self.gui, make_state(self.gui), VIDEO_WITH_RETRIES_LINES)
        self.assertEqual(
            self.gui.signals.update_download_progress.emitted[-1], (984, 0)
        )

    def test_real_audio_destination_after_retries_still_switches(self):
        lines = VIDEO_WITH_RETRIES_LINES + [
            "[download] 100% of  801.55MiB in 00:10:22 at  1.29MiB/s",
            "[download] Destination: Danny Jones/S26E0831 - Title.f251.webm",
            "[download]  40.0% of   12.34MiB at  250.00KiB/s ETA 00:02",
        ]
        state = feed(self.gui, make_state(self.gui), lines)
        pm = state["progress_manager"]
        self.assertEqual((pm.video_progress, pm.audio_progress), (1000, 400))
        self.assertEqual(pm.download_count, 2)

    def test_on_download_destination_ignores_retry_reprint(self):
        pm = app.DownloadProgressManager(media_type="video")
        pm.on_download_destination("/d/Title.f399.mp4")
        self.assertEqual(
            (pm.download_count, pm.active_stream), (1, pm.STREAM_VIDEO)
        )
        # Retry re-print of the same file (quoted, with a transient suffix)
        pm.on_download_destination(' "/d/Title.f399.mp4.part" ')
        self.assertEqual(
            (pm.download_count, pm.active_stream), (1, pm.STREAM_VIDEO)
        )
        # ...so the following percent still lands in the video bar
        pm.update_from_ytdlp_percent(50.0)
        self.assertEqual((pm.video_progress, pm.audio_progress), (500, 0))
        # A genuinely different destination is the next stream (audio)
        pm.on_download_destination("/d/Title.f251.webm")
        self.assertEqual(
            (pm.download_count, pm.active_stream), (2, pm.STREAM_AUDIO)
        )

    def test_no_arg_call_still_counts(self):
        # Backwards compatibility: on_download_destination() without a path
        # (regex failed) behaves like before.
        pm = app.DownloadProgressManager(media_type="video")
        pm.on_download_destination()
        self.assertEqual((pm.download_count, pm.active_stream), (1, pm.STREAM_VIDEO))
        pm.on_download_destination()
        self.assertEqual((pm.download_count, pm.active_stream), (2, pm.STREAM_AUDIO))


class TestVideoOnlyWithSubtitles(unittest.TestCase):
    def test_merged_filename_is_the_video_no_convertor(self):
        # No [SubtitlesConvertor] line when subs are natively srt: the media
        # destination itself must end the subtitle transfer.
        self.gui = make_gui("video_only")
        lines = [
            "[download] Destination: Title.en.srt",
            "[download] 100% of    416.00B in 00:00:00 at 5.07KiB/s",
            "[download] Destination: Title.f395.mp4",
            "[download] 100% of    2.00MiB in 00:00:02 at  1.00MiB/s",
        ]
        state = feed(self.gui, make_state(self.gui), lines)
        self.assertEqual(state["merged_filename"], "Title.f395.mp4")
        self.assertEqual(state["progress_manager"].video_progress, 1000)


class TestAudioWithSubtitles(unittest.TestCase):
    def test_audio_bar_gets_audio_progress(self):
        self.gui = make_gui("audio")
        lines = [
            "[info] Writing video subtitles to: Title.en.srt",
            "[download] Destination: Title.en.srt",
            "[download] 100% of    416.00B in 00:00:00 at 5.07KiB/s",
            "[download] Destination: Title.f251.webm",
            "[download]  55.0% of  500.00KiB at  250.00KiB/s ETA 00:01",
            "[ExtractAudio] Destination: Title.m4a",
        ]
        state = feed(self.gui, make_state(self.gui), lines)
        pm = state["progress_manager"]
        self.assertEqual((pm.video_progress, pm.audio_progress), (0, 550))
        self.assertEqual(state["merged_filename"], "Title.m4a")


class TestSubtitlesOnlyMode(unittest.TestCase):
    def test_bars_untouched_during_subtitle_downloads(self):
        self.gui = make_gui("subtitles")
        lines = [
            "[info] Writing video subtitles to: Title.en.srt",
            "[download] Destination: Title.en.srt",
            "[download] 100% of    416.00B in 00:00:00 at 5.07KiB/s",
            "[download] Destination: Title.de.srt",
            "[download] 100% of    512.00B in 00:00:00 at 5.07KiB/s",
        ]
        state = feed(self.gui, make_state(self.gui), lines)
        pm = state["progress_manager"]
        self.assertEqual((pm.video_progress, pm.audio_progress), (0, 0))
        self.assertFalse(pm.is_started())
        self.assertEqual(self.gui.signals.update_download_progress.emitted, [])
        self.assertEqual(self.gui.signals.update_dock_progress.emitted, [])
        # ... but everything is still logged (consecutive progress lines are
        # collapsed into the last output line via update_last_line)
        logged = [args[0] for args in self.gui.signals.append_output.emitted]
        logged += [args[0] for args in self.gui.signals.update_last_line.emitted]
        for line in lines:
            self.assertIn(line, logged)


class TestAlreadyDownloaded(unittest.TestCase):
    def test_subtitle_already_line_alone_does_not_start(self):
        self.gui = make_gui("video")
        state = feed(
            self.gui,
            make_state(self.gui),
            ['[download] "Title.en.srt" has already been downloaded'],
        )
        self.assertFalse(state["progress_manager"].is_started())
        self.assertIsNone(state["merged_filename"])

    def test_media_already_downloaded_with_missing_subs(self):
        self.gui = make_gui("video")
        lines = [
            '[download] "Title.en.srt" has already been downloaded',
            '[download] "Title.f395.mp4" has already been downloaded',
        ]
        state = feed(self.gui, make_state(self.gui), lines)
        self.assertEqual(state["merged_filename"], "Title.f395.mp4")

    def test_subs_present_media_downloads_normally(self):
        self.gui = make_gui("video")
        lines = [
            '[download] "Title.en.srt" has already been downloaded',
            "[download] Destination: Title.f395.mp4",
            "[download]  30.0% of    2.00MiB at    1.00MiB/s ETA 00:01",
        ]
        state = feed(self.gui, make_state(self.gui), lines)
        pm = state["progress_manager"]
        self.assertEqual((pm.video_progress, pm.audio_progress), (300, 0))
        self.assertEqual(state["merged_filename"], "Title.f395.mp4")


class TestCarriageReturnChunks(unittest.TestCase):
    def test_cr_chunked_progress(self):
        # yt-dlp separates progress updates with "\r"; universal newlines
        # yields an EMPTY line between the chunks, which must not end the
        # subtitle transfer (regression test for the real captured output).
        self.gui = make_gui("video")
        raw = (
            "[download] Destination: Title.en.srt\n"
            "\r[download]    416.00B at  248.75KiB/s (00:00:00)\r"
            "[download] 100% of    416.00B in 00:00:00 at 5.91KiB/s\n"
            "[SubtitlesConvertor] Converting subtitles\n"
            "[download] Destination: Title.f395.mp4\n"
            "\r[download]  50.0% of    2.00MiB at    1.00MiB/s\r"
            "[download] 100% of    2.00MiB in 00:00:02\n"
        )
        state = feed(self.gui, make_state(self.gui), raw.splitlines())
        pm = state["progress_manager"]
        # Only the REAL video download may fill the video bar
        self.assertEqual((pm.video_progress, pm.audio_progress), (1000, 0))
        self.assertEqual(pm.download_count, 1)
        self.assertEqual(state["merged_filename"], "Title.f395.mp4")


class FakeCheckbox:
    """Minimal QCheckBox/QPushButton stand-in (no Qt event loop needed)."""

    def __init__(self, text=""):
        self._text = text
        self.enabled = True
        self.checked = False

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)

    def isEnabled(self):
        return self.enabled

    def setChecked(self, checked):
        self.checked = bool(checked)

    def isChecked(self):
        return self.checked


class UiHarness:
    """
    Minimal stand-in that reuses the REAL checkbox/UI-state methods from
    YTDLPDownloaderGUI without constructing any Qt widget.
    """

    _update_subtitle_checkboxes = app.YTDLPDownloaderGUI._update_subtitle_checkboxes
    _auto_select_subtitles = app.YTDLPDownloaderGUI._auto_select_subtitles
    _set_ui_enabled_state = app.YTDLPDownloaderGUI._set_ui_enabled_state

    def __init__(self, media_type="video"):
        self.video_state = {"media_type": media_type, "available_subtitles": {}}
        self.subtitle_unavailable = set()
        self.subtitle_checkboxes = {
            code: FakeCheckbox(f"{name} ({code})")
            for name, code in (("English", "en"), ("German", "de"), ("Spanish", "es"))
        }
        # Primary controls (only setEnabled/isChecked is used)
        self.url_entry = FakeCheckbox()
        self.paste_button = FakeCheckbox()
        self.title_entry = FakeCheckbox()
        self.clean_button = FakeCheckbox()
        self.output_dir_entry = FakeCheckbox()
        self.browse_button = FakeCheckbox()
        self.download_button = FakeCheckbox()
        self.thumbnail_button = FakeCheckbox()
        self.subtitles_checkbox = FakeCheckbox()
        self.sb_all_checkbox = FakeCheckbox("All")
        self.sb_checkbox_map = {FakeCheckbox("Sponsor"): "sponsor"}
        self.option_group_map = [{"buttons": [{"button": FakeCheckbox()}], "enabled": True}]


class TestSubtitleCheckboxDisable(unittest.TestCase):
    """Languages reported as "(none)" must keep their checkbox disabled."""

    def setUp(self):
        self.gui = UiHarness("video")

    def test_none_languages_disabled_after_fetch(self):
        self.gui.video_state["available_subtitles"] = {"en": "real"}
        self.gui._update_subtitle_checkboxes("en")
        self.assertTrue(self.gui.subtitle_checkboxes["en"].isEnabled())
        self.assertEqual(self.gui.subtitle_checkboxes["en"].text(), "English (real)")
        for code in ("de", "es"):
            cb = self.gui.subtitle_checkboxes[code]
            self.assertFalse(cb.isEnabled(), code)
            self.assertTrue(cb.text().endswith("(none)"), code)
            self.assertFalse(cb.checked, code)
        self.assertEqual(self.gui.subtitle_unavailable, {"de", "es"})

    def test_none_stays_disabled_after_ui_reenable(self):
        # title_fetch_finished() / enable_all_controls() re-enable the whole
        # UI after every fetch/download; "(none)" languages must stay disabled
        self.gui.video_state["available_subtitles"] = {"en": "real"}
        self.gui._update_subtitle_checkboxes("en")
        self.gui._set_ui_enabled_state(True)
        self.assertTrue(self.gui.subtitle_checkboxes["en"].isEnabled())
        self.assertFalse(self.gui.subtitle_checkboxes["de"].isEnabled())
        self.assertFalse(self.gui.subtitle_checkboxes["es"].isEnabled())

    def test_all_disabled_while_ui_locked(self):
        self.gui.video_state["available_subtitles"] = {"en": "real"}
        self.gui._update_subtitle_checkboxes("en")
        self.gui._set_ui_enabled_state(False)
        for code, cb in self.gui.subtitle_checkboxes.items():
            self.assertFalse(cb.isEnabled(), code)

    def test_availability_recovers_on_next_fetch(self):
        self.gui.video_state["available_subtitles"] = {"en": "real"}
        self.gui._update_subtitle_checkboxes("en")
        # Second fetch: de became available, en disappeared
        self.gui.video_state["available_subtitles"] = {"de": "auto"}
        self.gui._update_subtitle_checkboxes("de")
        self.assertTrue(self.gui.subtitle_checkboxes["de"].isEnabled())
        self.assertEqual(self.gui.subtitle_checkboxes["de"].text(), "German (auto)")
        self.assertFalse(self.gui.subtitle_checkboxes["en"].isEnabled())
        self.assertEqual(self.gui.subtitle_checkboxes["en"].text(), "English (none)")
        self.assertEqual(self.gui.subtitle_unavailable, {"en", "es"})

    def test_previously_checked_language_unchecked_when_none(self):
        self.gui.subtitle_checkboxes["de"].setChecked(True)
        self.gui.video_state["available_subtitles"] = {"en": "real"}
        self.gui._update_subtitle_checkboxes("en")
        self.assertFalse(self.gui.subtitle_checkboxes["de"].checked)

    def test_auto_select_ignores_none_languages(self):
        gui = UiHarness("subtitles")
        gui.video_state["available_subtitles"] = {"en": "real"}
        gui._update_subtitle_checkboxes("en")
        self.assertTrue(gui.subtitle_checkboxes["en"].checked)
        self.assertFalse(gui.subtitle_checkboxes["de"].checked)
        self.assertFalse(gui.subtitle_checkboxes["es"].checked)

    def test_real_subtitles_checked_with_priority_over_auto(self):
        # If any language has "(real)" subtitles, only those are checked —
        # even when another language only offers "(auto)" captions.
        self.gui.video_state["available_subtitles"] = {"en": "real", "de": "auto"}
        self.gui._update_subtitle_checkboxes("en")
        self.assertTrue(self.gui.subtitle_checkboxes["en"].checked)
        self.assertFalse(self.gui.subtitle_checkboxes["de"].checked)
        self.assertTrue(self.gui.subtitle_checkboxes["de"].text().endswith("(auto)"))

    def test_auto_fallback_checked_when_no_real_exists(self):
        # Without "(real)" subtitles, "(auto)" languages are checked instead.
        self.gui.video_state["available_subtitles"] = {"de": "auto"}
        self.gui._update_subtitle_checkboxes("de")
        self.assertTrue(self.gui.subtitle_checkboxes["de"].checked)
        self.assertFalse(self.gui.subtitle_checkboxes["en"].checked)
        self.assertFalse(self.gui.subtitle_checkboxes["es"].checked)

    def test_stale_auto_unchecked_when_real_appears(self):
        # A checkbox left checked from a previous video's "(auto)" fallback
        # is unchecked when a newer fetch reports "(real)" elsewhere.
        self.gui.subtitle_checkboxes["de"].setChecked(True)
        self.gui.video_state["available_subtitles"] = {"en": "real", "de": "auto"}
        self.gui._update_subtitle_checkboxes("en")
        self.assertTrue(self.gui.subtitle_checkboxes["en"].checked)
        self.assertFalse(self.gui.subtitle_checkboxes["de"].checked)


if __name__ == "__main__":
    unittest.main(verbosity=2)


