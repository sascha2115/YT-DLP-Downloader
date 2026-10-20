"""Unit tests for the cookies_from_browser preference.

Run from the repo root:  python3 -m unittest temp.test_cookie_preference -v

Covers the pure readers in `ytdl/preferences.py`:
  - `cookies_from_browser_enabled()` - the on/off switch
  - `cookie_browser()` - the single place that decides whether yt-dlp gets
    `--cookies-from-browser`

No Qt needed.
"""

import json
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ytdl import preferences as P  # noqa: E402


class TestCookiesFromBrowserEnabled(unittest.TestCase):
    """The on/off switch, read from a dict."""

    def test_absent_key_defaults_to_off(self):
        self.assertFalse(P.cookies_from_browser_enabled({}))

    def test_explicit_true_and_false(self):
        self.assertTrue(P.cookies_from_browser_enabled(
            {"cookies_from_browser_enabled": True}))
        self.assertFalse(P.cookies_from_browser_enabled(
            {"cookies_from_browser_enabled": False}))

    def test_non_bool_falls_back_to_default(self):
        # A string is not a bool, and bool is a subclass of int - so only a
        # real bool counts.
        self.assertFalse(P.cookies_from_browser_enabled(
            {"cookies_from_browser_enabled": "yes"}))
        self.assertFalse(P.cookies_from_browser_enabled(
            {"cookies_from_browser_enabled": 1}))

    def test_missing_key_migrates_from_browser_name(self):
        # An existing install has a browser name but no switch: cookies were
        # in use before, so they stay in use.
        self.assertTrue(P.cookies_from_browser_enabled(
            {"cookies_from_browser": "firefox"}))
        self.assertFalse(P.cookies_from_browser_enabled(
            {"cookies_from_browser": ""}))

    def test_explicit_false_beats_a_configured_browser(self):
        self.assertFalse(P.cookies_from_browser_enabled(
            {"cookies_from_browser": "chrome",
             "cookies_from_browser_enabled": False}))


class TestCookieBrowser(unittest.TestCase):
    """The browser name that is actually passed to yt-dlp."""

    def test_off_switch_wins(self):
        self.assertEqual(P.cookie_browser({
            "cookies_from_browser": "firefox",
            "cookies_from_browser_enabled": False}), "")

    def test_on_with_browser(self):
        self.assertEqual(P.cookie_browser({
            "cookies_from_browser": "firefox",
            "cookies_from_browser_enabled": True}), "firefox")

    def test_on_without_browser(self):
        self.assertEqual(P.cookie_browser({
            "cookies_from_browser": "",
            "cookies_from_browser_enabled": True}), "")

    def test_profile_form_is_kept(self):
        self.assertEqual(P.cookie_browser({
            "cookies_from_browser": "firefox:1abc2def.default-release",
            "cookies_from_browser_enabled": True}),
            "firefox:1abc2def.default-release")

    def test_unknown_browser_is_dropped(self):
        self.assertEqual(P.cookie_browser({
            "cookies_from_browser": "netscape",
            "cookies_from_browser_enabled": True}), "")

    def test_whitespace_is_trimmed(self):
        self.assertEqual(P.cookie_browser({
            "cookies_from_browser": "  chrome  ",
            "cookies_from_browser_enabled": True}), "chrome")

    def test_non_string_browser_is_dropped(self):
        self.assertEqual(P.cookie_browser({
            "cookies_from_browser": 42,
            "cookies_from_browser_enabled": True}), "")


