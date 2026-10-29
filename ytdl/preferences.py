"""Runtime preferences (macOS: ~/Library/Application Support, Linux: ~/.config).

Owns the preferences file path, loading, and the in-memory state
(`preferences` / `CHANNEL_NAME_MAP`). Mutate via `apply_preferences()` —
module-level `from ytdl.preferences import CHANNEL_NAME_MAP` bindings go
stale after a save, so read live values through this module instead.
"""

import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

if sys.platform == "darwin":
    PREFERENCES_DIR = os.path.expanduser(
        "~/Library/Application Support/YT-DLP Downloader"
    )
else:
    # Linux & other Unix: XDG config directory
    PREFERENCES_DIR = os.path.join(
        os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
        "YT-DLP Downloader",
    )
PREFERENCES_FILE = os.path.join(PREFERENCES_DIR, "preferences.json")

# Strip bracketed sound cues ("[laughter]", "[snorts]", "[musik]") from
# subtitles. Default on: automatic captions have always had them removed, so
# turning this off is a way to see the raw ASR text, not a change of behaviour.
STRIP_BRACKETED_SOUNDCUES_DEFAULT = True

# Cookies from browser: one of "brave", "chrome", "chromium", "edge",
# "firefox", "opera", "safari", "vivaldi", "whale", or "firefox:profile"
# (yt-dlp's BROWSER:PROFILE form). Default off.
VALID_COOKIE_BROWSERS = (
    "brave", "chrome", "chromium", "edge", "firefox",
    "opera", "safari", "vivaldi", "whale",
)

# Whether the browser cookies are used at all. The browser name lives in its
# own key ("cookies_from_browser"); this key is the on/off switch next to it.
COOKIES_FROM_BROWSER_ENABLED_DEFAULT = False

# The help text for each preference. It is shown as the tooltip of the widget
# that owns the key, so the dialog needs no separate explanations section.
# These live here (not in the dialog) so they can be checked without Qt.
TOOLTIP_COOKIES_FROM_BROWSER_ENABLED = (
    "cookies_from_browser_enabled: true or false (default false). The "
    "on/off switch for browser cookies.\n"
    "Reads cookies from your browser for authenticated requests "
    "(fixes 403/Sign-in/age-restricted videos). Only used when the "
    "browser name below is set."
)

TOOLTIP_COOKIES_FROM_BROWSER = (
    "cookies_from_browser: empty string (default) or one of brave, "
    "chrome, chromium, edge, firefox, opera, safari, vivaldi, whale "
    "(or firefox:profile_dir). The browser to read cookies from.\n"
    "For Firefox use the full profile directory name, e.g. "
    "firefox:1abc2def.default-release, not just default-release.\n"
    "Chromium-based browsers must be closed first (they lock the cookie DB)."
)

TOOLTIP_STRIP_BRACKETED_SOUNDCUES = (
    "strip_bracketed_soundcues: true or false (default "
    f"{'true' if STRIP_BRACKETED_SOUNDCUES_DEFAULT else 'false'}). "
    "Removes bracketed sound cues such as [laughter], [snorts] and "
    "[musik] from subtitles; false keeps them."
)

TOOLTIP_SHOW_FETCHINFO_COMMAND = (
    "show_fetchinfo_command: true or false (default false). Logs the "
    "info-fetch yt-dlp command (with cookies/JS runtime) to the "
    "output panel for debugging."
)

TOOLTIP_CHANNEL_NAME_MAP_EDITOR = (
    "The four switches above are set in the form; this editor holds "
    "only channel_name_map, a JSON object mapping a channel name to "
    "the name to show for it.\n"
    f"Editing: {PREFERENCES_FILE}"
)

# Longest gap between spoken phrases before a subtitle cue is broken into two.
# Below this, phrases stay in the same cue (YouTube's own rolling look).
# Above this, a blank pause is inserted and the next phrase starts a new cue.
PAUSE_THRESHOLD_MS_DEFAULT = 2000

TOOLTIP_PAUSE_THRESHOLD_MS = (
    "pause_threshold_ms: integer milliseconds (default 2000). When "
    "subtitles are downloaded in JSON3 format, the gap between spoken "
    "phrases is measured from per-word timestamps. If a gap exceeds this "
    "threshold, the cue is broken there — a blank pause appears on "
    "screen and the next phrase starts a new cue. Below it, phrases "
    "stay in the same cue (YouTube's own rolling look). Set to 0 to "
    "disable pause-based splitting."
)


