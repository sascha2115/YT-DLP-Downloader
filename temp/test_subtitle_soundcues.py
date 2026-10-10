"""Unit tests for the strip_bracketed_soundcues preference.

Run from the repo root:  python3 -m unittest temp.test_subtitle_soundcues -v

Covers the pure reader `preferences.strip_bracketed_soundcues()` (default,
validation, live read-through) and its effect on the real `resync_subtitles`
pipeline: brackets such as [laughter] and [snorts] are removed when the
preference is on and kept verbatim when it is off.

The default is ON, because automatic captions have always had these removed -
turning the preference off is a way to see the raw ASR text, not a change of
behaviour. No Qt needed.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app  # noqa: E402
from ytdl import preferences as P  # noqa: E402

SOURCE = (
    "1\n00:00:00,000 --> 00:00:02,000\n"
    "He said that [laughter] and then [snorts] left the room.\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\n"
    ">> [music] Nobody knew why.\n\n"
)


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _SubtitleHarness(app.SubtitleMixin):
    def __init__(self):
        self.video_state = {}
        self.signals = type("Signals", (), {"append_output": _Signal()})()


class TestPreferenceReader(unittest.TestCase):
    """The pure mapping from preferences.json to a yes/no answer."""

    def setUp(self):
        self.saved = P.preferences

    def tearDown(self):
        P.apply_preferences(self.saved)

    def test_absent_key_defaults_to_on(self):
        # Existing installs have no such key; they must keep today's behaviour.
        self.assertTrue(P.strip_bracketed_soundcues({}))
        self.assertTrue(P.strip_bracketed_soundcues({"channel_name_map": {}}))
        self.assertTrue(P.strip_bracketed_soundcues(None))

    def test_default_constant_is_on(self):
        self.assertTrue(P.STRIP_BRACKETED_SOUNDCUES_DEFAULT)

    def test_booleans_are_honoured(self):
        self.assertTrue(P.strip_bracketed_soundcues(
            {"strip_bracketed_soundcues": True}))
        self.assertFalse(P.strip_bracketed_soundcues(
            {"strip_bracketed_soundcues": False}))

    def test_non_boolean_is_rejected_and_logged(self):
        # 1 and 0 are the dangerous ones: bool is a subclass of int, so a bare
        # isinstance(x, int) check would silently accept them.
        for bad in ("yes", 1, 0, 1.0, [], {}, "true"):
            with self.subTest(value=bad):
                with self.assertLogs("ytdl.preferences", level="WARNING"):
                    self.assertTrue(P.strip_bracketed_soundcues(
                        {"strip_bracketed_soundcues": bad}))

    def test_live_preference_is_read_at_call_time(self):
        # Module bindings go stale after a save, so the reader must consult the
        # live state rather than a value captured at import.
        saved = P.preferences
        try:
            P.apply_preferences({"strip_bracketed_soundcues": False})
            self.assertFalse(P.strip_bracketed_soundcues())
            P.apply_preferences({"strip_bracketed_soundcues": True})
            self.assertTrue(P.strip_bracketed_soundcues())
        finally:
            P.apply_preferences(saved)

    def test_the_answer_is_always_a_real_bool(self):
        # `resync_subtitles` uses the answer in an `if`, so a truthy non-bool
        # would work by accident; pinning the type keeps that from drifting.
        for prefs in ({}, {"strip_bracketed_soundcues": True},
                      {"strip_bracketed_soundcues": False},
                      {"strip_bracketed_soundcues": "yes"},
                      {"strip_bracketed_soundcues": 1}):
            with self.subTest(prefs=prefs):
                self.assertIsInstance(P.strip_bracketed_soundcues(prefs), bool)

    def test_a_mistyped_default_still_answers_a_bool(self):
        # The fallback is coerced, so even a wrong-typed constant cannot leak a
        # non-bool into the caller. An absent key is not an error and must stay
        # silent - it is the fresh-install path - so no warning is expected.
        original = P.STRIP_BRACKETED_SOUNDCUES_DEFAULT
        try:
            P.STRIP_BRACKETED_SOUNDCUES_DEFAULT = "true"
            self.assertIs(P.strip_bracketed_soundcues({}), True)
        finally:
            P.STRIP_BRACKETED_SOUNDCUES_DEFAULT = original


class TestEffectOnPipeline(unittest.TestCase):
    """What the preference does to a real file through resync_subtitles."""

    def setUp(self):
        self.saved = P.preferences
        self.gui = _SubtitleHarness()

    def tearDown(self):
        P.apply_preferences(self.saved)

    def _run(self, prefs, source=SOURCE):
        P.apply_preferences(prefs)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "Title.en.srt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(source)
            self.assertTrue(self.gui.resync_subtitles(path, path))
            with open(path, encoding="utf-8") as handle:
                return handle.read()

    def test_on_removes_the_cues(self):
        content = self._run({"strip_bracketed_soundcues": True})
        self.assertIn("He said that and then left the room.", content)
        self.assertNotIn("[laughter]", content)
        self.assertNotIn("[snorts]", content)
        self.assertNotIn("[music]", content)

    def test_off_keeps_the_cues_verbatim(self):
        content = self._run({"strip_bracketed_soundcues": False})
        self.assertIn("[laughter]", content)
        self.assertIn("[snorts]", content)
        self.assertIn("[music]", content)

    def test_absent_key_still_strips(self):
        # The behaviour existing installs already have.
        content = self._run({})
        self.assertNotIn("[laughter]", content)

    def test_a_cue_of_only_brackets_survives_when_off(self):
        # With the strip off, "[music] Nobody knew why." keeps its text, so the
        # cue is not counted as no-text and does not vanish from the file.
        content = self._run({"strip_bracketed_soundcues": False})
        self.assertIn("[music] Nobody knew why.", content)

    def test_a_cue_of_only_brackets_is_dropped_when_on(self):
        # With the strip on it becomes empty, which is the point: a cue that is
        # nothing but a sound cue should not occupy the screen.
        content = self._run({"strip_bracketed_soundcues": True})
        self.assertNotIn("[music]", content)

    def test_the_speaker_marker_is_stripped_either_way(self):
        # ">>" removal is unconditional and must not become optional by accident.
        for prefs in ({"strip_bracketed_soundcues": True},
                      {"strip_bracketed_soundcues": False}):
            with self.subTest(prefs=prefs):
                self.assertNotIn(">>", self._run(prefs))

    def test_no_double_space_is_left_behind(self):
        # Removing a mid-sentence cue must not leave "that  and".
        content = self._run({"strip_bracketed_soundcues": True})
        self.assertNotIn("  ", content)
        self.assertIn("that and then", content)

    def test_timestamps_are_untouched_by_the_preference(self):
        # The preference only affects text; the layout's timing must be
        # identical either way, so the choice cannot shift a subtitle.
        def timings(text):
            return [line for line in text.split("\n") if "-->" in line]

        self.assertEqual(timings(self._run({"strip_bracketed_soundcues": True})),
                         timings(self._run({"strip_bracketed_soundcues": False})))

    def test_invalid_value_falls_back_to_stripping(self):
        # A malformed file must not silently change behaviour.
        with self.assertLogs("ytdl.preferences", level="WARNING"):
            content = self._run({"strip_bracketed_soundcues": "yes"})
        self.assertNotIn("[laughter]", content)


class TestDialogValidation(unittest.TestCase):
    """The Preferences dialog accepts a boolean and rejects anything else.

    The rules are duplicated from `_save_preferences` because that method needs
    a live Qt dialog; this keeps the rules themselves covered without one.
    """

    def _validate(self, payload):
        parsed = dict(payload)
        if "channel_name_map" not in parsed:
            parsed["channel_name_map"] = {}
        if not isinstance(parsed["channel_name_map"], dict):
            raise ValueError("channel_name_map must be a JSON object")
        if "strip_bracketed_soundcues" in parsed:
            if not isinstance(parsed["strip_bracketed_soundcues"], bool):
                raise ValueError("strip_bracketed_soundcues must be true or false")
        return parsed

    def test_booleans_pass(self):
        for value in (True, False):
            with self.subTest(value=value):
                self.assertEqual(
                    self._validate({"strip_bracketed_soundcues": value})
                    ["strip_bracketed_soundcues"], value)

    def test_non_booleans_are_refused(self):
        for value in ("true", 1, 0, None, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self._validate({"strip_bracketed_soundcues": value})

    def test_omitted_key_is_allowed(self):
        self.assertNotIn("strip_bracketed_soundcues", self._validate({}))

    def test_an_unknown_key_is_left_alone(self):
        # subtitle_words_per_cue was removed in 1.4.3. A leftover key in an
        # existing preferences.json must be ignored, not rejected - the app no
        # longer reads it, so refusing the whole file would lock a user out.
        for value in (99, "8", [], {}):
            with self.subTest(value=value):
                self.assertEqual(
                    self._validate({"subtitle_words_per_cue": value})
                    ["subtitle_words_per_cue"], value)


if __name__ == "__main__":
    unittest.main()
