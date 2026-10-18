"""yt-dlp output parsing tables, the download progress manager, and
progress-parsing mixin methods extracted from ytdl/download.py.
"""

import logging
import re

logger = logging.getLogger(__name__)


RE_MERGE = re.compile(r'\[Merger\] Merging formats into "?(.*?)"?$')
RE_AUDIO = re.compile(r'\[ExtractAudio\] Destination: "?(.*?)"?$')
RE_DEST = re.compile(r'\[download\] Destination: "?(.*?)"?$')
RE_ALREADY = re.compile(r'\[download\] "?(.*?)"? has already been downloaded')
RE_CONVERT = re.compile(r'\[VideoConvertor\] (?:Converting|Recoding) video from .* to "?(.*?)"?$')
RE_SLEEP = re.compile(
    r"\[download\] Sleeping \d+\.\d+ seconds as required by the site\.\.\."
)

# Subtitle file extensions yt-dlp can output. The app requests srt
# (--sub-format srt --convert-subs srt), but yt-dlp may download an original
# subtitle format first (vtt, srv3, json3, ...) and convert it afterwards.
# Used to tell subtitle transfers apart from video/audio downloads, because
# yt-dlp downloads subtitles BEFORE the media streams.
SUBTITLE_EXTENSIONS = frozenset(
    {
        ".srt", ".vtt", ".ass", ".ssa", ".ttml", ".sbv", ".lrc", ".sami",
        ".scc", ".json3", ".srv1", ".srv2", ".srv3", ".srv4", ".mpl",
    }
)

# ====================================================================================================
# Download Progress Manager Class
# ====================================================================================================
class DownloadProgressManager:
    PROGRESS_MAX = 1000
    PROGRESS_SCALE = 10

    # Typical 1080p VP9/WebM + best Opus: video is ~90–95% of bytes (audio ~128–165 kbps
    # vs video ~2–5 Mbps). Bar widths use 80/20 so the audio strip stays visible; dock
    # overlay uses 85/15 for a closer byte-weighted overall estimate.
    VIDEO_BAR_STRETCH = 4
    AUDIO_BAR_STRETCH = 1
    VIDEO_BYTE_WEIGHT = 0.85
    AUDIO_BYTE_WEIGHT = 0.15

    STREAM_VIDEO = "video"
    STREAM_AUDIO = "audio"

    def __init__(self, media_type: str = "video"):
        self.media_type = media_type
        self.video_progress = 0
        self.audio_progress = 0
        self.active_stream = self.STREAM_VIDEO
        self.download_count = 0
        self.started = False
        # Set by mark_complete(), i.e. a run that ended with exit code 0. The
        # dock overlay needs it to report the terminal 1.0 when the download
        # turned out to be a single muxed stream: the video phase is scaled
        # down to VIDEO_BYTE_WEIGHT to leave room for a possible audio
        # transfer, and that reservation is only released once the run is over.
        self.completed = False
        # Last "[download] Destination:" path seen. yt-dlp RE-PRINTS the
        # destination of the file it is RETRYING after a network error, so
        # a repeated destination must not advance the stream accounting.
        self.last_destination: str | None = None

    @staticmethod
    def _normalize_destination(dest: str | None) -> str | None:
        """Collapse differences that do not identify a different FILE."""
        if not dest:
            return None
        dest = dest.strip().strip('"').strip("'").strip()
        # Strip transient download suffixes (".part", ".ytdl", ".temp")
        while dest.endswith((".part", ".ytdl", ".temp")):
            dest = dest.rsplit(".", 1)[0]
        return dest

    def mark_started(self):
        self.started = True

    def is_started(self):
        return self.started

    def on_download_destination(self, dest: str | None = None):
        """Called when yt-dlp starts downloading a new file (video then audio).

        After a mid-download error ("Got error ... Retrying (n/10)...") yt-dlp
        re-prints the destination of the SAME file. That re-print must not
        bump the download count or switch the active stream (video → audio);
        only a genuinely different destination file may do that.
        """
        normalized = self._normalize_destination(dest)
        if normalized and normalized == self.last_destination:
            # Same file again: a retry, not the next stream
            return
        if normalized:
            self.last_destination = normalized
        self.download_count += 1
        if self.media_type == "audio":
            self.active_stream = self.STREAM_AUDIO
        elif self.media_type == "video_only":
            self.active_stream = self.STREAM_VIDEO
        elif self.download_count == 1:
            self.active_stream = self.STREAM_VIDEO
        else:
            self.active_stream = self.STREAM_AUDIO

    def update_from_ytdlp_percent(self, yt_dlp_percent: float) -> tuple[int, int]:
        value = min(
            self.PROGRESS_MAX,
            int(yt_dlp_percent * self.PROGRESS_SCALE),
        )
        if self.active_stream == self.STREAM_AUDIO:
            self.audio_progress = value
        else:
            self.video_progress = value
        return self.video_progress, self.audio_progress

    def mark_complete(self) -> tuple[int, int]:
        self.completed = True
        if self.media_type == "audio":
            self.audio_progress = self.PROGRESS_MAX
        elif self.media_type == "video_only":
            self.video_progress = self.PROGRESS_MAX
        else:
            # video or muxed: always fill video bar
            self.video_progress = self.PROGRESS_MAX
            # The audio bar is filled here too. For a single muxed stream
            # (download_count == 1) the file contains both tracks and no audio
            # transfer followed the video; for separate streams
            # (download_count > 1) the audio bar already reached 100% on its
            # own transfer. Either way it ends at full, so the count is not
            # needed to decide — the old "download_count > 1 or == 1" form
            # was True for every value that can reach this branch.
            self.audio_progress = self.PROGRESS_MAX
        return self.video_progress, self.audio_progress

    def get_combined_fraction(self) -> float:
        """Overall progress for dock icon overlay (0.0 - 1.0).

        Monotonic by construction: the video transfer only fills its share of
        the bar (VIDEO_BYTE_WEIGHT) and the audio transfer the rest, so the
        overlay keeps rising when the second stream starts. Returning the plain
        video fraction during the video phase instead painted the bar to 100%
        and then dropped it back to VIDEO_BYTE_WEIGHT the moment the audio
        destination appeared - the overlay visibly jumped backwards.
        """
        v = self.video_progress / self.PROGRESS_MAX
        a = self.audio_progress / self.PROGRESS_MAX
        if self.media_type == "audio":
            return a
        if self.media_type == "video_only":
            return v
        if self.completed and self.audio_progress == 0:
            # Single muxed stream: no audio transfer followed the video, so it
            # owned the whole bar after all (and the run is over, so nothing
            # can still arrive in the reserved share).
            return 1.0
        return (
            v * self.VIDEO_BYTE_WEIGHT + a * self.AUDIO_BYTE_WEIGHT
        )


