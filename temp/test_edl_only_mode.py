"""Tests for the ".edl only" download mode wired into the app (ytdl/download.py).

The prototype in temp/sponsorblock_standalone.py proved the idea; these tests
pin the integration:

- the Type group declares a ".edl only" radio whose value is EDL_ONLY_MEDIA_TYPE
- start_download() branches on it right after the shared prelude (title gate,
  directory creation, update_video_state) — BEFORE build_command(), so the
  harnesses here have no build_command/yt_dlp_bin at all: reaching the yt-dlp
  path would raise AttributeError, which is itself the test
- start_edl_only() disables the UI and hands the work to a daemon worker
  (the Cancel button stays hidden — there is no process to kill)
- run_edl_only() writes <title>.edl, re-enables the UI and puts a verdict on
  the button: "EDL Created ✅" / "EDL Error 🚨" (failure) / "No EDL Written"
  (nothing matched) — create_edl_only()'s tri-state return decides
- create_edl_only() prefers the sb_bar's cached segments (zero HTTP after a
  normal info fetch) and falls back to one direct API request when empty
- the written bytes are IDENTICAL to the prototype's writer
- the EDL name is <base_filename>.edl verbatim — a dotted title suffix
  ("Lecture 1.5") is kept, not eaten as a file extension — and the Hook
  checkbox matches the API's real "hook" category (which the app's own
  getSegments request must include, or the API never returns those segments)

No network (fetch is stubbed and forbidden-by-default), no Qt event loop,
no yt-dlp.

Run from the repo root:
    python3 -m unittest temp.test_edl_only_mode -v
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from temp import sponsorblock_standalone as sb
from ytdl.download import (
    DownloadMixin,
    EDL_ONLY_MEDIA_TYPE,
    SB_ALL_API_CATEGORIES,
    build_edl_content,
    fetch_sponsorblock_segments,
    to_ui_category,
    write_edl,
)
from ytdl.ui_build import UiBuildMixin


def seg(category, start, end):
    """One raw API segment object (same shape the bar stores)."""
    return {"category": category, "segment": [start, end], "UUID": "u-" + category}


def forbidden_fetch(video_id, *args, **kwargs):
    raise AssertionError(f"HTTP fetch despite cached bar segments ({video_id})")


class _Entry:
    """QLineEdit stand-in."""

    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text


class _Button:
    """QPushButton stand-in: records setText/setEnabled."""

    def __init__(self):
        self.text_log = []
        self.enabled_log = []

    def setText(self, text):  # noqa: N802 - mirrors Qt
        self.text_log.append(text)

    def setEnabled(self, enabled):  # noqa: N802 - mirrors Qt
        self.enabled_log.append(enabled)


class _Signal:
    """Signal stand-in: records emissions."""

    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _Signals:
    """The signals the .edl-only flow touches, all recording."""

    def __init__(self):
        self.append_output = _Signal()
        self.update_dock_tile = _Signal()
        self.set_cancel_button_visible = _Signal()


class _Bar:
    """Stand-in for SponsorBlockBar: only .segments is read."""

    def __init__(self, segments):
        self.segments = list(segments)


class _EdlHarness:
    """Real .edl-only methods on a GUI stand-in (no Qt, no yt-dlp).

    Deliberately has NO build_command, run_download, yt_dlp_bin or
    Popen-reachables: touching the download path raises AttributeError,
    which is the test. The default fetch is forbidden (raises); pass one
    explicitly to exercise the API fallback.
    """

    start_edl_only = DownloadMixin.start_edl_only
    run_edl_only = DownloadMixin.run_edl_only
    get_full_path = DownloadMixin.get_full_path

    def __init__(self, output_dir, base_filename="Video",
                 video_id="yePLuwL3FbM", site="youtube",
                 categories=("sponsor",), bar_segments=(), fetch=None):
        self.video_state = {
            "base_filename": base_filename,
            "output_dir": output_dir,
            "video_id": video_id,
            "site": site,
            "url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
            "media_type": EDL_ONLY_MEDIA_TYPE,
            "is_download_running": True,
        }
        self.sb_bar = _Bar(bar_segments)
        self._categories = list(categories)
        self._fetch = fetch or forbidden_fetch
        self.signals = _Signals()
        self.download_button = _Button()
        self.emitted = []          # every (name, args) that went through _emit
        self.ui_enabled_log = []
        self.busy_log = []
        self.cached_video_metadata = "stale"
        self._cancelled = True     # start_edl_only must reset it

    def create_edl_only(self, fetch=None):
        """Real body; the default fetch is the harness's (forbidden)."""
        return DownloadMixin.create_edl_only(
            self, fetch if fetch is not None else self._fetch
        )

    # --- tiny GUI one-liners, replicated instead of imported (no Qt needed)
    def update_video_state(self, **kwargs):
        # Mirrors YTDLPDownloaderGUI.update_video_state (ytdl/app.py): a new
        # title also becomes base_filename, which get_full_path() reads.
        self.video_state.update(kwargs)
        if "title" in kwargs:
            self.video_state["base_filename"] = kwargs["title"]

    def set_download_button_status(self, _status):
        pass

    # --- real DownloadMixin/GUI-shaped helpers the flow reaches ---
    def get_output_dir(self):
        return self.video_state.get("output_dir", "")

    def get_selected_sb_categories(self):
        return list(self._categories)

    def get_clean_url(self):
        return self.video_state.get("url", "")

    def clearDockProgress(self):
        pass

    def _set_ui_enabled_state(self, enabled):
        self.ui_enabled_log.append(enabled)

    def _reset_download_progress_bars(self):
        pass

    def _set_download_busy(self, busy):
        self.busy_log.append(busy)

    def _emit(self, name, *args):
        self.emitted.append((name, args))
        if name == "append_output":
            self.signals.append_output.emit(*args)

    # --- assertion helpers ---
    def emitted_last(self, name):
        values = [args for n, args in self.emitted if n == name]
        return values[-1] if values else None

    def output_lines(self):
        # _Signal stores the *args tuple; the panel lines are single strings.
        return [args[0] for args in self.signals.append_output.emitted if args]


