"""Tests for the standalone SponsorBlock fetch + EDL prototype (temp/ only).

Proves the whole "SponsorBlock without yt-dlp" idea offline:

- the video id can be extracted from a YouTube URL by the site profiles alone
- one GET to https://sponsor.ajay.app/api/skipSegments carries videoID + the
  category set, and every failure shape (404, 5xx, network, bad JSON) turns
  into (segments, error) instead of an exception
- the UI's selected categories filter the API's raw categories correctly
  (the SB_API_MAP translation outro->ending, filler->tangents is applied —
  the API never speaks the UI's naming)
- the EDL bytes are IDENTICAL to what ytdl/download.py::create_edl_file()
  writes from yt-dlp's .info.json — verified by running the real method on a
  synthetic info.json and comparing files

No network (requests is stubbed), no Qt event loop, no yt-dlp.

Run from the repo root:
    python3 -m unittest temp.test_sponsorblock_standalone -v
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from temp import sponsorblock_standalone as sb
from ytdl.download import DownloadMixin
from ytdl.sidecars import SidecarMixin
from ytdl.utils import sanitize_title


class RecordingResponse:
    """Stand-in for requests.Response."""

    def __init__(self, status_code=200, payload=None, bad_json=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else []
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("no JSON here")
        return self._payload


class RecordingRequests:
    """requests-module stub: records the call, returns a canned response."""

    class exceptions:  # noqa: N801 - mirrors requests.exceptions
        RequestException = OSError

    def __init__(self, response=None, exc=None):
        self.response = response if response is not None else RecordingResponse()
        self.exc = exc
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self.exc is not None:
            raise self.exc
        return self.response


def seg(category, start, end):
    """One raw API segment object."""
    return {"category": category, "segment": [start, end], "UUID": "u-" + category}


class TestVideoIdExtraction(unittest.TestCase):
    """extract_video_id() is the site profile gate, YouTube-only."""

    def test_watch_url(self):
        self.assertEqual(
            sb.extract_video_id("https://www.youtube.com/watch?v=yePLuwL3FbM"),
            "yePLuwL3FbM",
        )

    def test_short_url(self):
        self.assertEqual(sb.extract_video_id("https://youtu.be/jNQXAC9IVRw"),
                         "jNQXAC9IVRw")

    def test_shorts_url(self):
        self.assertEqual(
            sb.extract_video_id("https://www.youtube.com/shorts/abcABC123_-"),
            "abcABC123_-",
        )

    def test_garbage_is_not_a_url(self):
        self.assertEqual(sb.extract_video_id("nonsense"), "")

    def test_non_youtube_site_yields_no_id(self):
        # SponsorBlock is a YouTube-only database: a Rumble id must not leak out.
        self.assertEqual(sb.extract_video_id("https://rumble.com/v6abcde.html"), "")

    def test_empty_input(self):
        self.assertEqual(sb.extract_video_id(""), "")
        self.assertEqual(sb.extract_video_id(None), "")


class TestFetchSponsorBlockSegments(unittest.TestCase):
    """One GET, no yt-dlp; every failure is a returned message, not a raise."""

    def test_request_shape(self):
        stub = RecordingRequests(response=RecordingResponse(
            payload=[seg("sponsor", 1, 9)]))
        with mock.patch.object(sb, "requests", stub):
            segments, error = sb.fetch_sponsorblock_segments("yePLuwL3FbM")
        self.assertIsNone(error)
        self.assertEqual(len(segments), 1)
        call = stub.calls[0]
        self.assertEqual(call["url"], sb.API_URL)
        self.assertEqual(call["params"]["videoID"], "yePLuwL3FbM")
        self.assertEqual(call["params"]["category"], sb.ALL_API_CATEGORIES)
        self.assertEqual(call["timeout"], sb.DEFAULT_TIMEOUT)

    def test_custom_categories_are_passed_through(self):
        stub = RecordingRequests()
        with mock.patch.object(sb, "requests", stub):
            sb.fetch_sponsorblock_segments("abc", categories=["sponsor"])
        self.assertEqual(stub.calls[0]["params"]["category"], ["sponsor"])

    def test_404_is_no_segments_not_an_error(self):
        stub = RecordingRequests(response=RecordingResponse(status_code=404))
        with mock.patch.object(sb, "requests", stub):
            segments, error = sb.fetch_sponsorblock_segments("abc")
        self.assertEqual(segments, [])
        self.assertIsNone(error)

    def test_server_error_becomes_message(self):
        stub = RecordingRequests(response=RecordingResponse(status_code=500))
        with mock.patch.object(sb, "requests", stub):
            segments, error = sb.fetch_sponsorblock_segments("abc")
        self.assertEqual(segments, [])
        self.assertIn("500", error)

    def test_network_failure_becomes_message(self):
        stub = RecordingRequests(exc=OSError("connection refused"))
        with mock.patch.object(sb, "requests", stub):
            segments, error = sb.fetch_sponsorblock_segments("abc")
        self.assertEqual(segments, [])
        self.assertIn("connection refused", error)

    def test_bad_json_becomes_message(self):
        stub = RecordingRequests(response=RecordingResponse(bad_json=True))
        with mock.patch.object(sb, "requests", stub):
            segments, error = sb.fetch_sponsorblock_segments("abc")
        self.assertEqual(segments, [])
        self.assertIn("JSON", error)

    def test_unexpected_payload_becomes_message(self):
        stub = RecordingRequests(response=RecordingResponse(payload={"oops": 1}))
        with mock.patch.object(sb, "requests", stub):
            segments, error = sb.fetch_sponsorblock_segments("abc")
        self.assertEqual(segments, [])
        self.assertIn("payload", error)

    def test_missing_id_never_touches_the_network(self):
        stub = RecordingRequests()
        with mock.patch.object(sb, "requests", stub):
            segments, error = sb.fetch_sponsorblock_segments("")
        self.assertEqual(segments, [])
        self.assertEqual(error, "no video id")
        self.assertEqual(stub.calls, [])


class TestCategorySelection(unittest.TestCase):
    """The API speaks its own names; the checkboxes speak the UI's."""

    def test_api_names_map_to_ui_names(self):
        self.assertEqual(sb.to_ui_category("outro"), "ending")
        self.assertEqual(sb.to_ui_category("filler"), "tangents")
        self.assertEqual(sb.to_ui_category("poi_highlight"), "highlight")
        self.assertEqual(sb.to_ui_category("sponsor"), "sponsor")

    def test_selected_ending_matches_api_outro(self):
        segments = [seg("outro", 10, 20), seg("sponsor", 1, 5)]
        chosen = sb.select_segments(segments, ["ending"])
        self.assertEqual(chosen, [seg("outro", 10, 20)])

    def test_selected_tangents_matches_api_filler(self):
        segments = [seg("filler", 3, 4), seg("intro", 0, 2)]
        chosen = sb.select_segments(segments, ["tangents"])
        self.assertEqual(chosen, [seg("filler", 3, 4)])

    def test_unselected_category_is_dropped(self):
        segments = [seg("sponsor", 1, 5), seg("selfpromo", 6, 8)]
        self.assertEqual(sb.select_segments(segments, ["sponsor"]),
                         [seg("sponsor", 1, 5)])

    def test_checkboxless_category_needs_its_raw_name(self):
        # The UI has no music_offtopic checkbox, so its segments only join a
        # selection that names the API category itself ("All"-style requests).
        segments = [seg("music_offtopic", 1, 5)]
        self.assertEqual(sb.select_segments(segments, sb.UI_CATEGORIES), [])
        self.assertEqual(sb.select_segments(segments, ["music_offtopic"]), segments)

    def test_empty_selection_keeps_nothing(self):
        segments = [seg("sponsor", 1, 5)]
        self.assertEqual(sb.select_segments(segments, []), [])


