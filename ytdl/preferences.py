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


def load_preferences():
    """Load preferences from preferences.json (macOS: ~/Library/Application Support/,
    Linux: ~/.config/)."""
    default_preferences = {
        "channel_name_map": {}
    }

    try:
        os.makedirs(PREFERENCES_DIR, exist_ok=True)
        if not os.path.exists(PREFERENCES_FILE):
            return default_preferences
        with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
            parsed = json.load(f)
        return parsed
    except Exception as e:
        logger.error(f"Error loading preferences: {e}")
        return default_preferences


def apply_preferences(parsed):
    """Replace the in-memory preferences state (called by the preferences
    dialog after a successful save)."""
    global preferences, CHANNEL_NAME_MAP
    preferences = parsed
    CHANNEL_NAME_MAP = parsed.get("channel_name_map", {})


# Loaded once at import time
preferences = load_preferences()
CHANNEL_NAME_MAP = preferences.get("channel_name_map", {})