# ============================================================================
# Progress-parsing mixin (extracted from ytdl/download.py)
# ============================================================================
class ProgressMixin:
    """Parses yt-dlp stdout into progress-bar updates and state changes.

    Methods access shared GUI state via ``self`` (``video_state``, ``_emit``,
    etc.) provided by ``DownloadMixin`` on the combined class.
    """

    def _parse_download_output(self, line, state):
        # A subtitle transfer only ever produces consecutive "[download]"
        # lines; any other non-empty line ends it, so later media lines are
        # accounted normally. (yt-dlp's "\r"-separated progress chunks yield
        # EMPTY lines mid-transfer, which must NOT end it.)
        if line and not line.startswith("[download]"):
            state["downloading_subtitles"] = False

        progress_manager = state.get("progress_manager")
        if progress_manager and progress_manager.is_started():
            if (
                line.startswith(("[Merger]", "[ExtractAudio]", "[Fixup]"))
                or "Merging formats" in line
                or "Deleting original file" in line
            ):
                self._emit("set_indeterminate", True)

        new_filename = None
        if m := RE_MERGE.search(line):
            new_filename = m.group(1).strip()
        elif m := RE_AUDIO.search(line):
            if self._effective_media_type() == "audio":
                new_filename = m.group(1).strip()
        elif m := RE_CONVERT.search(line):
            new_filename = m.group(1).strip()
        elif m := RE_ALREADY.search(line):
            if not self._is_subtitle_path(m.group(1)):
                new_filename = m.group(1).strip()
        elif state["merged_filename"] is None:
            if m := RE_DEST.search(line):
                dest = m.group(1).strip()
                if not self._is_subtitle_path(dest):
                    new_filename = dest

        if new_filename:
            new_filename = new_filename.strip('"')
            state["merged_filename"] = new_filename
            logger.debug(f"Captured final filename: {new_filename}")

        if line.startswith(("[youtube]", "[info]", "[debug]", "[Metadata]")):
            self._emit("append_output", line)
            state["last_line_was_progress"] = False
        elif RE_SLEEP.search(line):
            self._emit("append_output", line)
            state["last_line_was_progress"] = False
        elif line.startswith("[download]"):
            state = self._update_download_progress(line, state)
            state["last_line_was_progress"] = True
        elif RE_MERGE.search(line):
            self._emit("append_output", line)
            state["last_line_was_progress"] = False
        else:
            self._emit("append_output", line)
            state["last_line_was_progress"] = False

        return state

    def _update_download_progress(self, line, state):
        progress_manager = state["progress_manager"]
        in_subtitle_transfer = state.get("downloading_subtitles", False)

        if "Destination:" in line:
            m = RE_DEST.search(line)
            in_subtitle_transfer = bool(m and self._is_subtitle_path(m.group(1)))
            state["downloading_subtitles"] = in_subtitle_transfer
            if not in_subtitle_transfer:
                progress_manager.on_download_destination(
                    m.group(1) if m else None
                )
        elif m := RE_ALREADY.search(line):
            in_subtitle_transfer = self._is_subtitle_path(m.group(1))

        if not in_subtitle_transfer:
            if not progress_manager.is_started():
                progress_manager.mark_started()
                self._emit("set_indeterminate", False)

            percent_match = re.search(r"(\d{1,3}(?:\.\d+)?)%", line)
            if percent_match:
                try:
                    percent = float(percent_match.group(1))
                    video_val, audio_val = progress_manager.update_from_ytdlp_percent(
                        percent
                    )
                    self._emit("update_download_progress", video_val, audio_val)
                    self._emit(
                        "update_dock_progress",
                        progress_manager.get_combined_fraction(),
                    )
                except ValueError:
                    pass

        if state["last_line_was_progress"]:
            self._emit("update_last_line", line)
        else:
            self._emit("append_output", line)

        return state

    def _is_subtitle_path(self, path) -> bool:
        """Return True if the given yt-dlp destination path is a subtitle file."""
        name = (path or "").strip().strip('"').lower()
        while name.endswith((".part", ".ytdl", ".temp")):
            name = name.rsplit(".", 1)[0]
        return name.endswith(tuple(SUBTITLE_EXTENSIONS))