class _StartHarness:
    """Real start_download() up to (and including) the .edl-only branch.

    No build_command, no get_selected_subtitle_codes: any media type other
    than .edl_only falls off the prelude into AttributeError, which proves
    the branch is specific to EDL_ONLY_MEDIA_TYPE.
    """

    start_download = DownloadMixin.start_download
    get_full_path = DownloadMixin.get_full_path

    def __init__(self, tmp, media_type, channel="PowerfulJRE",
                 title="Ep 12 - The Big One"):
        self.video_state = {
            "channel": channel,
            "media_type": media_type,
            "video_id": "yePLuwL3FbM",
            "site": "youtube",
            "url": "https://www.youtube.com/watch?v=yePLuwL3FbM",
        }
        self.title_entry = _Entry(title)
        self.output_dir_entry = _Entry(tmp)
        self.download_button = _Button()
        self.signals = _Signals()
        self.edl_only_calls = []

    def update_video_state(self, **kwargs):
        # Same title -> base_filename mapping as ytdl/app.py's method.
        self.video_state.update(kwargs)
        if "title" in kwargs:
            self.video_state["base_filename"] = kwargs["title"]

    def set_download_button_status(self, _status):
        pass

    def start_edl_only(self, channel, base_name):
        self.edl_only_calls.append((channel, base_name))


