"""Pure SponsorBlock / EDL helpers: no Qt, no ``self``.

Extracted from ``ytdl/download.py`` so ``DownloadMixin`` stops growing past
pylint's line limit. Everything here is module-level: functions take arguments
and return values, touching no instance state. The two network-touching
functions (``fetch_sponsorblock_segments``, ``_segments_from_response``) are
pure in the sense that they take a video id / response and return
``(segments, error)`` — they just happen to make one HTTP request.
"""

import re
import requests

from ytdl.config import SPONSORBLOCK_API_URL, SPONSORBLOCK_TIMEOUT_SECONDS
from ytdl.sites import SUPPORTED_SITES, detect_site
from ytdl.widgets import SB_API_MAP

# .edl-only media type: start_download() branches on video_state["wants_edl"]
# before build_command(), so no yt-dlp process is ever started for this mode.
# (Replaced the old "media_type" radio enum with checkboxes; EDL_ONLY_MEDIA_TYPE
# is kept for backward-compat references.)
EDL_ONLY_MEDIA_TYPE = "edl_only"

# Every SponsorBlock API category. check_sponsorblock() requests it so the
# visual bar shows everything, and fetch_sponsorblock_segments() below
# requests it so the standalone .edl writer sees exactly the same segments
# as the bar.
SB_ALL_API_CATEGORIES = [
    "sponsor", "selfpromo", "interaction", "intro", "outro",
    # "hook" is a real API category (merged upstream, yt-dlp PR #14845) —
    # the UI's Hook checkbox selects it, so it must be requested here too.
    "preview", "hook", "music_offtopic", "filler", "poi_highlight",
    "exclusive_access", "chapter",
]


def to_ui_category(api_category):
    """Map a raw SponsorBlock API category onto the UI checkbox's naming.

    The checkboxes speak the UI's naming (ending, tangents, highlight) while
    the API — and yt-dlp's sponsorblock_chapters, which copies its categories
    verbatim — speaks the raw one (outro, filler, poi_highlight). SB_API_MAP
    is the translation; categories the UI has no checkbox for
    (music_offtopic, chapter, ...) pass through unchanged.
    """
    return SB_API_MAP.get(api_category, api_category)


def select_segments(segments, selected_ui_categories):
    """Keep the raw API segments whose category maps to a selected UI category.

    `selected_ui_categories` is what `get_selected_sb_categories()` returns:
    UI checkbox names such as "ending", "tangents", "highlight". Each segment
    carries its raw API category (e.g. "outro", "filler", "poi_highlight").
    The segment's raw category is translated to UI naming via `to_ui_category()`
    before comparison, so an "Ending"-only selection matches raw "outro"
    segments. Categories without a UI checkbox (e.g. "music_offtopic") pass
    through `to_ui_category()` unchanged, so they match only when the caller
    passes that exact name.
    """
    selected = set(selected_ui_categories)
    return [
        seg for seg in segments
        if to_ui_category(seg.get("category", "")) in selected
    ]


def build_edl_content(segments):
    """Render the EDL text: one "<start> <end> 0" line per raw API segment.

    Zero-length segments (the API's whole-video marker) are skipped — a jump
    range with start == end is meaningless to a player.
    """
    lines = []
    for seg in segments:
        start, end = seg["segment"]
        if start == end:
            continue
        lines.append(f"{start} {end} 0")
    return "\n".join(lines) + ("\n" if lines else "")


def write_edl(edl_path, segments):
    """Write the EDL text to edl_path and return that path.

    The caller names the file — there is no splitext() guesswork here:
    create_edl_only() appends ".edl" to the extensionless base path itself,
    because a splitext() would eat a dotted title suffix ("Lecture 1.5" →
    "Lecture 1.edl") and the player looks the file up under the media's
    exact basename.
    """
    with open(edl_path, "w", encoding="utf-8") as handle:
        handle.write(build_edl_content(segments))
    return edl_path


def _extract_video_id(url):
    """Return the video id for a URL, or "" if there is none.

    Pure, read-only twin of YTDLPDownloaderGUI.extract_video_id(): same site
    profile regexes, but no video_state cache to touch — the ".edl only"
    worker must not mutate state the main thread reads.
    """
    if not url:
        return ""
    site = detect_site(url)
    match = SUPPORTED_SITES[site]["id_regex"].search(url)
    return match.group(1) if match else ""


def fetch_sponsorblock_segments(video_id, timeout=SPONSORBLOCK_TIMEOUT_SECONDS):
    """One direct SponsorBlock request for one video id. No yt-dlp involved.

    Returns (segments, error): (list, None) on success — a 404 is a clean
    "no segments" -> ([], None); ([], str) on any HTTP/network failure.
    Each segment is the raw API object: {"category", "segment": [start, end],
    "UUID", "videoDuration", ...}.
    """
    if not video_id:
        return [], "no video id"
    payload = {"videoID": video_id, "category": SB_ALL_API_CATEGORIES}
    try:
        response = requests.get(
            SPONSORBLOCK_API_URL, params=payload, timeout=timeout
        )
    except requests.exceptions.RequestException as exc:
        return [], f"request error: {exc}"
    return _segments_from_response(response)


def _segments_from_response(response):
    """Validate one SponsorBlock getSegments HTTP response -> (segments, error).

    Split out of fetch_sponsorblock_segments() purely for readability: a 404
    is a clean "this video has no segments" ([], None); any other non-200
    status or unusable body is an error ([], str).
    """
    if response.status_code == 404:
        return [], None
    if response.status_code != 200:
        return [], f"API returned status {response.status_code}"
    try:
        segments = response.json()
    except ValueError as exc:
        return [], f"invalid JSON: {exc}"
    if not isinstance(segments, list):
        return [], "unexpected payload shape"
    return segments, None