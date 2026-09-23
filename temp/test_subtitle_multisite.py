# Unit tests for site-aware subtitle handling (subtitle-key shapes).
# Run from the repo root:  python3 -m unittest temp.test_subtitle_multisite -v

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app


class SubtitleHarness:
    def __init__(self, out_dir):
        self._out_dir = out_dir
        self.video_state = {"base_filename": "Title"}

    get_output_dir = lambda self: self._out_dir  # noqa: E731
    get_full_path = app.YTDLPDownloaderGUI.get_full_path
    _subtitle_lang_patterns = app.YTDLPDownloaderGUI._subtitle_lang_patterns
    _find_downloaded_subtitles = app.YTDLPDownloaderGUI._find_downloaded_subtitles
    _normalize_subtitle_names = app.YTDLPDownloaderGUI._normalize_subtitle_names
    _subtitle_needs_resync = app.YTDLPDownloaderGUI._subtitle_needs_resync


class TestSubtitleLangPatterns(unittest.TestCase):
    def test_covers_all_key_shapes(self):
        h = SubtitleHarness(tempfile.mkdtemp())
        self.assertEqual(h._subtitle_lang_patterns(["en"]), "en,a.en,en-auto,a.en-auto")

    def test_multiple_langs(self):
        h = SubtitleHarness(tempfile.mkdtemp())
        arg = h._subtitle_lang_patterns(["en", "de"])
        for needle in ("en", "a.en", "en-auto", "de", "a.de", "de-auto"):
            self.assertIn(needle, arg)

    def test_all_variants_are_valid_regexes(self):
        # yt-dlp compiles every --sub-langs entry as a regex; ensure none of
        # our variants is rejected (which would abort the whole run).
        import re
        h = SubtitleHarness(tempfile.mkdtemp())
        for variant in h._subtitle_lang_patterns(["en", "de"]).split(","):
            try:
                re.compile(variant)
            except re.error:  # pragma: no cover - failure path
                self.fail(f"invalid regex in sub-langs: {variant}")


class TestFindDownloadedSubtitles(unittest.TestCase):
    def _harness(self, files):
        d = tempfile.mkdtemp()
        for name in files:
            with open(os.path.join(d, name), "w") as fh:
                fh.write("sub")
        return SubtitleHarness(d), d

    def test_rumble_auto_srt_found(self):
        h, d = self._harness(["Title.en-auto.srt"])
        subs = h._find_downloaded_subtitles(["en"])
        self.assertEqual(len(subs), 1)
        lang, path, sub_type = subs[0]
        self.assertEqual((lang, sub_type), ("en", "auto"))
        self.assertEqual(path, os.path.join(d, "Title.en-auto.srt"))

    def test_prefers_real_canonical_over_auto(self):
        h, d = self._harness(["Title.en-auto.srt", "Title.en.srt"])
        subs = h._find_downloaded_subtitles(["en"])
        self.assertEqual(subs[0][1], os.path.join(d, "Title.en.srt"))
        self.assertEqual(subs[0][2], "real")

    def test_a_prefix_is_auto(self):
        h, _ = self._harness(["Title.a.en.srt"])
        subs = h._find_downloaded_subtitles(["en"])
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0][2], "auto")

    def test_no_match_returns_empty(self):
        h, _ = self._harness(["Title.de.srt", "Title.en-espanol.txt"])
        self.assertEqual(h._find_downloaded_subtitles(["en"]), [])

    def test_vtt_fallback_if_not_converted(self):
        h, d = self._harness(["Title.en-auto.vtt"])
        subs = h._find_downloaded_subtitles(["en"])
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0][2], "auto")
        self.assertEqual(subs[0][1], os.path.join(d, "Title.en-auto.vtt"))

    def test_region_variant_not_matched(self):
        # Historical behavior: region keys like "en-US" were never detected
        h, _ = self._harness(["Title.en-US.srt"])
        self.assertEqual(h._find_downloaded_subtitles(["en"]), [])


