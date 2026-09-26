"""A video's only subtitle is stored without a language code.

The rule is literal: one subtitle file for the whole video -> "<title>.srt",
however many languages the site offers or the request asked for. With two or
more files the language code is what tells them apart and is kept.

A bare name records no language, so the "already downloaded" pre-check can
only credit it to a request that covers every language the site offers
(SubtitleMixin._bare_subtitle_credit) - otherwise the subtitle is requested
again rather than silently assumed to be the right language.

Run from the repo root:  python3 -m unittest temp.test_subtitle_single_naming -v
"""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app  # noqa: E402
from temp.test_download_completion import _Process, _Signals  # noqa: E402


# Two well-formed cues, short enough that the 2-line merge leaves them alone.
SRT = (
    "1\n00:00:01,000 --> 00:00:02,000\nHello there\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\nGeneral Kenobi\n"
)


class NamingHarness(app.SubtitleMixin, app.DownloadMixin):
    """Real mixins; only the output directory and video state come from here."""

    def __init__(self, out_dir, available=None, base="Title"):
        self._out_dir = out_dir
        self.video_state = {
            "base_filename": base,
            "available_subtitles": available or {},
        }
        self.signals = SimpleNamespace(
            append_output=SimpleNamespace(emit=lambda *_args: None)
        )

    get_output_dir = lambda self: self._out_dir  # noqa: E731


class TestBareSubtitleDecision(unittest.TestCase):
    """The rule is literal: one file on disk -> no language code.

    How many languages the *site* offers is irrelevant; only the number of
    files that were actually produced matters.
    """

    def test_one_file_drops_the_code(self):
        self.assertTrue(NamingHarness(tempfile.mkdtemp())._uses_bare_subtitle_name(1))

    def test_several_offered_languages_still_drop_the_code(self):
        h = NamingHarness(tempfile.mkdtemp(), {"en": "auto", "de": "real"})
        self.assertTrue(h._uses_bare_subtitle_name(1))

    def test_without_info_availability_the_code_is_still_dropped(self):
        self.assertTrue(NamingHarness(tempfile.mkdtemp())._uses_bare_subtitle_name(1))

    def test_two_files_keep_the_code(self):
        # The language code is then the only thing telling them apart.
        h = NamingHarness(tempfile.mkdtemp())
        self.assertFalse(h._uses_bare_subtitle_name(2))

    def test_no_subtitle_file_is_not_bare(self):
        h = NamingHarness(tempfile.mkdtemp())
        self.assertFalse(h._uses_bare_subtitle_name(0))


class TestBareSubtitleCredit(unittest.TestCase):
    """Which request a language-less file may be credited to on a re-run.

    The bare name stores no language, so a later run cannot read it back. It is
    only trusted when the requested set covers every language the site offers;
    otherwise crediting it would skip a download and silently leave the wrong
    language on disk.
    """

    def _harness(self, available):
        return NamingHarness(tempfile.mkdtemp(), available)

    def test_credited_when_the_request_covers_everything_offered(self):
        h = self._harness({"en": "auto"})
        self.assertEqual(h._bare_subtitle_credit(["en"]), "en")

    def test_credited_to_exactly_one_language(self):
        # Both requested: the bare file can stand for one of them, so the
        # other is still missing and the run is not skipped.
        h = self._harness({"en": "auto", "de": "real"})
        self.assertEqual(h._bare_subtitle_credit(["en", "de"]), "en")

    def test_not_credited_when_another_offered_language_was_not_requested(self):
        # The bare file may be that unrequested language.
        h = self._harness({"en": "auto", "de": "real"})
        self.assertIsNone(h._bare_subtitle_credit(["de"]))

    def test_not_credited_without_info_availability(self):
        self.assertIsNone(self._harness({})._bare_subtitle_credit(["en"]))

    def test_nothing_to_credit_without_a_request(self):
        self.assertIsNone(self._harness({"en": "auto"})._bare_subtitle_credit([]))


class TestSubtitleOutputPath(unittest.TestCase):
    def test_bare_drops_the_language_segment(self):
        h = NamingHarness(tempfile.mkdtemp(), {"en": "auto"})
        self.assertEqual(
            os.path.basename(h._subtitle_output_path("en", bare=True)), "Title.srt"
        )

    def test_tagged_keeps_the_language_segment(self):
        h = NamingHarness(tempfile.mkdtemp(), {"en": "auto"})
        self.assertEqual(os.path.basename(h._subtitle_output_path("en")), "Title.en.srt")


