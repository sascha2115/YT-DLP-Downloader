"""Sidecar file creation (NFO, EDL) — extracted from ytdl/download.py.

Mixin for YTDLPDownloaderGUI; methods access shared state via ``self``.
Provides failure-path sidecars (`.nfo`, `.edl`) written from the
`.info.json` yt-dlp leaves behind even when the media download fails.
"""

import html
import json
import os
import re
from datetime import datetime

from ytdl.sponsorblock import to_ui_category, write_edl


class SidecarMixin:
    def _finalize_failure_sidecars(self, cmd, media_downloaded=False, subtitles_downloaded=False):
        """Run the failure-sidecar step exactly once, regardless of how the
        run failed.

        yt-dlp writes .info.json before downloading, so even a nonzero exit
        or a Python exception raised after that point has all the metadata
        needed (title, description, SponsorBlock segments). The wording names what
        already landed: "the media file is on disk" for a video/audio job,
        "the requested subtitle is on disk" for a subtitle-only job (whose
        media was never requested, let alone downloaded), and the requested
        operation for a job whose output did not land. Sidecar *attempting* still
        depends on media_type alone (an .edl/.nfo describes a video episode),
        so for a video job whose media landed but whose sidecars were not yet
        written (the exception happened before EDL/NFO creation) the sidecars
        are still attempted. Cancellation is excluded by the caller, and
        `sidecars_attempted` ensures this is never invoked twice for one run.
        """
        media_type = self.video_state.get("media_type", "video")
        if media_downloaded or subtitles_downloaded:
            # The message names what actually landed: "the media file" for a
            # video/audio job, "the requested subtitle" for a subtitle-only
            # job. Saying "the media file is on disk" for an SRT-only run is
            # inaccurate — the media was never requested, let alone downloaded.
            if subtitles_downloaded and not media_downloaded:
                landed = "the requested subtitle is on disk"
            else:
                landed = "the media file is on disk"
            self._emit("append_output",
                f"⚠️ Post-download processing failed; {landed}."
            )
            # Video sidecars may still be unwritten at this point (the
            # exception can land before EDL/NFO creation), so do not return
            # for video jobs — attempt them. Non-video jobs have no video
            # sidecars to write, so stop here.
            if media_type not in ("video", "video_only"):
                return
        elif media_type not in ("video", "video_only"):
            operation = self.describe_format_choice(cmd, media_type)
            self._emit("append_output",
                f"⚠️ {operation} failed; no video sidecars to generate."
            )
            return
        try:
            self._try_create_sidecar_files()
        except Exception as e:
            self._emit("append_output", f"🚩 Error creating sidecar files: {e}")
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Sidecar creation failed: {e}")

    def _try_create_sidecar_files(self):
        """Attempt to create .edl and .nfo files even when the media download failed.

        yt-dlp writes .info.json before downloading, so even if the video/audio
        download fails we have all the data needed (title, description, and
        SponsorBlock segments). The metadata sidecars are still useful: media
        center apps can display them, and the user can retry the download
        without re-fetching metadata.
        """
        base_filename = self.video_state.get("base_filename", "")
        if not base_filename:
            return

        # Construct the expected video path (we know the extension yt-dlp would
        # have used from the format selection).
        video_path = self.get_full_path(".mp4")
        if not video_path:
            return

        # Warn the user that the media file is missing
        self._emit("append_output",
            "⚠️ Media download failed, but metadata files are still available."
        )

        # Create NFO (uses video_state metadata only, no file dependency)
        self.create_nfo_file(video_path)

        # Create EDL (reads SponsorBlock segments from .info.json)
        # Only if the user has selected SB categories
        sb_categories = self.get_selected_sb_categories()
        if sb_categories:
            self.create_edl_file(video_path)
        else:
            self._emit("append_output",
                "  → skipped EDL (no SponsorBlock categories selected)"
            )

    def create_nfo_file(self, video_path):
        if not video_path:
            return

        base_filename = self.video_state.get("base_filename", "")
        nfo_path = os.path.splitext(video_path)[0] + ".nfo"

        title = re.sub(r"^S\d+E\d+\s*-\s*", "", base_filename).strip()
        if not title:
            title = self.video_state.get("original_title", "")
        showtitle = self.video_state.get("channel", "")
        description = self.video_state.get("description", "")
        upload_date = self.video_state.get("upload_date", "")  # YYYYMMDD
        url = self.video_state.get("clean_url", "")

        season = ""
        episode = ""
        aired = ""

        # Use special-case episode code if set (e.g. S01E2463 for JRE), otherwise derive from upload_date
        episode_code = self.video_state.get("episode_code", "")
        if episode_code:
            # Parse S##E#### format
            ep_code_match = re.match(r"S(\d+)E(\d+)", episode_code)
            if ep_code_match:
                season = str(int(ep_code_match.group(1)))  # e.g. "1"
                episode = str(int(ep_code_match.group(2)))  # e.g. "2463"
            # Still set aired from upload_date if available
            if upload_date and len(upload_date) == 8:
                try:
                    dt = datetime.strptime(upload_date, "%Y%m%d")
                    aired = dt.strftime("%Y-%m-%d")
                except ValueError:
                    aired = upload_date
        elif upload_date and len(upload_date) == 8:
            season = upload_date[2:4]
            episode = upload_date[4:8]
            try:
                dt = datetime.strptime(upload_date, "%Y%m%d")
                aired = dt.strftime("%Y-%m-%d")
            except ValueError:
                aired = upload_date  # Fallback

        # XML Content
        nfo_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<episodedetails>
    <title>{html.escape(title)}</title>
    <showtitle>{html.escape(showtitle)}</showtitle>
    <season>{season}</season>
    <episode>{episode}</episode>
    <plot>{html.escape(description)}</plot>
    <aired>{aired}</aired>
    <url>{html.escape(url)}</url>