class TestEdlContent(unittest.TestCase):
    """The EDL bytes: '<start> <end> 0' per line, action 0 = skip."""

    def test_format_matches_create_edl_file(self):
        content = sb.build_edl_content([
            seg("sponsor", 15.25, 90.5),
            seg("outro", 600, 612),
        ])
        self.assertEqual(content, "15.25 90.5 0\n600 612 0\n")

    def test_zero_length_segment_is_skipped(self):
        # The API uses [0, 0] as its "entire video" marker (yt-dlp drops it too).
        content = sb.build_edl_content([seg("sponsor", 0, 0), seg("intro", 2, 6)])
        self.assertEqual(content, "2 6 0\n")

    def test_empty_selection_writes_empty_file(self):
        self.assertEqual(sb.build_edl_content([]), "")


class TestWriteEdl(unittest.TestCase):
    """<base>.edl next to the media, exactly create_edl_file's naming."""

    def test_writes_beside_the_media_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = os.path.join(tmp, "My Video.mp4")
            path = sb.write_edl(media, [seg("sponsor", 1, 9)])
            self.assertEqual(path, os.path.join(tmp, "My Video.edl"))
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "1 9 0\n")

    def test_base_without_extension_still_gets_edl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = sb.write_edl(os.path.join(tmp, "yePLuwL3FbM"),
                                [seg("outro", 3, 7)])
            self.assertEqual(path, os.path.join(tmp, "yePLuwL3FbM.edl"))

    def test_overwrites_previous_edl(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "v")
            sb.write_edl(base, [seg("sponsor", 1, 9)])
            path = sb.write_edl(base, [seg("sponsor", 2, 8)])
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "2 8 0\n")