def validate_cookies_from_browser(value):
    """Validate cookies_from_browser value. Returns (is_valid, error_msg)."""
    if not isinstance(value, str):
        return False, "must be a string"
    # Accept browser or browser:profile (yt-dlp form)
    browser_part = value.split(":")[0].lower()
    if browser_part not in VALID_COOKIE_BROWSERS:
        return False, f"unknown browser '{browser_part}' — expected one of {', '.join(VALID_COOKIE_BROWSERS)}"
    return True, ""


def cookies_from_browser_enabled(prefs_dict=None):
    """Whether cookies from the browser should be used (the on/off switch).

    Reads `cookies_from_browser_enabled` from the live preferences at call
    time. An absent key is derived from the browser name: a non-empty
    `cookies_from_browser` means the user had configured cookies before the
    switch existed, so it stays enabled (otherwise an upgrade would silently
    drop their cookies). Everything else falls back to the default — off.
    """
    source = preferences if prefs_dict is None else prefs_dict
    raw = (source or {}).get("cookies_from_browser_enabled")
    if isinstance(raw, bool):
        return raw
    if raw is not None:
        logger.warning(
            "cookies_from_browser_enabled is %s, not true or false — using %s",
            type(raw).__name__,
            "on" if COOKIES_FROM_BROWSER_ENABLED_DEFAULT else "off",
        )
        return bool(COOKIES_FROM_BROWSER_ENABLED_DEFAULT)
    # Absent key: migrate. A configured browser name means cookies were in
    # use before the switch existed.
    browser = (source or {}).get("cookies_from_browser")
    if isinstance(browser, str) and browser.strip():
        return True
    return bool(COOKIES_FROM_BROWSER_ENABLED_DEFAULT)


def cookie_browser(prefs_dict=None):
    """The browser to take cookies from, or "" when cookies are off.

    This is the single place that decides whether `--cookies-from-browser`
    is passed to yt-dlp: the switch must be on AND a browser name must be
    set. Both the info fetch and the download build their command from it.
    """
    if not cookies_from_browser_enabled(prefs_dict):
        return ""
    source = preferences if prefs_dict is None else prefs_dict
    browser = (source or {}).get("cookies_from_browser")
    if not isinstance(browser, str):
        return ""
    browser = browser.strip()
    if not browser:
        return ""
    valid, _ = validate_cookies_from_browser(browser)
    return browser if valid else ""


def strip_bracketed_soundcues(prefs_dict=None):
    """Whether to remove bracketed sound cues like "[laughter]" from subtitles.

    Reads `strip_bracketed_soundcues` from the live preferences at call time and
    always answers with a real bool. An absent key, or one that is not a JSON
    true/false (a string, a number, a list), falls back to
    `STRIP_BRACKETED_SOUNDCUES_DEFAULT` - on - so a malformed file cannot
    silently switch the behaviour; a wrong-typed value is logged. Pass a dict to
    test a specific state.
    """
    source = preferences if prefs_dict is None else prefs_dict
    raw = (source or {}).get("strip_bracketed_soundcues")
    if isinstance(raw, bool):
        return raw
    if raw is not None:
        logger.warning(
            "strip_bracketed_soundcues is %s, not true or false — using %s",
            type(raw).__name__,
            "on" if STRIP_BRACKETED_SOUNDCUES_DEFAULT else "off",
        )
    # Absent, or unusable: fall back to the default, coerced so the answer is
    # always a real bool even if the constant itself were mistyped.
    return bool(STRIP_BRACKETED_SOUNDCUES_DEFAULT)


def show_fetchinfo_command(prefs_dict=None):
    """Whether the info-fetch yt-dlp command is logged to the output panel.

    Same contract as the other boolean preferences: a real bool is used
    as-is, anything else falls back to the default (off).
    """
    source = preferences if prefs_dict is None else prefs_dict
    raw = (source or {}).get("show_fetchinfo_command")
    if isinstance(raw, bool):
        return raw
    if raw is not None:
        logger.warning(
            "show_fetchinfo_command is %s, not true or false — using off",
            type(raw).__name__,
        )
    return False


