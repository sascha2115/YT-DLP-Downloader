"""Standalone SponsorBlock lookup + EDL writer — prototype, no yt-dlp, no Qt.

PROTOTYPE: lives in temp/ and is only driven by test scripts. The app itself
(ytdl/) is untouched by this experiment.

The SponsorBlock API is public and keyed by video id alone, so the segment data
does not have to come out of an info fetch. This module does the whole job with
one HTTP request:

    1. extract_video_id(url)        - pure, reuses the site profiles in ytdl/sites.py
    2. fetch_sponsorblock_segments  - GET https://sponsor.ajay.app/api/skipSegments
    3. select_segments              - filter by the UI's checkbox categories
    4. write_edl                    - "<base>.edl" in the exact format create_edl_file() writes
    5. create_edl_only(gui)         - the ".edl only" download mode: no yt-dlp at all

Design decisions for the ".edl only" mode (locked in by the user):

    (a) The EDL basename comes from the normal Fetch Info flow. The mode runs
        exactly where start_download() would branch, i.e. AFTER the shared
        prelude that turns the info fetch's title into a sanitized folder and
        base filename — so the .edl lands where the media file would have.
    (b) The mode is a new ".edl only" radio button in the Type group, directly
        below "Subtitles only" (RADIO_GROUP_CONFIGS in ytdl/ui_build.py), not a
        separate button. It stores EDL_ONLY_MEDIA_TYPE in
        video_state["media_type"], and start_download() branches on it before
        build_command() — no yt-dlp process is ever started.

EDL format (identical to ytdl/download.py::create_edl_file):
    <start> <end> 0
    # action 0 = skip/cut; the player jumps, the media file stays untouched.

Run a live demo (one API call, no yt-dlp):

    python3 temp/sponsorblock_standalone.py "https://youtu.be/yePLuwL3FbM"

Usage from a test:

    from sponsorblock_standalone import extract_video_id, fetch_sponsorblock_segments
    segments, error = fetch_sponsorblock_segments("yePLuwL3FbM")
"""

import argparse
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ytdl.sites import DEFAULT_SITE, SUPPORTED_SITES, detect_site  # noqa: E402
from ytdl.widgets import SB_API_MAP  # noqa: E402

API_URL = "https://sponsor.ajay.app/api/skipSegments"
# Same category set check_sponsorblock() requests, so the standalone lookup
# sees exactly what the app's own lookup sees (everything, for the bar/EDL).
ALL_API_CATEGORIES = [
    "sponsor", "selfpromo", "interaction", "intro", "outro",
    # "hook" is a real API category (merged upstream, yt-dlp PR #14845) —
    # the UI's Hook checkbox selects it, so it must be requested here too.
    "preview", "hook", "music_offtopic", "filler", "poi_highlight",
    "exclusive_access", "chapter",
]
# The values of YTDLPDownloaderGUI.sb_checkbox_map (ytdl/ui_build.py) — the
# categories the UI can actually select, in the UI's own naming.
UI_CATEGORIES = [
    "sponsor", "selfpromo", "interaction", "intro",
    "ending", "preview", "hook", "tangents",
]
DEFAULT_TIMEOUT = 4.0  # SPONSORBLOCK_TIMEOUT_SECONDS equivalent

# video_state["media_type"] value of the new ".edl only" radio button in the
# Type group (decision b). start_download() branches on it before
# build_command(), so no yt-dlp process is ever started for this mode.
EDL_ONLY_MEDIA_TYPE = "edl_only"


def extract_video_id(url):
    """Return the YouTube video id for a URL, or "" if there is none.

    Same rules as YTDLPDownloaderGUI.extract_video_id(), minus the video_state
    cache (nothing to cache without a GUI). SponsorBlock is YouTube-only, so a
    non-YouTube site yields "".
    """
    if not url:
        return ""
    site = detect_site(url)
    if not SUPPORTED_SITES.get(site, {}).get("sponsorblock", False):
        return ""
    match = SUPPORTED_SITES[site]["id_regex"].search(url)
    return match.group(1) if match else ""