class TestNormalizeToBareName(unittest.TestCase):
    def _harness(self, files, available):
        d = tempfile.mkdtemp()
        for name in files:
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(SRT)
        return NamingHarness(d, available), d

    def _process(self, harness, selected, count=1):
        """The exact call chain run_download() performs."""
        found = harness._find_downloaded_subtitles(selected)
        bare = harness._uses_bare_subtitle_name(count)
        return harness._normalize_subtitle_names(found, bare=bare), bare

    def test_single_language_ends_up_as_title_srt(self):
        h, d = self._harness(["Title.en-auto.srt"], {"en": "auto"})
        subs, bare = self._process(h, ["en"])
        self.assertTrue(bare)
        self.assertEqual(subs[0][1], os.path.join(d, "Title.srt"))
        self.assertEqual(os.listdir(d), ["Title.srt"])

    def test_canonical_source_is_renamed_to_the_bare_name(self):
        h, d = self._harness(["Title.en.srt"], {"en": "real"})
        subs, _ = self._process(h, ["en"])
        self.assertEqual(subs[0][1], os.path.join(d, "Title.srt"))
        self.assertEqual(os.listdir(d), ["Title.srt"])

    def test_content_survives_the_rename(self):
        h, d = self._harness(["Title.en.srt"], {"en": "real"})
        self._process(h, ["en"])
        with open(os.path.join(d, "Title.srt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), SRT)

    def test_one_of_two_requested_languages_drops_the_code(self):
        # Two languages requested, but the site only delivered one file: the
        # literal rule keys off files on disk, not off the request.
        h, d = self._harness(["Title.en.srt"], {"en": "auto", "de": "real"})
        found = h._find_downloaded_subtitles(["en", "de"])
        bare = h._uses_bare_subtitle_name(len(found))
        normalized = h._normalize_subtitle_names(found, bare=bare)
        self.assertTrue(bare)
        self.assertEqual(normalized[0][1], os.path.join(d, "Title.srt"))
        self.assertEqual(os.listdir(d), ["Title.srt"])

    def test_two_languages_keep_their_code(self):
        h, d = self._harness(
            ["Title.en.srt", "Title.de.srt"], {"en": "real", "de": "real"}
        )
        subs, bare = self._process(h, ["en", "de"], count=2)
        self.assertFalse(bare)
        self.assertEqual(
            sorted(os.path.basename(path) for _lang, path, _t in subs),
            ["Title.de.srt", "Title.en.srt"],
        )

    def test_bare_default_keeps_existing_behavior(self):
        # Callers that do not opt in (replay helpers) keep the old naming.
        h, d = self._harness(["Title.en-auto.srt"], {"en": "auto"})
        normalized = h._normalize_subtitle_names(h._find_downloaded_subtitles(["en"]))
        self.assertEqual(normalized[0][1], os.path.join(d, "Title.en.srt"))


class TestResyncWritesTheSameName(unittest.TestCase):
    def _harness(self, filename, available):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, filename), "w", encoding="utf-8") as fh:
            fh.write(SRT)
        return NamingHarness(d, available), d

    def test_resync_target_is_the_bare_name(self):
        h, d = self._harness("Title.srt", {"en": "auto"})
        h._resync_subtitle_for_language("en", os.path.join(d, "Title.srt"), [], bare=True)
        self.assertEqual(os.listdir(d), ["Title.srt"])
        with open(os.path.join(d, "Title.srt"), encoding="utf-8") as fh:
            merged = fh.read()
        # Rewritten in place by the 2-line merge pass, under the bare name.
        self.assertIn("-->", merged)
        self.assertIn("General Kenobi", merged)

    def test_resync_without_bare_keeps_the_code(self):
        h, d = self._harness("Title.en.srt", {"en": "real"})
        h._resync_subtitle_for_language("en", os.path.join(d, "Title.en.srt"), [])
        self.assertEqual(os.listdir(d), ["Title.en.srt"])


class TestSupersededBareSubtitle(unittest.TestCase):
    def _harness(self, files, available):
        d = tempfile.mkdtemp()
        for name in files:
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(SRT)
        return NamingHarness(d, available), d

    def test_leftover_is_removed(self):
        # An earlier run left a language-less file; this run produced two
        # language-tagged ones, which supersede it.
        h, d = self._harness(
            ["Title.srt", "Title.en.srt", "Title.de.srt"], {"en": "auto", "de": "real"}
        )
        removed = h._remove_superseded_bare_subtitle()
        self.assertEqual(removed, os.path.join(d, "Title.srt"))
        self.assertEqual(sorted(os.listdir(d)), ["Title.de.srt", "Title.en.srt"])

    def test_tagged_files_are_untouched(self):
        h, d = self._harness(
            ["Title.en.srt", "Title.de.srt"], {"en": "auto", "de": "real"}
        )
        self.assertIsNone(h._remove_superseded_bare_subtitle())
        self.assertEqual(sorted(os.listdir(d)), ["Title.de.srt", "Title.en.srt"])

    def test_nothing_to_do_without_a_bare_file(self):
        h, d = self._harness(["Title.en.srt"], {"en": "auto", "de": "real"})
        self.assertIsNone(h._remove_superseded_bare_subtitle())
        self.assertTrue(os.path.exists(os.path.join(d, "Title.en.srt")))


