"""Assorted pure helpers: binary lookup, paths, title/format/SRT-time."""

import glob
import os
import re
import shutil
import sys
import sysconfig
import unicodedata


def resource_path(relative_path):
    # In dev runs, assets live at the repo root - one level ABOVE this
    # package directory. In PyInstaller builds, sys._MEIPASS points at the
    # bundle root where the datas were unpacked.
    base_path = getattr(
        sys,
        "_MEIPASS",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    return os.path.join(base_path, relative_path)


def get_log_dir():
    """App log directory (macOS: ~/Library/Logs, Linux: XDG state dir)."""
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Logs/YT-DLP Downloader")
    # Linux & other Unix: XDG state directory
    return os.path.join(
        os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
        "YT-DLP Downloader",
    )


# ----------------------------------------------------------------------------------------------------
# Find binary in system PATH or common macOS locations
# ----------------------------------------------------------------------------------------------------
def find_binary(name):
    # First try to find in PATH
    binary_path = shutil.which(name)
    if binary_path:
        return binary_path

    # Pip console-scripts dir of the running Python (version-agnostic).
    # Catches e.g. /Library/Frameworks/Python.framework/Versions/3.14/bin/yt-dlp
    # without hardcoding the Python version.
    scripts_dir = sysconfig.get_path("scripts")
    if scripts_dir:
        pip_bin_path = os.path.join(scripts_dir, name)
        if os.path.isfile(pip_bin_path) and os.access(pip_bin_path, os.X_OK):
            return pip_bin_path

    # Fallback: common install locations (macOS + Linux)
    common_locations = [
        f"/usr/local/bin/{name}",
        f"/opt/homebrew/bin/{name}",
        f"/opt/local/bin/{name}",
        f"/home/linuxbrew/.linuxbrew/bin/{name}",  # Homebrew on Linux
        f"~/.deno/bin/{name}",  # Deno installer dir; often missing from GUI-launched PATH
        f"~/.local/bin/{name}",
    ]
    for location in common_locations:
        expanded_path = os.path.expanduser(location)
        if os.path.isfile(expanded_path) and os.access(expanded_path, os.X_OK):
            return expanded_path

    # Python.org framework installs (versioned subfolders). Broadly catches
    # pip-installed binaries when running as a PyInstaller bundle, where the
    # sysconfig lookup above resolves inside the app, not the system Python.
    for framework_bin in glob.glob(
        "/Library/Frameworks/Python.framework/Versions/*/bin"
    ):
        candidate = os.path.join(framework_bin, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    return name
    # Return name as-is, let it fail with helpful error

# Remove unwanted stuff from title
# ----------------------------------------------------------------------------------------------------
def sanitize_title(title):
    # Preprocessing
    title = title.replace(":", " - ")
    title = title.replace("! ", " - ")
    title = re.sub(r"[|–/]", "-", title)

    # German characters to preserve
    german_chars = "äöüÄÖÜß"
    # Character Normalization and Filtering via List Comprehension
    normalized_chars = [
        char
        if char in german_chars
        else "".join(
            c
            for c in unicodedata.normalize("NFD", char)
            if unicodedata.category(c) != "Mn"
        )
        for char in title
    ]
    # String Rebuilding
    title = "".join(normalized_chars)
    # Remove all characters except: word characters, spaces, hyphens, parentheses, and German umlauts
    sanitized = re.sub(r"[^\w\s\-\(\)äöüÄÖÜß]", "", title)
    # Clean up multiple spaces
    sanitized = re.sub(r"\s+", " ", sanitized)
    return sanitized.strip()


# ----------------------------------------------------------------------------------------------------
# Format video duration
# ----------------------------------------------------------------------------------------------------
def format_duration(total_seconds):
    try:
        total_seconds = int(float(total_seconds))
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return f"{hours:02}:{minutes:02}:{seconds:02}"
    except (ValueError, TypeError):
        return "N/A"


# ----------------------------------------------------------------------------------------------------
# Format file size
# ----------------------------------------------------------------------------------------------------
def format_filesize(num_bytes):
    if num_bytes is None:
        return "N/A"
    try:
        num_bytes = float(num_bytes)
        # Use 1000 to match macOS Finder decimal calculation
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if num_bytes < 1000.0:
                return f"{num_bytes:.2f} {unit}"
            num_bytes /= 1000.0
        return f"{num_bytes:.2f} PB"
    except (ValueError, TypeError):
        return "N/A"


# ----------------------------------------------------------------------------------------------------
# Convert SRT timestamp to seconds
# ----------------------------------------------------------------------------------------------------
def parse_srt_time(time_str):
    # Format: HH:MM:SS,mmm
    match = re.match(r"(\d+):(\d+):(\d+),(\d+)", time_str)
    if not match:
        return 0

    h, m, s, ms = map(int, match.groups())
    return h * 3600 + m * 60 + s + ms / 1000


# ----------------------------------------------------------------------------------------------------
# Convert seconds to SRT timestamp format
# ----------------------------------------------------------------------------------------------------
def format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)

    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