class _EdlHarness:
    """Minimal stand-in around the REAL DownloadMixin.create_edl_file()."""

    def __init__(self, json_path, selected):
        self._json_path = json_path
        self._selected = selected
        self.lines = []

    def get_full_path(self, extension="", lang=None):
        # Mirror the real get_full_path(): no extension -> extensionless base;
        # with extension -> base + extension. The harness previously always
        # returned the .info.json path, which was sufficient when create_edl_file
        # only queried the JSON file; now it also needs the base for the EDL path.
        base = self._json_path.removesuffix(".info.json")
        if lang:
            base = f"{base}.{lang}"
        if extension:
            if not extension.startswith("."):
                extension = "." + extension
            return base + extension
        return base

    def get_selected_sb_categories(self):
        return list(self._selected)

    def _emit(self, name, *args):
        self.lines.append(" ".join(str(a) for a in args))


class TestParityWithTheAppsEdl(unittest.TestCase):
    """The prototype's EDL must be byte-identical to the app's own writer."""

    def _app_edl_content(self, tmp, chapters, selected, require_file=True):
        info_path = os.path.join(tmp, "Video.info.json")
        with open(info_path, "w", encoding="utf-8") as handle:
            json.dump({"sponsorblock_chapters": chapters}, handle)
        harness = _EdlHarness(info_path, selected)
        SidecarMixin.create_edl_file(harness, os.path.join(tmp, "Video.mp4"))
        edl_path = os.path.join(tmp, "Video.edl")
        if not os.path.exists(edl_path):
            # create_edl_file() returns before writing when nothing matched.
            self.assertFalse(require_file, "app wrote no EDL")
            return ""
        with open(edl_path, encoding="utf-8") as handle:
            return handle.read()

    def test_identical_bytes_to_create_edl_file(self):
        chapters = [
            {"start_time": 15.25, "end_time": 90.5, "category": "sponsor",
             "_categories": [("sponsor", 15.25, 90.5, "Sponsor")]},
            {"start_time": 600, "end_time": 612, "category": "sponsor",
             "_categories": [("sponsor", 600, 612, "Sponsor")]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            app_content = self._app_edl_content(tmp, chapters, ["sponsor"])
        our_content = sb.build_edl_content([
            seg("sponsor", 15.25, 90.5),
            seg("sponsor", 600, 612),
        ])
        self.assertEqual(app_content, our_content)
        self.assertEqual(app_content, "15.25 90.5 0\n600 612 0\n")

    def test_unselected_chapters_are_dropped_by_both_writers(self):
        chapters = [
            {"start_time": 1, "end_time": 9, "category": "sponsor",
             "_categories": [("sponsor", 1, 9, "Sponsor")]},
            {"start_time": 20, "end_time": 30, "category": "selfpromo",
             "_categories": [("selfpromo", 20, 30, "Selfpromo")]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            app_content = self._app_edl_content(tmp, chapters, ["sponsor"])
        our_content = sb.build_edl_content(sb.select_segments(
            [seg("sponsor", 1, 9), seg("selfpromo", 20, 30)], ["sponsor"]
        ))
        self.assertEqual(app_content, our_content)
        self.assertEqual(app_content, "1 9 0\n")

    def test_app_edl_translates_outro_for_ending_checkbox(self):
        """create_edl_file() now maps raw API category names to the UI's.

        yt-dlp writes raw API category names into sponsorblock_chapters
        (_categories = [("outro", ...)]), while get_selected_sb_categories()
        returns the UI's naming ("ending"). The app used to compare them
        verbatim, so an "Ending"-only selection matched nothing at all —
        this used to be the characterization test for that quirk. It now
        maps through SB_API_MAP (to_ui_category), exactly like the
        prototype, and the two writers agree again.
        """
        chapters = [
            {"start_time": 5, "end_time": 15, "category": "outro",
             "_categories": [("outro", 5, 15, "Endcards/Credits")]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            app_content = self._app_edl_content(tmp, chapters, ["ending"])
        self.assertEqual(app_content, "5 15 0\n")
        our_content = sb.build_edl_content(sb.select_segments(
            [seg("outro", 5, 15)], ["ending"]
        ))
        self.assertEqual(our_content, "5 15 0\n")


class _Bar:
    """Stand-in for SponsorBlockBar: only .segments is read by the prototype."""

    def __init__(self, segments):
        self.segments = list(segments)


class _EdlOnlyHarness:
    """Minimal GUI stand-in at the start_download() branch point.

    Simulates state AFTER (1) a normal fetch-info and (2) start_download()'s
    shared prelude — i.e. exactly where media_type == EDL_ONLY_MEDIA_TYPE
    would branch off before build_command(). Deliberately has NO build_command,
    run_download, yt_dlp_bin or title_entry: touching anything from the
    yt-dlp download path raises AttributeError, which is the test.
    """

    def __init__(self, output_dir, base_filename, video_id="yePLuwL3FbM",
                 site="youtube", categories=("sponsor",), bar_segments=()):
        self.video_state = {
            "base_filename": base_filename,
            "output_dir": output_dir,
            "video_id": video_id,
            "site": site,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "media_type": sb.EDL_ONLY_MEDIA_TYPE,
            "is_download_running": True,
        }
        self.sb_bar = _Bar(bar_segments)
        self._categories = list(categories)
        self.lines = []

    def get_full_path(self, extension="", lang=None):
        return DownloadMixin.get_full_path(self, extension, lang)

    def get_output_dir(self):
        return self.video_state.get("output_dir", "")

    def get_selected_sb_categories(self):
        return list(self._categories)

    def _emit(self, name, *args):
        assert name == "append_output", name
        self.lines.append(" ".join(str(a) for a in args))


def _run(harness, fetch=None):
    """create_edl_only() with a fetch stub that FAILS THE TEST if called."""

    def _forbidden(video_id, *a, **k):
        raise AssertionError(f"HTTP fetch despite cached bar segments ({video_id})")

    return sb.create_edl_only(harness, fetch=fetch or _forbidden)


class TestCreateEdlOnlyMode(unittest.TestCase):
    """The ".edl only" radio mode: info-fetch name + bar segments, no yt-dlp.

    The harness has no build_command/yt_dlp_bin at all, so any attempt to go
    through the download path would raise AttributeError — passing tests prove
    the mode never starts (or even references) a yt-dlp process.
    """

    def test_edl_lands_under_the_info_fetch_title(self):
        """Decision (a): basename + folder come from the shared prelude."""
        with tempfile.TemporaryDirectory() as tmp:
            # start_download()'s prelude, mirrored: Root / Channel / Title.
            title = sanitize_title("Ep 12 — The Big One")
            video_dir = os.path.join(tmp, "PowerfulJRE", title)
            os.makedirs(video_dir)
            harness = _EdlOnlyHarness(video_dir, title,
                                      bar_segments=[seg("sponsor", 1, 9)])
            applied = _run(harness)
            expected = os.path.join(video_dir, title + ".edl")
            self.assertTrue(os.path.isfile(expected), expected)
            with open(expected, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "1 9 0\n")
            self.assertEqual(applied, [seg("sponsor", 1, 9)])
            self.assertTrue(any("Created EDL file" in line
                                for line in harness.lines))

    def test_media_type_is_the_new_radio_value(self):
        """Decision (b): the mode is a media_type, not a button."""
        self.assertEqual(sb.EDL_ONLY_MEDIA_TYPE, "edl_only")

    def test_cached_bar_segments_never_hit_the_network(self):
        """fetch-info already pulled all categories for the bar — reuse them."""
        with tempfile.TemporaryDirectory() as tmp:
            harness = _EdlOnlyHarness(tmp, "v", bar_segments=[
                seg("sponsor", 1, 9), seg("outro", 20, 30),
            ])
            # _run()'s default fetch raises if called; categories include
            # "ending", so the outro->ending SB_API_MAP translation is
            # exercised too.
            harness._categories = ["sponsor", "ending"]
            applied = _run(harness)
            self.assertEqual(len(applied), 2)  # no HTTP, both kept

    def test_falls_back_to_api_when_bar_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = _EdlOnlyHarness(tmp, "v", bar_segments=[])
            calls = []

            def fake_fetch(video_id):
                calls.append(video_id)
                return [seg("sponsor", 5, 15)], None

            applied = sb.create_edl_only(harness, fetch=fake_fetch)
            self.assertEqual(calls, ["yePLuwL3FbM"])
            self.assertEqual(applied, [seg("sponsor", 5, 15)])
            self.assertTrue(os.path.isfile(os.path.join(tmp, "v.edl")))

    def test_fetch_error_writes_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = _EdlOnlyHarness(tmp, "v")
            applied = sb.create_edl_only(
                harness, fetch=lambda vid: ([], "HTTP 500"))
            self.assertEqual(applied, [])
            self.assertEqual(os.listdir(tmp), [])
            self.assertTrue(any("lookup failed" in line
                                for line in harness.lines))

    def test_non_youtube_site_skips_without_fetching(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = _EdlOnlyHarness(tmp, "v", site="rumble",
                                      bar_segments=[seg("sponsor", 1, 9)])
            applied = _run(harness)  # default fetch raises when called
            self.assertEqual(applied, [])
            self.assertEqual(os.listdir(tmp), [])
            self.assertTrue(any("YouTube only" in line
                                for line in harness.lines))

    def test_missing_video_id_reports_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = _EdlOnlyHarness(tmp, "v", video_id="")
            harness.video_state["url"] = ""
            applied = _run(harness)
            self.assertEqual(applied, [])
            self.assertEqual(os.listdir(tmp), [])
            self.assertTrue(any("Could not extract video ID" in line
                                for line in harness.lines))

    def test_unmatched_categories_write_no_file(self):
        """Selected category exists, but the video has no such segment."""
        with tempfile.TemporaryDirectory() as tmp:
            harness = _EdlOnlyHarness(tmp, "v",
                                      bar_segments=[seg("music_offtopic", 1, 9)],
                                      categories=["sponsor"])
            applied = _run(harness)
            self.assertEqual(applied, [])
            self.assertEqual(os.listdir(tmp), [])
            self.assertTrue(any("No segments to skip" in line
                                for line in harness.lines))

    def test_no_categories_checked_write_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = _EdlOnlyHarness(tmp, "v",
                                      bar_segments=[seg("sponsor", 1, 9)],
                                      categories=[])
            applied = _run(harness)
            self.assertEqual(applied, [])
            self.assertEqual(os.listdir(tmp), [])

    def test_zero_length_marker_alone_writes_no_file(self):
        """The API's [0, 0] whole-video marker must not produce an empty .edl."""
        with tempfile.TemporaryDirectory() as tmp:
            harness = _EdlOnlyHarness(tmp, "v",
                                      bar_segments=[seg("sponsor", 0, 0)])
            applied = _run(harness)
            self.assertEqual(applied, [])
            self.assertEqual(os.listdir(tmp), [])

    def test_empty_base_filename_reports_no_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = _EdlOnlyHarness(tmp, "",  # get_full_path() -> ""
                                      bar_segments=[seg("sponsor", 1, 9)])
            applied = _run(harness)
            self.assertEqual(applied, [])
            self.assertEqual(os.listdir(tmp), [])
            self.assertTrue(any("No video title" in line
                                for line in harness.lines))


if __name__ == "__main__":
    unittest.main()

