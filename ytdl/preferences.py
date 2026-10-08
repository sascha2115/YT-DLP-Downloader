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

# Subtitle pace preference: how many words one subtitle carries.
# The layout ceiling is derived from it (target + 2), so the two can
# never drift apart. Reading speed (words/sec) is deliberately NOT a
# preference - it is only a floor and every real video already speaks
# above it, so the setting would change nothing.
SUBTITLE_WORDS_MIN = 6
SUBTITLE_WORDS_MAX = 16
SUBTITLE_WORDS_DEFAULT = 8


def load_preferences():
    """Load preferences from preferences.json (macOS: ~/Library/Application Support/,
    Linux: ~/.config/)."""
    default_preferences = {
        "channel_name_map": {},
        "subtitle_words_per_cue": SUBTITLE_WORDS_DEFAULT,
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


def subtitle_layout_targets(prefs_dict=None):
    """Map the subtitle pace preference onto layout_cues() targets.

    `subtitle_words_per_cue` (6-16, default 10) is the user-facing
    knob: how many words one subtitle carries. Lower values split
    subtitles sooner, so the next cue starts earlier. Returns {} when
    the preference is unset or invalid, so the layout module's own
    defaults apply. Pass a dict to test a specific state; without one
    the live in-memory `preferences` are read at call time (module
    bindings go stale after apply_preferences()).
    """
    source = preferences if prefs_dict is None else prefs_dict
    raw = (source or {}).get("subtitle_words_per_cue")
    if raw is None:
        return {}
    if isinstance(raw, bool) or not isinstance(raw, int):
        logger.warning(
            "subtitle_words_per_cue is %s, not a whole number — using default",
            type(raw).__name__,
        )
        return {}
    if not SUBTITLE_WORDS_MIN <= raw <= SUBTITLE_WORDS_MAX:
        logger.warning(
            "subtitle_words_per_cue %d is outside %d-%d — clamping",
            raw, SUBTITLE_WORDS_MIN, SUBTITLE_WORDS_MAX,
        )
        raw = min(max(raw, SUBTITLE_WORDS_MIN), SUBTITLE_WORDS_MAX)
    return {"target_words": raw, "ceiling_words": raw + 2}


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