</episodedetails>
"""
        try:
            with open(nfo_path, "w", encoding="utf-8") as f:
                f.write(nfo_content)
            self._emit("append_output", f"🪪 Created NFO file: {os.path.basename(nfo_path)}")
        except Exception as e:
            self._emit("append_output", f"🚩 Error creating NFO file: {e}")

    def create_edl_file(self, video_path):
        if not video_path:
            self._emit("append_output", "📟 create_edl_file called without video_path")
            return []

        json_file = self.get_full_path(".info.json")
        if not os.path.exists(json_file):
            self._emit("append_output", f"📟 .info.json not found at {json_file}")
            return []

        try:
            self._emit("append_output", f"📟 Reading {json_file} for EDL generation...")
            with open(json_file, "r", encoding="utf-8") as f:
                info = json.load(f)

            segments = []
            removed_categories = self.get_selected_sb_categories()
            self._emit("append_output", f"📟 Selected SB categories: {removed_categories}")

            if "sponsorblock_chapters" in info:
                self._emit("append_output", f"📟 Found {len(info['sponsorblock_chapters'])} SB chapters")
                for chapter in info["sponsorblock_chapters"]:
                    start = chapter.get("start_time", 0)
                    end = chapter.get("end_time", 0)
                    categories = chapter.get("_categories", [])

                    flat_categories = []
                    if categories:
                        for cat in categories:
                            if isinstance(cat, list) and len(cat) > 0:
                                flat_categories.append(str(cat[0]))
                            else:
                                flat_categories.append(str(cat))

                    # Check if this segment matches selected categories.
                    # _categories holds the API's raw names (yt-dlp copies
                    # them from SponsorBlock) while the checkboxes speak the
                    # UI's naming — translate before comparing, or an
                    # "Ending"-only selection matches nothing at all
                    # (raw "outro" vs UI "ending").
                    if any(
                        to_ui_category(cat) in removed_categories
                        for cat in flat_categories
                    ):
                        self._emit("append_output", f"📟 Matching segment: {start} - {end} ({flat_categories})")
                        # Store the first category (main category) with the segment
                        category = flat_categories[0] if flat_categories else "unknown"
                        segments.append({"start": start, "end": end, "category": category})
            else:
                self._emit("append_output", "📟 No sponsorblock_chapters in info JSON")

            if not segments:
                self._emit("append_output", "📟 No segments to skip found in selected categories")
                return []

            # EDL segments in API shape (segment = [start, end]) for the shared
            # build_edl_content() helper, so both writers produce identical output.
            edl_segments = [{"segment": [s["start"], s["end"]]} for s in segments]

            # Append ".edl" to the extensionless base path — never splitext()
            # over the title, which would eat dotted suffixes ("Lecture 1.5"
            # -> "Lecture 1.edl").
            base_path = self.get_full_path()
            if not base_path:
                self._emit("append_output", "🚩 Error: No video title available")
                return []
            edl_path = write_edl(base_path + ".edl", edl_segments)

            self._emit("append_output", f"🎬 Created EDL file: {os.path.basename(edl_path)}")
            return segments

        except Exception as e:
            self._emit("append_output", f"🚩 Error creating EDL file: {e}")
            return []