class TestLivePreferences(unittest.TestCase):
    """The readers work against the live in-memory state."""

    def setUp(self):
        self.saved = P.preferences

    def tearDown(self):
        P.apply_preferences(self.saved)

    def test_live_read(self):
        P.apply_preferences({
            "cookies_from_browser": "chrome",
            "cookies_from_browser_enabled": True,
        })
        self.assertTrue(P.cookies_from_browser_enabled())
        self.assertEqual(P.cookie_browser(), "chrome")

    def test_live_read_off(self):
        P.apply_preferences({
            "cookies_from_browser": "chrome",
            "cookies_from_browser_enabled": False,
        })
        self.assertFalse(P.cookies_from_browser_enabled())
        self.assertEqual(P.cookie_browser(), "")

    def test_wrong_typed_switch_is_normalized(self):
        P.apply_preferences({
            "cookies_from_browser": "chrome",
            "cookies_from_browser_enabled": "yes",
        })
        self.assertFalse(P.cookies_from_browser_enabled())


class _FakeSignal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _FakeLineEdit:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text

    def setText(self, value):
        self._text = value


class _FakeCheckBox:
    def __init__(self, checked=False):
        self._checked = checked

    def isChecked(self):
        return self._checked

    def setChecked(self, value):
        self._checked = value


class _FakeDialog:
    def __init__(self):
        self.accepted = False

    def accept(self):
        self.accepted = True


class _SaveHarness:
    """Minimal stand-in for the dialog mixin's host object."""

    def __init__(self):
        self.signals = type("Signals", (), {"append_output": _FakeSignal()})()


