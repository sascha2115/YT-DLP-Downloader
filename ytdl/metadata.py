"""File metadata analysis (ffprobe) — extracted from ytdl/download.py.

Mixin for YTDLPDownloaderGUI; methods access shared state via ``self``.
Probes the finished media file with ffprobe and reports codec, resolution,
bitrate, duration, and subtitle streams to the output panel.
"""

import gc
import json
import logging
import os
import subprocess

from ytdl.utils import format_duration, format_filesize

logger = logging.getLogger(__name__)


class MetadataMixin:
    def _analyze_downloaded_file(self, filename):
        # Use cached metadata
        metadata = self.cached_video_metadata
        if not metadata:
            self._emit("append_output", "🚩 No metadata available for analysis")
            return

        # Display Metadata Summary
        self._emit("append_output", "\nFile info by ffprobe:")
        container = filename.split('.')[-1].upper()
        size_info = f"Container: {container}"
        if metadata.get("file_size_formatted"):
            size_info += f"  (Size: {metadata['file_size_formatted']})"
        self._emit("append_output", size_info)

        if metadata["video_codec"]:
            video_info = (
                f"Video: {metadata['video_codec']} ({metadata['video_long_codec']})"
            )
            if metadata["resolution"]:
                video_info += f", {metadata['resolution']}"

            # Show strictly video-specific bitrate as requested
            v_bitrate = metadata["video_bitrate"]
            video_info += f", {v_bitrate} kbps" if v_bitrate else ", none"

            self._emit("append_output", video_info)

        if metadata["audio_codec"]:
            audio_info = (
                f"Audio: {metadata['audio_codec']} ({metadata['audio_long_codec']})"
            )
            self._emit("append_output", audio_info)

        if metadata["subtitle_streams"]:
            sub_list = [
                f"{s['lang']} ({s['codec']})" for s in metadata["subtitle_streams"]
            ]
            self._emit("append_output", f"Subtitles: {', '.join(sub_list)}")

        if metadata.get("ffmpeg_bitrate_line"):
            self._emit("append_output", metadata["ffmpeg_bitrate_line"])
        else:
            self._emit("append_output", f"Duration: {metadata['duration_formatted']}")

    def get_file_metadata(self, file_path):
        metadata = {
            "duration_seconds": None,
            "duration_formatted": "N/A",
            "video_codec": None,
            "video_long_codec": None,
            "resolution": None,
            "video_bitrate": None,
            "audio_codec": None,
            "audio_long_codec": None,
            "subtitle_streams": [],
            "ffmpeg_bitrate_line": None,
            "file_size": 0,
            "file_size_formatted": "N/A",
        }
        if not self.ffprobe_bin:
            self._emit("append_output",
                "🚩 Cannot analyze file: ffprobe is not installed or accessible"
            )
            return metadata

        try:
            cmd = [
                self.ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,codec_long_name,codec_type,width,height,bit_rate,tags:format=duration",
                "-of",
                "json",
                file_path,
            ]
            logger.debug(f"Running ffprobe on: {file_path}")
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            try:
                stdout, _ = proc.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, cmd)
            data = json.loads(stdout)

            # Get file size
            if os.path.exists(file_path):
                f_size = os.path.getsize(file_path)
                metadata["file_size"] = f_size
                metadata["file_size_formatted"] = format_filesize(f_size)

            # format duration
            fmt = data.get("format", {})
            duration_seconds = fmt.get("duration")
            if duration_seconds:
                metadata["duration_seconds"] = float(duration_seconds)
                metadata["duration_formatted"] = format_duration(duration_seconds)

            # Process Stream Metadata
            for stream in data.get("streams", []):
                codec_name = stream.get("codec_name")
                codec_long_name = stream.get("codec_long_name")
                codec_type = stream.get("codec_type")
                tags = stream.get("tags", {})

                if codec_type == "video" and not metadata["video_codec"]:
                    metadata["video_codec"] = codec_name
                    metadata["video_long_codec"] = codec_long_name

                    # Extract resolution as width x height
                    width = stream.get("width")
                    height = stream.get("height")
                    if width and height:
                        metadata["resolution"] = f"{width}x{height}"

                    # Extract video bitrate (in bps -> convert to kbps)
                    bit_rate = stream.get("bit_rate")

                    # Fallback for WebM/MKV: Check tags for 'BPS'
                    if not bit_rate:
                        v_tags = stream.get("tags", {})
                        # Check "BPS" or "BPS-eng" etc.
                        for tag_key, tag_val in v_tags.items():
                            if tag_key.upper().startswith("BPS"):
                                bit_rate = tag_val
                                break

                    if bit_rate:
                        try:
                            metadata["video_bitrate"] = round(int(bit_rate) / 1000)
                        except (ValueError, TypeError):
                            pass

                elif codec_type == "audio" and not metadata["audio_codec"]:
                    metadata["audio_codec"] = codec_name
                    metadata["audio_long_codec"] = codec_long_name

                elif codec_type == "subtitle":
                    # Track subtitle streams
                    language = tags.get("language", "N/A")
                    title = tags.get("title", "N/A")
                    metadata["subtitle_streams"].append(
                        {"codec": codec_name, "lang": language, "title": title}
                    )

        except subprocess.CalledProcessError as e:
            self._emit("append_output", f"🚩 ffprobe failed for: {os.path.basename(file_path)}")
            logger.error(f"ffprobe error for {file_path}: {e}")
        except subprocess.TimeoutExpired:
            self._emit("append_output", "👉 ffprobe timed out")
        except json.JSONDecodeError:
            self._emit("append_output", "🚩 ffprobe returned unreadable output")
        except Exception as e:
            self._emit("append_output",
                f"🚩 Unexpected error during file analysis: {e}"
            )
        finally:
            # Try to get the actual bitrate line from ffmpeg -i as a source of truth
            if self.ffmpeg_bin:
                try:
                    ffmpeg_cmd = [self.ffmpeg_bin, "-i", file_path]
                    f_proc = subprocess.Popen(
                        ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                    )
                    try:
                        _, f_stderr = f_proc.communicate(timeout=10)
                    except subprocess.TimeoutExpired:
                        f_proc.kill()
                        f_proc.communicate()  # drain pipes after kill
                        f_stderr = ""
                    for f_line in f_stderr.splitlines():
                        if "bitrate:" in f_line:
                            metadata["ffmpeg_bitrate_line"] = f_line.strip()
                            break
                except Exception:
                    pass
            # release file handles
            gc.collect()

        return metadata