class TestRadioAndConstant(unittest.TestCase):
    """The mode is a media_type radio, not a button, and both sides agree."""

    def test_media_type_value(self):
        self.assertEqual(EDL_ONLY_MEDIA_TYPE, "edl_only")
        # Single source of truth against the prototype's copy.
        self.assertEqual(sb.EDL_ONLY_MEDIA_TYPE, EDL_ONLY_MEDIA_TYPE)

    def test_ui_declares_the_edl_only_radio(self):
        """The Type group has the radio wired to the same value."""
        ui_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "ytdl", "ui_build.py",
        )
        with open(ui_path, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('"text": ".edl only"', source)
        self.assertIn('"value": "edl_only"', source)
        self.assertIn('"attr": "edl_only_radio"', source)


class _FakeRadio:
    """QRadioButton stand-in for _on_option_group_change (identity + text)."""

    def __init__(self, text, group):
        self._text = text
        self._group = group

    def group(self):
        return self._group

    def text(self):
        return self._text


class TestMediaTypeClickResolution(unittest.TestCase):
    """Clicking a Type radio stores ITS value — resolved by button identity.

    Regression: "Video + Audio" and "Video only" share the first-word map
    key "Video", so the text-based lookup in _on_option_group_change stored
    whichever button the group registered last — clicking "Video + Audio"
    set media_type to "video_only" and silently asked for a video-only
    stream. Verified live with Qt before the fix; the identity lookup
    cannot collide whatever the button texts are.
    """

    TYPE_BUTTONS = [
        ("Video + Audio", "video"),
        ("Video only", "video_only"),
        ("Audio only", "audio"),
        ("Subtitles only", "subtitles"),
        (".edl only", EDL_ONLY_MEDIA_TYPE),
    ]

    def _gui(self):
        class _Gui:  # pylint: disable=too-few-public-methods
            pass

        gui = _Gui()
        gui.video_state = {}
        gui.auto_selected = []
        gui._auto_select_subtitles = lambda: gui.auto_selected.append(True)
        return gui

    def _config_for(self, group):
        buttons = [
            {"button": _FakeRadio(text, group), "enabled": True, "value": value}
            for text, value in self.TYPE_BUTTONS
        ]
        # The colliding first-word map, exactly as _create_radio_group builds it.
        button_map = {}
        for entry in buttons:
            key = entry["button"].text().split(" ")[0].strip()
            button_map[key] = entry["value"]
        return {
            "group": group,
            "attr": "media_type",
            "map": button_map,
            "enabled": True,
            "buttons": buttons,
        }

    def _click(self, gui, config, index):
        UiBuildMixin._on_option_group_change(  # pylint: disable=protected-access
            gui, config["buttons"][index]["button"]
        )

    def test_every_type_button_stores_its_own_value(self):
        gui = self._gui()
        group = object()
        config = self._config_for(group)
        gui.option_group_map = [config]
        for index, (_text, expected) in enumerate(self.TYPE_BUTTONS):
            self._click(gui, config, index)
            self.assertEqual(gui.video_state["media_type"], expected,
                             f"button index {index}")

    def test_video_plus_audio_does_not_collide_with_video_only(self):
        """The exact collision, called out on its own."""
        gui = self._gui()
        config = self._config_for(object())
        gui.option_group_map = [config]
        self._click(gui, config, 0)  # "Video + Audio"
        self.assertEqual(gui.video_state["media_type"], "video")

    def test_text_fallback_when_button_is_not_in_the_config(self):
        gui = self._gui()
        group = object()
        gui.option_group_map = [
            # Keyed by the first *word* — "1080p" has no space to split on;
            # the map's VALUE is what does the p-stripping.
            {"group": group, "attr": "quality", "map": {"1080p": "1080"},
             "enabled": True, "buttons": []},
        ]
        UiBuildMixin._on_option_group_change(  # pylint: disable=protected-access
            gui, _FakeRadio("1080p", group)
        )
        self.assertEqual(gui.video_state["quality"], "1080")

    def test_subtitles_only_still_auto_selects(self):
        gui = self._gui()
        config = self._config_for(object())
        gui.option_group_map = [config]
        self._click(gui, config, 3)  # "Subtitles only"
        self.assertEqual(gui.auto_selected, [True])


class TestStartDownloadBranch(unittest.TestCase):
    """start_download() branches on the media type before build_command()."""

    def test_branches_after_the_shared_prelude(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _StartHarness(tmp, EDL_ONLY_MEDIA_TYPE)
            h.start_download()
            # The prelude ran: channel/title directory created, state updated.
            expected_dir = os.path.join(tmp, "PowerfulJRE", "Ep 12 - The Big One")
            self.assertTrue(os.path.isdir(expected_dir), expected_dir)
            self.assertEqual(h.video_state.get("base_filename"),
                             "Ep 12 - The Big One")
            self.assertEqual(h.video_state.get("output_dir"), expected_dir)
            self.assertTrue(h.video_state.get("is_download_running"))
            # And the branch handed off — build_command was never reached
            # (this harness has none; AttributeError would have been raised).
            self.assertEqual(h.edl_only_calls,
                             [("PowerfulJRE", "Ep 12 - The Big One")])

    def test_other_media_types_do_not_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _StartHarness(tmp, "video")
            with self.assertRaises(AttributeError):
                h.start_download()  # falls off the prelude into the missing
                # subtitle/download machinery — i.e. it did NOT branch
            self.assertEqual(h.edl_only_calls, [])


class _FakeThread:
    """Records construction; start() does nothing (no real thread)."""

    instances = []

    def __init__(self, target=None, args=(), **_kwargs):
        self.target = target
        self.args = args
        self.daemon = False
        self.started = False
        self.__class__.instances.append(self)

    def start(self):
        self.started = True


class TestStartEdlOnly(unittest.TestCase):
    """The main-thread entry mirrors the download pre-thread and spawns once."""

    def setUp(self):
        _FakeThread.instances = []

    def test_disables_ui_and_spawns_daemon_worker(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("ytdl.download.threading.Thread", _FakeThread):
            h = _EdlHarness(tmp)
            h.start_edl_only("PowerfulJRE", "Video")
            self.assertEqual(h.ui_enabled_log, [False])
            self.assertEqual(h.busy_log, [True])
            self.assertIsNone(h.cached_video_metadata)
            self.assertFalse(h._cancelled)
            self.assertTrue(
                any("no download" in line for line in h.output_lines())
            )
            # Exactly one worker, daemon, running run_edl_only.
            self.assertEqual(len(_FakeThread.instances), 1)
            thread = _FakeThread.instances[0]
            self.assertEqual(thread.target, h.run_edl_only)
            self.assertTrue(thread.daemon)
            self.assertTrue(thread.started)
            # No Cancel button: there is no process to kill.
            self.assertEqual(h.signals.set_cancel_button_visible.emitted, [])


class TestRunEdlOnly(unittest.TestCase):
    """run_edl_only(): the file, the button verdict, the UI re-enable."""

    def test_success_writes_edl_and_reenables_ui(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _EdlHarness(tmp, bar_segments=[seg("sponsor", 1, 9)])
            h.run_edl_only()
            path = os.path.join(tmp, "Video.edl")
            self.assertTrue(os.path.isfile(path), path)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "1 9 0\n")
            self.assertEqual(h.emitted_last("set_download_button_label"),
                             ("EDL Created ✅",))
            self.assertEqual(h.emitted_last("set_download_button_status"),
                             ("success",))
            self.assertIn(("enable_button", ()), h.emitted)
            self.assertIn(("set_indeterminate", (False,)), h.emitted)
            self.assertFalse(h.video_state["is_download_running"])

    def test_fetch_error_reports_and_reenables(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _EdlHarness(tmp, bar_segments=[],
                            fetch=lambda vid: ([], "HTTP 500"))
            h.run_edl_only()  # must not raise
            self.assertEqual(h.emitted_last("set_download_button_label"),
                             ("EDL Error 🚨",))
            self.assertEqual(h.emitted_last("set_download_button_status"),
                             ("error",))
            self.assertIn(("enable_button", ()), h.emitted)
            self.assertFalse(h.video_state["is_download_running"])
            self.assertFalse(os.path.exists(os.path.join(tmp, "Video.edl")))

    def test_no_matching_segments_is_neutral(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _EdlHarness(tmp, categories=("intro",),
                            bar_segments=[seg("sponsor", 1, 9)])
            h.run_edl_only()
            self.assertEqual(h.emitted_last("set_download_button_label"),
                             ("No EDL Written",))
            self.assertEqual(h.emitted_last("set_download_button_status"), ("",))
            self.assertIn(("enable_button", ()), h.emitted)
            self.assertFalse(os.path.exists(os.path.join(tmp, "Video.edl")))

    def test_unexpected_exception_still_reenables_ui(self):
        with tempfile.TemporaryDirectory() as tmp:
            def boom(_vid):
                raise RuntimeError("kaboom")

            h = _EdlHarness(tmp, bar_segments=[], fetch=boom)
            h.run_edl_only()  # must not raise
            self.assertEqual(h.emitted_last("set_download_button_label"),
                             ("EDL Error 🚨",))
            self.assertIn(("enable_button", ()), h.emitted)
            self.assertFalse(h.video_state["is_download_running"])


class TestCreateEdlOnly(unittest.TestCase):
    """Segment source priority, gates, and the tri-state return."""

    def test_bar_segments_never_hit_the_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _EdlHarness(
                tmp, categories=("sponsor", "ending"),
                bar_segments=[seg("sponsor", 1, 9), seg("outro", 20, 30)],
            )
            applied = h.create_edl_only(fetch=forbidden_fetch)
            self.assertEqual(len(applied), 2)  # no HTTP, both kept
            with open(os.path.join(tmp, "Video.edl"), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "1 9 0\n20 30 0\n")

    def test_falls_back_to_api_when_bar_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _EdlHarness(tmp, bar_segments=[])
            calls = []

            def fake_fetch(video_id):
                calls.append(video_id)
                return [seg("sponsor", 5, 15)], None

            applied = h.create_edl_only(fetch=fake_fetch)
            self.assertEqual(calls, ["yePLuwL3FbM"])
            self.assertEqual(len(applied), 1)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "Video.edl")))

    def test_non_youtube_site_skips_without_fetching(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _EdlHarness(tmp, site="rumble",
                            bar_segments=[seg("sponsor", 1, 9)])
            applied = h.create_edl_only(fetch=forbidden_fetch)
            self.assertIsNone(applied)
            self.assertEqual(os.listdir(tmp), [])
            self.assertTrue(any("YouTube only" in line
                                for line in h.output_lines()))

    def test_missing_video_id_reports_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _EdlHarness(tmp, video_id="")
            applied = h.create_edl_only(fetch=forbidden_fetch)
            self.assertIsNone(applied)
            self.assertTrue(any("video ID" in line for line in h.output_lines()))

    def test_video_id_is_extracted_from_url_when_state_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _EdlHarness(tmp, video_id="jNQXAC9IVRw", bar_segments=[])
            h.video_state["video_id"] = ""
            h.video_state["url"] = "https://youtu.be/jNQXAC9IVRw"
            calls = []

            def fake_fetch(video_id):
                calls.append(video_id)
                return [seg("sponsor", 1, 2)], None

            h.create_edl_only(fetch=fake_fetch)
            self.assertEqual(calls, ["jNQXAC9IVRw"])

    def test_whole_video_marker_alone_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _EdlHarness(tmp, bar_segments=[seg("sponsor", 0, 0)])
            applied = h.create_edl_only(fetch=forbidden_fetch)
            self.assertEqual(applied, [])  # deliberate no-op, not a failure
            self.assertEqual(os.listdir(tmp), [])
            self.assertTrue(any("No segments to skip" in line
                                for line in h.output_lines()))

    def test_empty_title_is_a_failure(self):
        h = _EdlHarness(".", base_filename="",
                        bar_segments=[seg("sponsor", 1, 9)])
        applied = h.create_edl_only(fetch=forbidden_fetch)
        self.assertIsNone(applied)
        self.assertTrue(any("No video title" in line
                            for line in h.output_lines()))

    def test_outro_is_selected_by_the_ending_checkbox(self):
        """The UI naming is translated here too — same fix as create_edl_file."""
        self.assertEqual(to_ui_category("outro"), "ending")
        with tempfile.TemporaryDirectory() as tmp:
            h = _EdlHarness(tmp, categories=("ending",),
                            bar_segments=[seg("outro", 5, 15)])
            applied = h.create_edl_only(fetch=forbidden_fetch)
            self.assertEqual(len(applied), 1)

    def test_dotted_title_keeps_suffix_in_edl_name(self):
        """base_filename is extensionless — ".5" is a title suffix, not a
        file extension (regression: splitext() used to eat it and write
        "Lecture 1.edl", which no player would find next to the media)."""
        with tempfile.TemporaryDirectory() as tmp:
            h = _EdlHarness(tmp, base_filename="Lecture 1.5",
                            categories=("sponsor",),
                            bar_segments=[seg("sponsor", 1, 9)])
            applied = h.create_edl_only(fetch=forbidden_fetch)
            self.assertEqual(len(applied), 1)
            self.assertTrue(
                os.path.isfile(os.path.join(tmp, "Lecture 1.5.edl")))
            self.assertFalse(os.path.exists(os.path.join(tmp, "Lecture 1.edl")))
            with open(os.path.join(tmp, "Lecture 1.5.edl"),
                      encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "1 9 0\n")

    def test_hook_checkbox_matches_hook_api_segment(self):
        """"hook" is a real SponsorBlock API category (upstream, yt-dlp PR
        #14845); the UI's Hook checkbox speaks the same name. The request
        must include it, or the API never returns hook segments to select."""
        self.assertIn("hook", SB_ALL_API_CATEGORIES)
        self.assertEqual(to_ui_category("hook"), "hook")  # no rename involved
        with tempfile.TemporaryDirectory() as tmp:
            h = _EdlHarness(tmp, categories=("hook",),
                            bar_segments=[seg("hook", 3, 11)])
            applied = h.create_edl_only(fetch=forbidden_fetch)
            self.assertEqual(len(applied), 1)
            with open(os.path.join(tmp, "Video.edl"),
                      encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "3 11 0\n")


class TestParityWithPrototype(unittest.TestCase):
    """The app's .edl bytes must be IDENTICAL to the prototype writer's."""

    def test_identical_bytes(self):
        segments = [
            seg("sponsor", 1.5, 9.25),
            seg("outro", 20, 30),        # selected via "ending"
            seg("intro", 0, 0),          # zero-length: dropped by both
            seg("filler", 40, 50),       # not selected
        ]
        selected = ["sponsor", "ending", "intro"]
        with tempfile.TemporaryDirectory() as tmp_app, \
                tempfile.TemporaryDirectory() as tmp_sb:
            h = _EdlHarness(tmp_app, categories=selected, bar_segments=segments)
            h.create_edl_only(fetch=forbidden_fetch)
            app_path = os.path.join(tmp_app, "Video.edl")
            sb.write_edl(os.path.join(tmp_sb, "Video"),
                         sb.select_segments(segments, selected))
            sb_path = os.path.join(tmp_sb, "Video.edl")
            with open(app_path, encoding="utf-8") as app_file, \
                    open(sb_path, encoding="utf-8") as sb_file:
                app_content, sb_content = app_file.read(), sb_file.read()
        self.assertEqual(app_content, sb_content)
        self.assertEqual(app_content, build_edl_content(
            sb.select_segments(segments, selected)
        ))
        self.assertEqual(app_content, "1.5 9.25 0\n20 30 0\n")


class TestWriteEdl(unittest.TestCase):
    """write_edl() writes exactly where the caller says — no splitext()."""

    def test_path_is_used_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "Lecture 1.5.edl")
            path = write_edl(target, [seg("sponsor", 2, 6)])
            self.assertEqual(path, target)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "2 6 0\n")

    def test_empty_selection_writes_empty_file(self):
        # The caller decides emptiness; the writer just renders.
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "Video.edl")
            write_edl(target, [])
            with open(target, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "")