class TestSavePreferences(unittest.TestCase):
    """The form fields are authoritative for the two cookie keys."""

    def setUp(self):
        self.harness = _SaveHarness()
        self.saved = P.preferences
        self._patcher = None

    def tearDown(self):
        P.apply_preferences(self.saved)
        if self._patcher is not None:
            self._patcher.stop()

    def _patch_prefs_file(self):
        """Redirect the preferences file to a temp dir."""
        import tempfile
        tmp = tempfile.mkdtemp()
        self._patcher = unittest.mock.patch.multiple(
            P,
            PREFERENCES_DIR=tmp,
            PREFERENCES_FILE=os.path.join(tmp, "preferences.json"),
        )
        self._patcher.start()
        return self._patcher

    def test_form_writes_both_keys(self):
        self._patch_prefs_file()
        from ytdl.preferences_dialog import PreferencesDialogMixin

        mixin = PreferencesDialogMixin()
        mixin.signals = self.harness.signals
        text_edit = type("TE", (), {"toPlainText": lambda self: "{}"})()
        dialog = _FakeDialog()
        mixin._save_preferences(
            dialog, text_edit, _FakeCheckBox(True), _FakeLineEdit("firefox"))

        self.assertTrue(dialog.accepted)
        with open(P.PREFERENCES_FILE, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertTrue(saved["cookies_from_browser_enabled"])
        self.assertEqual(saved["cookies_from_browser"], "firefox")

    def test_switch_off_still_saves_the_browser_name(self):
        self._patch_prefs_file()
        from ytdl.preferences_dialog import PreferencesDialogMixin

        mixin = PreferencesDialogMixin()
        mixin.signals = self.harness.signals
        text_edit = type("TE", (), {"toPlainText": lambda self: "{}"})()
        dialog = _FakeDialog()
        mixin._save_preferences(
            dialog, text_edit, _FakeCheckBox(False), _FakeLineEdit("chrome"))

        with open(P.PREFERENCES_FILE, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertFalse(saved["cookies_from_browser_enabled"])
        self.assertEqual(saved["cookies_from_browser"], "chrome")

    def test_unknown_browser_is_rejected(self):
        self._patch_prefs_file()
        from ytdl.preferences_dialog import PreferencesDialogMixin

        mixin = PreferencesDialogMixin()
        mixin.signals = self.harness.signals
        text_edit = type("TE", (), {"toPlainText": lambda self: "{}"})()
        dialog = _FakeDialog()
        mixin._save_preferences(
            dialog, text_edit, _FakeCheckBox(True), _FakeLineEdit("netscape"))

        self.assertFalse(dialog.accepted)
        self.assertFalse(os.path.exists(P.PREFERENCES_FILE))
        self.assertIn("cookies_from_browser", self.harness.signals.append_output.emitted[0][0])


class TestShowFetchinfoCommand(unittest.TestCase):
    """The show_fetchinfo_command switch, read from a dict."""

    def test_absent_key_defaults_to_off(self):
        self.assertFalse(P.show_fetchinfo_command({}))

    def test_explicit_true_and_false(self):
        self.assertTrue(P.show_fetchinfo_command({"show_fetchinfo_command": True}))
        self.assertFalse(P.show_fetchinfo_command({"show_fetchinfo_command": False}))

    def test_non_bool_falls_back_to_off(self):
        # bool is a subclass of int, so only a real bool counts.
        self.assertFalse(P.show_fetchinfo_command({"show_fetchinfo_command": "yes"}))
        self.assertFalse(P.show_fetchinfo_command({"show_fetchinfo_command": 1}))


class TestSaveSwitches(unittest.TestCase):
    """The two standalone switches are saved from the form, not the JSON."""

    def setUp(self):
        self.harness = _SaveHarness()
        self.saved = P.preferences
        self._patcher = None

    def tearDown(self):
        P.apply_preferences(self.saved)
        if self._patcher is not None:
            self._patcher.stop()

    def _patch_prefs_file(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        self._patcher = unittest.mock.patch.multiple(
            P,
            PREFERENCES_DIR=tmp,
            PREFERENCES_FILE=os.path.join(tmp, "preferences.json"),
        )
        self._patcher.start()
        return self._patcher

    def _save(self, strip, show, editor_text="{}"):
        from ytdl.preferences_dialog import PreferencesDialogMixin

        mixin = PreferencesDialogMixin()
        mixin.signals = self.harness.signals
        text_edit = type("TE", (), {"toPlainText": lambda self: editor_text})()
        dialog = _FakeDialog()
        mixin._save_preferences(
            dialog, text_edit,
            _FakeCheckBox(False), _FakeLineEdit(""),
            _FakeCheckBox(strip), _FakeCheckBox(show),
        )
        return dialog

    def test_switches_are_saved_from_the_form(self):
        self._patch_prefs_file()
        dialog = self._save(strip=True, show=False)
        self.assertTrue(dialog.accepted)
        with open(P.PREFERENCES_FILE, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertTrue(saved["strip_bracketed_soundcues"])
        self.assertFalse(saved["show_fetchinfo_command"])

    def test_the_json_editor_cannot_override_a_switch(self):
        # The form owns these keys, so a value in the editor is ignored.
        self._patch_prefs_file()
        editor_text = json.dumps({
            "strip_bracketed_soundcues": True,
            "show_fetchinfo_command": True,
        })
        dialog = self._save(strip=False, show=False, editor_text=editor_text)
        self.assertTrue(dialog.accepted)
        with open(P.PREFERENCES_FILE, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertFalse(saved["strip_bracketed_soundcues"])
        self.assertFalse(saved["show_fetchinfo_command"])

    def test_the_channel_name_map_is_saved_from_the_editor(self):
        self._patch_prefs_file()
        editor_text = json.dumps({"channel_name_map": {"PowerfulJRE": "Joe Rogan"}})
        dialog = self._save(strip=True, show=True, editor_text=editor_text)
        self.assertTrue(dialog.accepted)
        with open(P.PREFERENCES_FILE, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["channel_name_map"], {"PowerfulJRE": "Joe Rogan"})

    def test_an_unknown_key_in_the_file_is_carried_over(self):
        # A hand-added entry the dialog does not know about must survive a
        # save, or the user silently loses it.
        self._patch_prefs_file()
        with open(P.PREFERENCES_FILE, "w", encoding="utf-8") as handle:
            json.dump({"my_own_key": 42, "channel_name_map": {}}, handle)
        dialog = self._save(strip=True, show=False)
        self.assertTrue(dialog.accepted)
        with open(P.PREFERENCES_FILE, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["my_own_key"], 42)

    def test_an_empty_editor_saves_an_empty_channel_map(self):
        self._patch_prefs_file()
        dialog = self._save(strip=True, show=False, editor_text="")
        self.assertTrue(dialog.accepted)
        with open(P.PREFERENCES_FILE, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["channel_name_map"], {})

    def test_the_editor_wins_over_the_map_on_disk(self):
        # The map in the editor is authoritative: an editor that drops the
        # key means an empty map, not "keep the old one".
        self._patch_prefs_file()
        with open(P.PREFERENCES_FILE, "w", encoding="utf-8") as handle:
            json.dump({"channel_name_map": {"Old": "Old"}}, handle)
        dialog = self._save(strip=True, show=False, editor_text="{}")
        self.assertTrue(dialog.accepted)
        with open(P.PREFERENCES_FILE, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["channel_name_map"], {})

    def test_a_missing_form_widget_keeps_the_value_from_the_file(self):
        # A caller that does not supply a widget must not wipe that setting.
        self._patch_prefs_file()
        with open(P.PREFERENCES_FILE, "w", encoding="utf-8") as handle:
            json.dump({
                "strip_bracketed_soundcues": True,
                "show_fetchinfo_command": True,
                "channel_name_map": {},
            }, handle)

        from ytdl.preferences_dialog import PreferencesDialogMixin

        mixin = PreferencesDialogMixin()
        mixin.signals = self.harness.signals
        text_edit = type("TE", (), {"toPlainText": lambda self: "{}"})()
        dialog = _FakeDialog()
        mixin._save_preferences(dialog, text_edit)

        self.assertTrue(dialog.accepted)
        with open(P.PREFERENCES_FILE, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertTrue(saved["strip_bracketed_soundcues"])
        self.assertTrue(saved["show_fetchinfo_command"])


class TestInvalidUtf8Preferences(unittest.TestCase):
    """A preferences file that is not valid UTF-8 must not break the dialog.

    The two read paths (the dialog's load and its carry-over helper) both
    promise to degrade to "empty" on any problem. A corrupted file used to
    raise UnicodeDecodeError out of open(), which stopped the dialog from
    opening and stopped a save.
    """

    def setUp(self):
        self.harness = _SaveHarness()
        self.saved = P.preferences
        self._patcher = None

    def tearDown(self):
        P.apply_preferences(self.saved)
        if self._patcher is not None:
            self._patcher.stop()

    def _patch_prefs_file(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        self._patcher = unittest.mock.patch.multiple(
            P,
            PREFERENCES_DIR=tmp,
            PREFERENCES_FILE=os.path.join(tmp, "preferences.json"),
        )
        self._patcher.start()
        return tmp

    def _write_invalid_utf8(self):
        tmp = self._patch_prefs_file()
        with open(P.PREFERENCES_FILE, "wb") as handle:
            handle.write(b'{"channel_name_map": {"a": "\xff\xfe"}}')
        return tmp

    def test_read_preferences_file_returns_empty(self):
        self._write_invalid_utf8()
        from ytdl.preferences_dialog import PreferencesDialogMixin

        mixin = PreferencesDialogMixin()
        mixin.signals = self.harness.signals
        self.assertEqual(mixin._read_preferences_file(), {})

    def test_a_save_over_a_broken_file_still_succeeds(self):
        self._write_invalid_utf8()
        from ytdl.preferences_dialog import PreferencesDialogMixin

        mixin = PreferencesDialogMixin()
        mixin.signals = self.harness.signals
        text_edit = type("TE", (), {"toPlainText": lambda self: "{}"})()
        dialog = _FakeDialog()
        mixin._save_preferences(
            dialog, text_edit,
            _FakeCheckBox(False), _FakeLineEdit(""),
            _FakeCheckBox(True), _FakeCheckBox(True),
        )

        self.assertTrue(dialog.accepted)
        with open(P.PREFERENCES_FILE, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertTrue(saved["strip_bracketed_soundcues"])
        self.assertTrue(saved["show_fetchinfo_command"])

    def test_the_file_is_replaced_with_valid_utf8(self):
        self._write_invalid_utf8()
        from ytdl.preferences_dialog import PreferencesDialogMixin

        mixin = PreferencesDialogMixin()
        mixin.signals = self.harness.signals
        text_edit = type("TE", (), {"toPlainText": lambda self: "{}"})()
        mixin._save_preferences(
            _FakeDialog(), text_edit,
            _FakeCheckBox(False), _FakeLineEdit(""),
            _FakeCheckBox(False), _FakeCheckBox(False),
        )

        # The broken file is now readable as UTF-8 again.
        with open(P.PREFERENCES_FILE, encoding="utf-8") as handle:
            json.load(handle)


class TestPreferenceTooltips(unittest.TestCase):
    """The help text for each preference lives in its widget's tooltip.

    There is no separate explanations section in the dialog any more, so
    the tooltip is the only place a user can read what a key does. These
    tests are pure (no Qt): they check the texts in `ytdl/preferences.py`,
    which the dialog hands to the widgets.
    """

    ALL_TOOLTIPS = (
        P.TOOLTIP_COOKIES_FROM_BROWSER_ENABLED,
        P.TOOLTIP_COOKIES_FROM_BROWSER,
        P.TOOLTIP_STRIP_BRACKETED_SOUNDCUES,
        P.TOOLTIP_SHOW_FETCHINFO_COMMAND,
        P.TOOLTIP_CHANNEL_NAME_MAP_EDITOR,
    )

    def test_every_tooltip_is_non_empty(self):
        for tooltip in self.ALL_TOOLTIPS:
            self.assertTrue(tooltip.strip(), "a tooltip is empty")

    def test_each_tooltip_names_its_own_key(self):
        # A tooltip that does not mention the key cannot help the user map
        # it back to the preferences file.
        self.assertIn("cookies_from_browser_enabled",
                      P.TOOLTIP_COOKIES_FROM_BROWSER_ENABLED)
        self.assertIn("cookies_from_browser",
                      P.TOOLTIP_COOKIES_FROM_BROWSER)
        self.assertIn("strip_bracketed_soundcues",
                      P.TOOLTIP_STRIP_BRACKETED_SOUNDCUES)
        self.assertIn("show_fetchinfo_command",
                      P.TOOLTIP_SHOW_FETCHINFO_COMMAND)
        self.assertIn("channel_name_map",
                      P.TOOLTIP_CHANNEL_NAME_MAP_EDITOR)

    def test_the_tooltips_state_the_type_and_the_default(self):
        # The old explanations section spelled out "true or false
        # (default X)" for every boolean; that has to survive the move
        # into the tooltips.
        for tooltip in (P.TOOLTIP_COOKIES_FROM_BROWSER_ENABLED,
                        P.TOOLTIP_STRIP_BRACKETED_SOUNDCUES,
                        P.TOOLTIP_SHOW_FETCHINFO_COMMAND):
            self.assertIn("true or false", tooltip)
            self.assertIn("default", tooltip)

    def test_the_strip_tooltip_reports_the_real_default(self):
        # The default is interpolated from the constant, so a change to
        # the constant cannot leave a stale "default true" in the text.
        expected = "default true" if P.STRIP_BRACKETED_SOUNDCUES_DEFAULT \
            else "default false"
        self.assertIn(expected, P.TOOLTIP_STRIP_BRACKETED_SOUNDCUES)

    def test_the_browser_tooltip_lists_every_valid_browser(self):
        for browser in P.VALID_COOKIE_BROWSERS:
            self.assertIn(browser, P.TOOLTIP_COOKIES_FROM_BROWSER)

    def test_the_browser_tooltip_keeps_the_firefox_profile_hint(self):
        # A recurring mistake: "default-release" instead of the full
        # profile directory name.
        self.assertIn("1abc2def.default-release",
                      P.TOOLTIP_COOKIES_FROM_BROWSER)
        self.assertIn("not just default-release",
                      P.TOOLTIP_COOKIES_FROM_BROWSER)


if __name__ == "__main__":
    unittest.main()