class _RunHarness(app.SubtitleMixin, app.DownloadMixin):
    """Real run_download() with a fake yt-dlp process (no threads, no Qt)."""

    def __init__(self, out_dir, available):
        self.video_state = {
            "media_type": "video",
            "is_download_running": True,
            "base_filename": "Title",
            "output_dir": out_dir,
            "available_subtitles": available,
            "site": "youtube",
        }
        self.signals = _Signals()
        self.simulate_download_error = False
        self.cached_video_metadata = None
        self._proc = None
        self._cancelled = False
        self._shutting_down = False

    def get_file_metadata(self, _filename):
        return {}

    def _analyze_downloaded_file(self, _filename):
        pass

    def create_edl_file(self, _filename):
        return []

    def create_nfo_file(self, _filename):
        pass

    def clearDockProgress(self):
        pass


class TestRunDownloadEndToEnd(unittest.TestCase):
    """The whole post-processing chain, with yt-dlp's output faked."""

    def _run(self, files, available, selected=("en",)):
        out_dir = tempfile.mkdtemp()
        for name, content in files.items():
            with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
                fh.write(content)
        harness = _RunHarness(out_dir, available)
        media = os.path.join(out_dir, "Title.mp4")
        process = _Process(
            [
                f"[download] Destination: {media}",
                "[download] 100% of 1.00MiB in 00:00:01",
            ],
            0,
        )
        with mock.patch.object(app.subprocess, "Popen", return_value=process):
            harness.run_download(["yt-dlp"], list(selected))
        return harness, out_dir

    @staticmethod
    def _output_lines(harness):
        return [args[0] for args in harness.signals.append_output.emitted if args]

    def test_single_language_ends_up_language_less(self):
        harness, out_dir = self._run(
            {"Title.mp4": "media", "Title.en.srt": SRT}, {"en": "auto"}
        )
        self.assertEqual(sorted(os.listdir(out_dir)), ["Title.mp4", "Title.srt"])
        self.assertIn(
            "  → single subtitle, stored as: Title.srt", self._output_lines(harness)
        )

    def test_one_file_from_a_multi_language_video_is_still_language_less(self):
        # The literal rule: one file landed, so no language code - even though
        # the video offers two languages and both were requested.
        _harness, out_dir = self._run(
            {"Title.mp4": "media", "Title.en.srt": SRT},
            {"en": "auto", "de": "real"},
            selected=("en", "de"),
        )
        self.assertEqual(sorted(os.listdir(out_dir)), ["Title.mp4", "Title.srt"])

    def test_auto_captions_are_resynced_into_the_bare_name(self):
        # The common YouTube case: auto-generated captions, site-named file,
        # merged into 2-line format and stored without the language code.
        _harness, out_dir = self._run(
            {"Title.mp4": "media", "Title.a.en.srt": SRT}, {"en": "auto"}
        )
        self.assertEqual(sorted(os.listdir(out_dir)), ["Title.mp4", "Title.srt"])
        with open(os.path.join(out_dir, "Title.srt"), encoding="utf-8") as fh:
            self.assertIn("General Kenobi", fh.read())

    def test_two_languages_keep_their_codes(self):
        _harness, out_dir = self._run(
            {"Title.mp4": "media", "Title.en.srt": SRT, "Title.de.srt": SRT},
            {"en": "auto", "de": "real"},
            selected=("en", "de"),
        )
        self.assertEqual(
            sorted(os.listdir(out_dir)), ["Title.de.srt", "Title.en.srt", "Title.mp4"]
        )

    def test_language_less_leftover_is_dropped_by_a_multi_language_run(self):
        _harness, out_dir = self._run(
            {
                "Title.mp4": "media",
                "Title.srt": SRT,
                "Title.en.srt": SRT,
                "Title.de.srt": SRT,
            },
            {"en": "auto", "de": "real"},
            selected=("en", "de"),
        )
        self.assertEqual(
            sorted(os.listdir(out_dir)), ["Title.de.srt", "Title.en.srt", "Title.mp4"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