def fetch_sponsorblock_segments(video_id, categories=None, timeout=DEFAULT_TIMEOUT):
    """Fetch skip segments for one YouTube video id. No yt-dlp involved.

    Returns (segments, error):
      - (list, None)  on success; a 404 is a clean "no segments" -> ([], None)
      - ([], str)    on any HTTP/network failure, error is the message

    Each segment is the raw API object: {"category", "segment": [start, end],
    "UUID", "videoDuration", ...}.
    """
    if not video_id:
        return [], "no video id"
    payload = {"videoID": video_id, "category": categories or ALL_API_CATEGORIES}
    try:
        response = requests.get(API_URL, params=payload, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        return [], f"request error: {exc}"
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


def to_ui_category(api_category):
    """Map an API category name onto the UI checkbox's naming (outro -> ending)."""
    return SB_API_MAP.get(api_category, api_category)


def select_segments(segments, selected_ui_categories):
    """Keep the segments whose (UI-mapped) category is selected.

    selected_ui_categories is what get_selected_sb_categories() returns. A
    segment the UI has no checkbox for (music_offtopic, chapter, ...) matches
    only when the caller passes its raw API name too.
    """
    selected = set(selected_ui_categories)
    return [
        seg for seg in segments
        if to_ui_category(seg.get("category", "")) in selected
    ]


def build_edl_content(segments):
    """Render the EDL text: one "<start> <end> 0" line per segment.

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


def write_edl(base_path, segments):
    """Write <base_path>.edl (create_edl_file's naming) and return its path.

    base_path is the media file path (or the path without extension); the EDL
    must sit next to the video under the same basename, because that is how
    the player finds it.
    """
    edl_path = os.path.splitext(base_path)[0] + ".edl"
    content = build_edl_content(segments)
    with open(edl_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return edl_path


def create_edl_only(gui, fetch=fetch_sponsorblock_segments):
    """Write <title>.edl for the current video — the ".edl only" mode body.

    Designed to run exactly where start_download() would branch for
    media_type == EDL_ONLY_MEDIA_TYPE: AFTER the shared prelude (title gate,
    directory creation, update_video_state), BEFORE build_command(). So the
    GUI it receives has:

        gui.video_state["base_filename"] / ["output_dir"]  - set by the prelude
        gui.video_state["video_id"] / ["site"] / ["url"]   - set by the info fetch
        gui.title_entry, gui.sb_bar                        - as usual

    Segment source (decision a: the info fetch already ran):
      1. gui.sb_bar.segments — check_sponsorblock() fetched ALL categories for
         the visual bar during the info fetch, so normally zero HTTP here.
      2. A fresh API fetch if the bar is empty (bar cleared/lookup failed).

    Returns the list of applied segments (like create_edl_file()), or []
    with a message on gui's output panel — no file is written when nothing
    matched, mirroring the app's own EDL writer.
    """
    state = gui.video_state
    emit = lambda text: gui._emit("append_output", text)

    site = state.get("site", DEFAULT_SITE)
    if not SUPPORTED_SITES.get(site, {}).get("sponsorblock", False):
        emit("SponsorBlock: Not available for this site (YouTube only) — skipping")
        return []

    video_id = state.get("video_id") or extract_video_id(
        state.get("url") or state.get("clean_url") or ""
    )
    if not video_id:
        emit("SponsorBlock: Could not extract video ID from URL")
        return []

    # Bar segments first: they were fetched (all categories) by the info fetch.
    bar = getattr(gui, "sb_bar", None)
    segments = list(getattr(bar, "segments", []) or [])
    if not segments:
        segments, error = fetch(video_id)
        if error:
            emit(f"🚩 SponsorBlock lookup failed: {error}")
            return []

    categories = gui.get_selected_sb_categories()
    emit(f"📟 Selected SB categories: {categories}")
    selected = select_segments(segments, categories)
    # Drop zero-length markers (API's whole-video flag) before the emptiness
    # check: a selection of only those must say "nothing to skip" instead of
    # writing an empty .edl.
    selected = [s for s in selected if s["segment"][0] != s["segment"][1]]
    if not selected:
        emit("📟 No segments to skip found in selected categories")
        return []

    base_path = gui.get_full_path()
    if not base_path:
        emit("🚩 Error: No video title available")
        return []
    try:
        edl_path = write_edl(base_path, selected)
    except OSError as exc:
        emit(f"🚩 Error creating EDL file: {exc}")
        return []

    emit(f"🎬 Created EDL file: {os.path.basename(edl_path)}")
    for line in build_edl_content(selected).splitlines():
        emit(f"   {line}")
    return selected


def summarize(segments):
    """'<count> segment(s) (2 Sponsor, 1 Ending ...)' for test/demo output."""
    if not segments:
        return "0 segments"
    counts = {}
    for seg in segments:
        cat = to_ui_category(seg.get("category", "unknown"))
        counts[cat] = counts.get(cat, 0) + 1
    detail = ", ".join(f"{n} {name}" for name, n in sorted(counts.items()))
    return f"{len(segments)} segment(s) ({detail})"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Standalone SponsorBlock lookup + EDL writer (no yt-dlp)."
    )
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument(
        "--categories",
        default="sponsor,selfpromo,ending,tangents",
        help="comma-separated UI categories to include (default: %(default)s)",
    )
    parser.add_argument("--out", default=".", help="output directory (default: .)")
    parser.add_argument(
        "--name", default="", help="EDL basename (default: the video id)"
    )
    parser.add_argument("--fetch-only", action="store_true", help="no file write")
    args = parser.parse_args(argv)

    video_id = extract_video_id(args.url)
    if not video_id:
        print(f"✗ no YouTube video id in: {args.url}")
        return 1
    print(f"video id: {video_id}")

    segments, error = fetch_sponsorblock_segments(video_id)
    if error:
        print(f"✗ SponsorBlock: {error}")
        return 1
    print(f"all segments:     {summarize(segments)}")

    selected = [c.strip() for c in args.categories.split(",") if c.strip()]
    chosen = select_segments(segments, selected)
    print(f"selected ({','.join(selected)}): {summarize(chosen)}")

    if args.fetch_only:
        for seg in chosen:
            start, end = seg["segment"]
            print(f"  {start:>8.2f} - {end:>8.2f}  {seg.get('category')}")
        return 0

    os.makedirs(args.out, exist_ok=True)
    base = os.path.join(args.out, args.name or video_id)
    edl_path = write_edl(base, chosen)
    print(f"🎬 wrote {edl_path}")
    for line in build_edl_content(chosen).splitlines():
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