def pause_threshold_ms(prefs_dict=None):
    """Longest gap between spoken phrases before a subtitle cue is broken.

    Returns the value in milliseconds as an int. A malformed or missing
    value falls back to the default (2000ms = 2 seconds).
    """
    source = preferences if prefs_dict is None else prefs_dict
    raw = (source or {}).get("pause_threshold_ms")
    if isinstance(raw, bool):
        logger.warning("pause_threshold_ms is bool, not a number — using default")
        return PAUSE_THRESHOLD_MS_DEFAULT
    if isinstance(raw, (int, float)):
        return int(raw)
    if raw is not None:
        logger.warning(
            f"pause_threshold_ms is {type(raw).__name__}, not a number — using default"
        )
    return PAUSE_THRESHOLD_MS_DEFAULT


def load_preferences():
    """Load preferences from preferences.json (macOS: ~/Library/Application Support/,
    Linux: ~/.config/)."""
    default_preferences = {
        "channel_name_map": {},
        "strip_bracketed_soundcues": STRIP_BRACKETED_SOUNDCUES_DEFAULT,
        "cookies_from_browser": "",
        "cookies_from_browser_enabled": COOKIES_FROM_BROWSER_ENABLED_DEFAULT,
        "show_fetchinfo_command": False,
        "pause_threshold_ms": PAUSE_THRESHOLD_MS_DEFAULT,
    }

    try:
        os.makedirs(PREFERENCES_DIR, exist_ok=True)
        if not os.path.exists(PREFERENCES_FILE):
            return default_preferences
        with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
            parsed = json.load(f)
        if not isinstance(parsed, dict):
            logger.warning(
                "Preferences file contains %s instead of an object — using defaults",
                type(parsed).__name__,
            )
            return default_preferences
        return parsed
    except Exception as e:
        logger.error(f"Error loading preferences: {e}")
        return default_preferences


def apply_preferences(parsed):
    """Replace the in-memory preferences state (called by the preferences
    dialog after a successful save)."""
    global preferences, CHANNEL_NAME_MAP
    if not isinstance(parsed, dict):
        logger.warning("apply_preferences received non-dict (%s) — ignoring", type(parsed).__name__)
        return
    preferences = parsed
    CHANNEL_NAME_MAP = parsed.get("channel_name_map", {})
    if not isinstance(CHANNEL_NAME_MAP, dict):
        logger.warning("channel_name_map is not an object — resetting to empty map")
        CHANNEL_NAME_MAP = {}
    # Validate cookies_from_browser if present
    if "cookies_from_browser" in parsed:
        valid, err = validate_cookies_from_browser(parsed["cookies_from_browser"])
        if not valid and parsed["cookies_from_browser"]:
            logger.warning("cookies_from_browser %s — ignoring", err)
            preferences["cookies_from_browser"] = ""
    # The on/off switch is a real bool, like strip_bracketed_soundcues.
    if "cookies_from_browser_enabled" in parsed:
        if not isinstance(parsed["cookies_from_browser_enabled"], bool):
            logger.warning(
                "cookies_from_browser_enabled is not true or false — using %s",
                "on" if COOKIES_FROM_BROWSER_ENABLED_DEFAULT else "off",
            )
            preferences["cookies_from_browser_enabled"] = bool(
                COOKIES_FROM_BROWSER_ENABLED_DEFAULT
            )
    # pause_threshold_ms is an integer number of milliseconds.
    if "pause_threshold_ms" in parsed:
        raw = parsed["pause_threshold_ms"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            logger.warning(
                "pause_threshold_ms is not a number — using default %d ms",
                PAUSE_THRESHOLD_MS_DEFAULT,
            )
            preferences["pause_threshold_ms"] = PAUSE_THRESHOLD_MS_DEFAULT


# Loaded once at import time
preferences = load_preferences()
CHANNEL_NAME_MAP = preferences.get("channel_name_map", {})
if not isinstance(CHANNEL_NAME_MAP, dict):
    logger.warning("channel_name_map is not an object — resetting to empty map")
    CHANNEL_NAME_MAP = {}
