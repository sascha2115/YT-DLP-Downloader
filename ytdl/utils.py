"""Assorted pure helpers: binary lookup, paths, title/format/SRT-time."""

import glob
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import unicodedata

from ytdl import APP_VERSION


# ------------------------------------------------------------------------------------------
# Subtitle language keys
# -----------------------------------------------------------------------------
# Site subtitle keys may use ISO 639-2 while the UI checkboxes use 639-1
# (ARD Mediathek and ZDF Mediathek report "deu" for German). Map a site key
# back to the base UI code; add entries when a site reports another variant.
SUBTITLE_LANG_ALIASES = {
    "deu": "de",
    "ger": "de",
}


def canonical_subtitle_lang(code):
    """UI language code (e.g. "de") for a site's subtitle key ("deu", "de-auto")."""
    base = str(code).split("-", 1)[0].lower()
    return SUBTITLE_LANG_ALIASES.get(base, base)


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
# Build identity
# ----------------------------------------------------------------------------------------------------
# The commit the running code came from, so a bug report can be tied to an
# exact checkout rather than to a version number that may cover many commits.
# Resolved once and cached: it shells out to git, so it must not run per call,
# and it must never be able to fail the app.
_GIT_COMMIT_CACHE = None
# Guard the shape, not just git's exit code: a wrapper or alias could print
# something that is not a hash at all.
_GIT_COMMIT_RE = re.compile(r"[0-9a-f]{7,40}")
# A packaged bundle ships no repository, so it cannot run git. The build
# scripts stamp the commit into this file and bundle it alongside assets/,
# and resource_path() finds it in both a source run and a PyInstaller bundle.
BUILD_COMMIT_FILENAME = "BUILD_COMMIT"


def _baked_build_commit():
    """Commit stamped in by build-macos.sh / build-linux.sh; "" if absent.

    This is the only source of truth inside a packaged .app, where there is
    no .git to ask. It is a fallback for source runs, never a replacement:
    see get_git_commit_short() for why live git wins there.
    """
    try:
        with open(resource_path(BUILD_COMMIT_FILENAME), "r", encoding="utf-8") as f:
            commit = f.read().strip()
    except OSError:
        return ""
    return commit if _GIT_COMMIT_RE.fullmatch(commit) else ""


def get_git_commit_short():
    """Short commit hash of the running code; "" when unknown.

    Every failure mode collapses to "": no git binary, no .git directory (a
    PyInstaller bundle), a timeout, a nonzero exit or output that is not a
    hash. The header line then shows the version alone instead of the app
    refusing to start over a cosmetic detail.
    """
    global _GIT_COMMIT_CACHE
    if _GIT_COMMIT_CACHE is not None:
        return _GIT_COMMIT_CACHE

    # Unknown until proven otherwise, so an early return still caches the
    # negative answer and a broken git is not re-shelled on every call.
    _GIT_COMMIT_CACHE = ""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(repo_root, ".git")):
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None and result.returncode == 0:
            commit = result.stdout.strip()
            if _GIT_COMMIT_RE.fullmatch(commit):
                _GIT_COMMIT_CACHE = commit
                return _GIT_COMMIT_CACHE

    # Live git is preferred over the baked file on purpose: in a source run
    # HEAD is what is actually executing, while a BUILD_COMMIT left in the
    # tree by an earlier build describes what was bundled then. A bundle has
    # no .git, so it lands here and this is its only answer.
    #
    # The fallback must be cached like any other answer. Returning it without
    # storing it left _GIT_COMMIT_CACHE at "", and since "" is not None the
    # "already resolved" check above then returned that empty string on every
    # later call - so a packaged app showed the commit in the startup header
    # and lost it after the first clear_output().
    _GIT_COMMIT_CACHE = _GIT_COMMIT_CACHE or _baked_build_commit()
    return _GIT_COMMIT_CACHE


def version_header():
    """Output-panel header: version, plus the commit when it is known."""
    commit = get_git_commit_short()
    if commit:
        return f"YT-DLP Downloader {APP_VERSION} [{commit}]"
    return f"YT-DLP Downloader {APP_VERSION}"


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


# ----------------------------------------------------------------------------------------------------
# Describe what a subtitle layout run did
# ----------------------------------------------------------------------------------------------------
def format_subtitle_stats(cues_in, cues_out, counters=None, name=None):
    """One-line summary of a subtitle layout run.

    "Title.srt: 2957 in → 1132 out · 1114 two-line · 3.28 wps · 638 fast-paced"

    "in → out" and the two-line count always describe what the run produced.
    `counters` may carry wps (the rate the video was laid out at, so a viewer
    can tell a dense video from a packing failure) and `fast-paced` (cues that
    could not be given their full reading time, because the speaker is faster
    than a comfortable reader - a fact about the material, not a fault).
    Everything except the two-line count is omitted when it is zero or missing.

    Pure formatter (no Qt, no filesystem): the counts are taken where the layout
    runs, this only renders them.
    """
    counters = counters or {}
    parts = [f"{cues_in} in → {cues_out} out",
             f"{counters.get('two_line', 0)} two-line"]
    labels = (
        ("wps", "wps"),
        ("over_target", "fast-paced"),
        ("no_text", "no text"),
        ("too_short", "too short"),
        ("dropped", "dropped"),
    )
    for key, label in labels:
        value = counters.get(key)
        if value:
            parts.append(f"{value} {label}")
    summary = " · ".join(parts)
    return f"{name}: {summary}" if name else summary