class TestNormalizeSubtitleNames(unittest.TestCase):
    def _harness(self, files):
        d = tempfile.mkdtemp()
        for name in files:
            with open(os.path.join(d, name), "w") as fh:
                fh.write("sub")
        return SubtitleHarness(d), d

    def test_rumble_auto_file_renamed_to_canonical(self):
        h, d = self._harness(["Title.en-auto.srt"])
        detected = h._find_downloaded_subtitles(["en"])
        self.assertEqual(detected[0][1], os.path.join(d, "Title.en-auto.srt"))
        normalized = h._normalize_subtitle_names(detected)
        # Single canonical file, original site-named file gone
        self.assertEqual(normalized[0][1], os.path.join(d, "Title.en.srt"))
        self.assertTrue(os.path.exists(os.path.join(d, "Title.en.srt")))
        self.assertFalse(os.path.exists(os.path.join(d, "Title.en-auto.srt")))
        # The auto marker is preserved in the metadata, not the filename
        self.assertEqual(normalized[0][2], "auto")

    def test_canonical_name_untouched(self):
        h, d = self._harness(["Title.en.srt"])
        detected = h._find_downloaded_subtitles(["en"])
        normalized = h._normalize_subtitle_names(detected)
        self.assertEqual(normalized[0][1], os.path.join(d, "Title.en.srt"))

    def test_overwrites_stale_canonical(self):
        # A stale canonical file from an earlier run is replaced
        h, d = self._harness(["Title.en-auto.srt", "Title.en.srt"])
        with open(os.path.join(d, "Title.en-auto.srt"), "w") as fh:
            fh.write("fresh download")
        normalized = h._normalize_subtitle_names([("en", os.path.join(d, "Title.en-auto.srt"), "auto")])
        self.assertEqual(normalized[0][1], os.path.join(d, "Title.en.srt"))
        with open(os.path.join(d, "Title.en.srt")) as fh:
            self.assertEqual(fh.read(), "fresh download")
        self.assertFalse(os.path.exists(os.path.join(d, "Title.en-auto.srt")))

    def test_missing_source_is_left_alone(self):
        h, d = self._harness([])
        ghost = os.path.join(d, "Title.en-auto.srt")
        normalized = h._normalize_subtitle_names([("en", ghost, "auto")])
        self.assertEqual(normalized[0][1], ghost)

    def test_full_pipeline_find_then_normalize(self):
        # Mirrors run_download's exact call chain
        h, d = self._harness(["Title.en-auto.srt"])
        normalized = h._normalize_subtitle_names(h._find_downloaded_subtitles(["en"]))
        self.assertEqual(
            [(lang, os.path.basename(path), sub_type) for lang, path, sub_type in normalized],
            [("en", "Title.en.srt", "auto")],
        )
        self.assertEqual(sorted(os.listdir(d)), ["Title.en.srt"])


class TestSubtitleNeedsResync(unittest.TestCase):
    """Resync/merge is site-aware: YouTube ASR needs it, Rumble doesn't."""

    def _harness(self, site):
        h = SubtitleHarness(tempfile.mkdtemp())
        h.video_state["site"] = site
        return h

    def test_youtube_auto_needs_merge(self):
        self.assertTrue(self._harness("youtube")._subtitle_needs_resync("auto"))

    def test_rumble_auto_keeps_original(self):
        self.assertFalse(self._harness("rumble")._subtitle_needs_resync("auto"))

    def test_real_never_resynced(self):
        for site in ("youtube", "rumble", "some-future-site"):
            self.assertFalse(self._harness(site)._subtitle_needs_resync("real"))

    def test_unknown_site_defaults_to_merge(self):
        self.assertTrue(self._harness("some-future-site")._subtitle_needs_resync("auto"))


if __name__ == "__main__":
    unittest.main()