class TestFetchHelper(unittest.TestCase):
    """fetch_sponsorblock_segments(): every failure shape is (segments, error)."""

    class _Response:
        def __init__(self, status_code=200, payload=None, bad_json=False):
            self.status_code = status_code
            self._payload = payload if payload is not None else []
            self._bad_json = bad_json

        def json(self):
            if self._bad_json:
                raise ValueError("no JSON here")
            return self._payload

    class _Requests:
        class exceptions:
            RequestException = OSError

        def __init__(self, response=None, exc=None):
            self.response = response or TestFetchHelper._Response()
            self.exc = exc
            self.calls = []

        def get(self, url, params=None, timeout=None):
            self.calls.append({"url": url, "params": params, "timeout": timeout})
            if self.exc is not None:
                raise self.exc
            return self.response

    def _with_requests(self, stub):
        return mock.patch("ytdl.download.requests", stub)

    def test_success(self):
        stub = self._Requests(self._Response(payload=[seg("sponsor", 1, 2)]))
        with self._with_requests(stub):
            segments, error = fetch_sponsorblock_segments("abc123")
        self.assertIsNone(error)
        self.assertEqual(len(segments), 1)
        self.assertEqual(stub.calls[0]["params"]["videoID"], "abc123")
        self.assertIn("sponsor", stub.calls[0]["params"]["category"])

    def test_404_is_clean_empty(self):
        stub = self._Requests(self._Response(status_code=404))
        with self._with_requests(stub):
            self.assertEqual(fetch_sponsorblock_segments("abc123"), ([], None))

    def test_http_error(self):
        stub = self._Requests(self._Response(status_code=500))
        with self._with_requests(stub):
            segments, error = fetch_sponsorblock_segments("abc123")
        self.assertEqual(segments, [])
        self.assertIn("500", error)

    def test_network_error(self):
        stub = self._Requests(exc=OSError("boom"))
        with self._with_requests(stub):
            segments, error = fetch_sponsorblock_segments("abc123")
        self.assertEqual(segments, [])
        self.assertIn("boom", error)

    def test_bad_json(self):
        stub = self._Requests(self._Response(bad_json=True))
        with self._with_requests(stub):
            segments, error = fetch_sponsorblock_segments("abc123")
        self.assertEqual(segments, [])
        self.assertIn("JSON", error)

    def test_no_video_id(self):
        segments, error = fetch_sponsorblock_segments("")
        self.assertEqual(segments, [])
        self.assertIn("no video id", error)


if __name__ == "__main__":
    unittest.main()



