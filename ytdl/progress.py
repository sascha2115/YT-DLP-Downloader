"""yt-dlp output parsing tables and the download progress manager."""

import re


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
        if self.media_type == "audio":
            self.audio_progress = self.PROGRESS_MAX
        elif self.media_type == "video_only":
            self.video_progress = self.PROGRESS_MAX
        else:
            # video or muxed: always fill video bar; only fill audio bar if
            # a separate audio stream was actually downloaded (download_count > 1)
            self.video_progress = self.PROGRESS_MAX
            if self.download_count > 1:
                self.audio_progress = self.PROGRESS_MAX
        return self.video_progress, self.audio_progress

    def get_combined_fraction(self) -> float:
        """Overall progress for dock icon overlay (0.0 – 1.0)."""
        v = self.video_progress / self.PROGRESS_MAX
        a = self.audio_progress / self.PROGRESS_MAX
        if self.media_type == "audio":
            return a
        if self.media_type == "video_only":
            return v
        if self.download_count <= 1 and self.audio_progress == 0:
            return v
        return (
            v * self.VIDEO_BYTE_WEIGHT + a * self.AUDIO_BYTE_WEIGHT
        )
