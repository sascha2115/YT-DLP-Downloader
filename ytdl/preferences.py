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


def load_preferences():
    """Load preferences from preferences.json (macOS: ~/Library/Application Support/,
    Linux: ~/.config/)."""
    default_preferences = {
        "channel_name_map": {},
        "strip_bracketed_soundcues": STRIP_BRACKETED_SOUNDCUES_DEFAULT,
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


# Loaded once at import time
preferences = load_preferences()
CHANNEL_NAME_MAP = preferences.get("channel_name_map", {})
if not isinstance(CHANNEL_NAME_MAP, dict):
    logger.warning("channel_name_map is not an object — resetting to empty map")
    CHANNEL_NAME_MAP = {}
