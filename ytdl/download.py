"""Download orchestration: command building, yt-dlp run, progress parsing, post-processing.

Mixin for YTDLPDownloaderGUI (assembled in ytdl/app.py);
methods access shared state via self."""

import gc
import html
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

from ytdl.config import DEFAULT_OUTPUT_DIR
from ytdl.progress import DownloadProgressManager, RE_ALREADY, RE_AUDIO, RE_CONVERT, RE_DEST, RE_MERGE, RE_SLEEP, SUBTITLE_EXTENSIONS
from ytdl.sites import DEFAULT_SITE, SUPPORTED_SITES, site_wants_js_runtime
from ytdl.utils import format_duration, format_filesize, sanitize_title


class DownloadMixin:
    def start_download(self):
        # Reset label when a new download is initiated
        self.download_button.setText("Download")
        self.set_download_button_status("")
        # Get base filename/folder from title
        base_name = self.title_entry.text().strip()
        if not base_name:
            self.signals.append_output.emit("🚩 Error: No video title available")
            return
            
        # Base directory from settings
        root_output_dir = self.output_dir_entry.text().strip() or DEFAULT_OUTPUT_DIR
        
        # Create folder structure: Root / Channel / SxxEyyyy - Video Title
        # Files inside: "SxxEyyyy - Video Title"
        channel = self.video_state.get("channel", "")
        sanitized_base_name = sanitize_title(base_name)
        
        if channel:
            sanitized_channel = sanitize_title(channel)
            # Create nested folder structure: {root}/{channel}/{base_name}
            video_dir = os.path.join(root_output_dir, sanitized_channel, sanitized_base_name)
        else:
            video_dir = os.path.join(root_output_dir, sanitized_base_name)

        if not os.path.exists(video_dir):
            try:
                os.makedirs(video_dir, exist_ok=True)
                rel_path = os.path.relpath(video_dir, root_output_dir)
                self.signals.append_output.emit(f"📂 Created directory: {rel_path}")
            except Exception as e:
                self.signals.append_output.emit(f"🚩 Error creating directory: {e}")
                return

        # Update video state — filename is just base_name (without channel prefix)
        self.update_video_state(title=base_name, output_dir=video_dir)
        self.video_state["is_download_running"] = True

        # Detailed Existence Check
        media_type = self.video_state.get("media_type")
        selected_langs = self.get_selected_subtitle_codes()
        base_path = self.get_full_path()
        
        # 1. Check Media (Video/Audio)
        media_exists = False
        media_file = ""
        if media_type in ["video", "video_only"]:
            for ext in [".mp4", ".mkv", ".webm"]:
                if os.path.exists(base_path + ext):
                    media_exists = True
                    media_file = os.path.basename(base_path + ext)
                    break
        elif media_type == "audio":
            for ext in [".mp3", ".m4a", ".wav", ".opus"]:
                if os.path.exists(base_path + ext):
                    media_exists = True
                    media_file = os.path.basename(base_path + ext)
                    break
        
        # 2. Check Subtitles
        missing_subs = []
        existing_subs = []
        for code in selected_langs:
            # Check both manual (.en.srt) and auto-generated (.a.en.srt) paths
            p1 = self.get_full_path(f".{code}.srt")
            p2 = self.get_full_path(f".a.{code}.srt")
            if os.path.exists(p1) or os.path.exists(p2):
                existing_subs.append(code)
            else:
                missing_subs.append(code)

        # 3. Output Detailed Status
        if media_file:
            self.signals.append_output.emit(f"✓ Media exists: {media_file}")
        
        for code in existing_subs:
            p1 = self.get_full_path(f".{code}.srt")
            p2 = self.get_full_path(f".a.{code}.srt")
            if os.path.exists(p1):
                self.signals.append_output.emit(f"✓ Subtitle exists: {os.path.basename(p1)}")
            elif os.path.exists(p2):
                self.signals.append_output.emit(f"✓ Subtitle exists: {os.path.basename(p2)}")

        # 4. Decide if we skip
        should_skip = False
        skip_reason = ""
        
        if media_type == "subtitles":
            if not selected_langs:
                self.signals.append_output.emit("👉 No subtitle languages selected. Please check at least one language.")
                self.video_state["is_download_running"] = False
                return
            
            if not missing_subs:
                should_skip = True
                skip_reason = "All requested subtitles already exist"
        else:
            # Video or Audio requested
            # Skip only if the media file exists AND all requested subtitles are already there
            if media_exists and not missing_subs:
                should_skip = True
                skip_reason = "All requested files (media and subtitles) already exist"

        if should_skip:
            self.signals.append_output.emit(f"👉 {skip_reason} - Download skipped")
            self.video_state["is_download_running"] = False
            return

        cmd = self.build_command(selected_langs)
        if not cmd:
            self.signals.append_output.emit("👉 Please enter a valid video URL")
            self.video_state["is_download_running"] = False
            self.url_entry.setFocus()
            return

        # Log the download start
        channel = self.video_state.get("channel", "")
        url = self.video_state.get("clean_url", "")
        logger.info(f"{channel}")
        logger.info(f"{base_name}")
        logger.info(f"{url}")

        # Clear cached metadata at start of new download
        self.cached_video_metadata = None

        self.signals.update_dock_tile.emit("")
        self.clearDockProgress()
        self._set_ui_enabled_state(False)
        self.download_button.setEnabled(False)
        self._reset_download_progress_bars()
        self._set_download_busy(True)

        self.signals.append_output.emit(
            f"\n🖥️ {' '.join(str(item) for item in cmd if item)}"
        )

        thread = threading.Thread(target=self.run_download, args=(cmd, selected_langs))
        thread.daemon = True
        thread.start()

    def build_command(self, selected_langs=None):
        url = self.get_clean_url()
        if not url:
            return None

        # Get the user-edited title for filename
        custom_title = self.title_entry.text().strip()
        if not custom_title:
            custom_title = "downloaded_video"

        # Update state
        self.update_video_state(title=custom_title)

        cmd = [self.yt_dlp_bin]

        # Dependency Paths
        if self.ffmpeg_bin and shutil.which(self.ffmpeg_bin):
            ffmpeg_dir = os.path.dirname(self.ffmpeg_bin)
            cmd.extend(["--ffmpeg-location", ffmpeg_dir])

        if (
            self.deno_bin
            and shutil.which(self.deno_bin)
            and site_wants_js_runtime(self.video_state.get("site", DEFAULT_SITE))
        ):
            cmd.extend(["--js-runtimes", f"deno:{self.deno_bin}"])

        # Media Type and Format Logic
        media_type = self.video_state["media_type"]
        quality = self.video_state["quality"]
        video_fmt = self.video_state["video_format"]
        audio_fmt = self.video_state["audio_format"]
        video_codec = self.video_state["video_codec"]

        if media_type == "audio":
            # Sites without audio-only streams (Odysee: every format is muxed,
            # video claims only offer the source MP4) would otherwise save a
            # VIDEO file for an audio request - "bestaudio/best" falls back to
            # a muxed format. Such sites set "always_extract_audio", which
            # forces -x so the audio is extracted from whatever is downloaded.
            site_profile = SUPPORTED_SITES.get(
                self.video_state.get("site", DEFAULT_SITE), {}
            )
            force_extract = site_profile.get("always_extract_audio", False)

            if audio_fmt == "best" and not force_extract:
                cmd.extend(["-f", "bestaudio/best"])
            else:
                # NOTE: this used to map the GUI's "M4A" option to yt-dlp's
                # "aac" postprocessor format. Per yt-dlp's FFmpegExtractAudioPP,
                # "aac" forces a raw ADTS stream (-f adts) while the output file
                # is still named ".m4a" - a mislabeled file. "m4a" instead writes
                # a real MP4/M4A container and stream-copies AAC sources
                # losslessly (YouTube AAC, Rumble's ADTS "audio-192p") via the
                # aac_adtstoasc bitstream filter; only non-AAC sources
                # (Opus/Vorbis) get re-encoded.
                cmd.extend(
                    [
                        "-x",
                        "--audio-format",
                        audio_fmt,
                        "--audio-quality",
                        "0",
                    ]
                )
        elif media_type in ["video", "video_only"]:
            # Build common video filters
            format_parts = [
                f"[height<={int(quality)}]" if quality != "best" else "",
                f"[vcodec*={video_codec}]" if video_codec != "best" else "",
            ]
            video_format_filter = "".join(p for p in format_parts if p)

            if media_type == "video":
                # "bestvideo*" rather than strict "bestvideo": muxed HLS
                # formats (Rumble) report unknown codecs, which strict
                # bestvideo skips - leaving only Rumble's tiny video-only
                # timeline strip (180p) as the "best" video stream. bv*
                # allows video+audio combinations, so the site's real
                # streams are eligible (the merge still uses the separate
                # bestaudio track when one exists).
                format_string = f"bestvideo*{video_format_filter}+bestaudio/best"
                cmd.extend(["-f", format_string])
                if video_fmt != "best":
                    cmd.extend(["--merge-output-format", video_fmt])

            elif media_type == "video_only":
                format_string = f"bestvideo{video_format_filter}/best"
                cmd.extend(["-f", format_string])
                if video_fmt != "best":
                    cmd.extend(["--recode-video", video_fmt])

        elif media_type == "subtitles":
            cmd.extend(["--skip-download"])

        # SponsorBlock Integration
        sb_categories = self.get_selected_sb_categories()

        if sb_categories and SUPPORTED_SITES.get(
            self.video_state.get("site", DEFAULT_SITE), {}
        ).get("sponsorblock", False):
            # SponsorBlock chapters only exist on YouTube
            cmd.extend(["--sponsorblock-mark", "all"])

        # Strip embedded broadcast captions (EIA-608 carried in H.264 SEI
        # "User Data Registered ITU-T T.35" NALs) from video downloads.
        # Rumble passes US broadcast feeds through, so the MP4 carries a
        # hidden CC track that some players (IINA) surface as a second
        # subtitle track; the app's own SRT files are the intended
        # subtitles. Removing SEI NAL type 6 is lossless (-c copy semantics
        # via the ffmpeg bitstream filter) for H.264 - but the numbering is
        # codec-specific: in AV1 an OBU of type 6 is a Frame OBU, so
        # applying this unconditionally deleted actual picture data and
        # corrupted AV1 downloads. Hence the per-site "strip_embedded_cc"
        # profile flag (True only for Rumble). Only relevant for formats
        # that contain video; audio-only downloads are unaffected anyway.
        if SUPPORTED_SITES.get(
            self.video_state.get("site", DEFAULT_SITE), {}
        ).get("strip_embedded_cc", False):
            cc_strip_args = "-bsf:v filter_units=remove_types=6"
            cmd.extend(["--postprocessor-args", f"Merger:{cc_strip_args}"])
            cmd.extend(["--postprocessor-args", f"FixupM3u8:{cc_strip_args}"])

        # Final parameters - use helper method
        cmd.extend(["--add-metadata"])
        cmd.extend(["-o", self.get_filename_template()])
        cmd.extend(["--no-playlist", "-N", "8"])
        cmd.extend(["--write-info-json"])
        # Subtitles Integration
        if selected_langs:
            lang_arg = self._subtitle_lang_patterns(selected_langs)
            cmd.extend(
                [
                    "--write-subs",
                    "--write-auto-subs",
                    "--sub-langs",
                    lang_arg,
                    "--sub-format",
                    "srt",
                    "--convert-subs",
                    "srt",
                ]
            )

        # cmd.extend(["--extractor-args", "youtube:player_client=default,ios"])
        cmd.append(url)
        return cmd

    def run_download(self, cmd, selected_langs):
        success = False
        had_download_progress = False
        removed_sb_segments = []
        try:
            if self.simulate_download_error:
                raise RuntimeError("Simulated download error (testing flag enabled)")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
            )

            progress_manager = DownloadProgressManager(
                media_type=self.video_state.get("media_type", "video")
            )
            state = {
                "last_line_was_progress": False,
                "merged_filename": None,
                "downloading_subtitles": False,
                "progress_manager": progress_manager,
            }

            if process.stdout is None:
                return

            self.signals.set_indeterminate.emit(True)

            # Main Output Loop
            for line in process.stdout:
                # yt-dlp separates progress updates with carriage returns;
                # universal newlines already splits them, but strip any
                # leading "\r" so the line reliably starts with "[download]".
                line = line.lstrip("\r").rstrip()
                state = self._parse_download_output(line, state)

            process.wait()
            exit_code = process.returncode
            had_download_progress = progress_manager.is_started()
            if had_download_progress:
                video_done, audio_done = progress_manager.mark_complete()
                self.signals.update_download_progress.emit(video_done, audio_done)

            if exit_code == 0:
                # Post-processing phase: keep the busy spinner visible while we
                # finalize files (subtitles, EDL/NFO, cleanup, analysis, etc.).
                self.signals.set_indeterminate.emit(True)

                media_type = self.video_state.get("media_type")
                if media_type == "subtitles":
                    self.signals.append_output.emit("Subtitle download complete.")
                else:
                    self.signals.append_output.emit("Video/Audio downloaded.")

                # Update video state with final filename
                if state["merged_filename"]:
                    self.video_state["full_path"] = state["merged_filename"]

                # Cache metadata immediately after download
                if state["merged_filename"]:
                    self.cached_video_metadata = self.get_file_metadata(
                        state["merged_filename"]
                    )

                # Process subtitles (now downloaded together with video)
                if selected_langs:
                    self.signals.append_output.emit("\nProcessing subtitles...")
                    downloaded_subs = self._normalize_subtitle_names(
                        self._find_downloaded_subtitles(selected_langs)
                    )

                    if downloaded_subs:
                        report_langs = [f"{item[0]} ({item[2]})" for item in downloaded_subs]
                        self.video_state["downloaded_subtitles"] = [item[0] for item in downloaded_subs]
                        
                        self.signals.append_output.emit(
                            f"💬 Subtitles identified: {', '.join(report_langs)}"
                        )

                        # Process subtitles: merge auto-generated ones for 2-line
                        # display where the site needs it (YouTube), but leave
                        # subs untouched that are already well-formatted (real
                        # subs anywhere, auto subs on e.g. Rumble).
                        for lang, srt_path, sub_type in downloaded_subs:
                            if not self._subtitle_needs_resync(sub_type):
                                self.signals.append_output.emit(f"  → {lang} ({sub_type}): keeping original format")
                            else:
                                self.signals.append_output.emit(f"  → {lang} (auto): merging into 2-line format")
                                self._resync_subtitle_for_language(lang, srt_path, [])
                    else:
                        self.signals.append_output.emit("👉 No subtitles were downloaded.")

                # Create EDL and NFO files BEFORE cleanup deletes the .info.json
                # Skip if we only downloaded subtitles
                if state["merged_filename"] and self.video_state.get("media_type") != "subtitles":
                    removed_sb_segments = self.create_edl_file(state["merged_filename"])
                    self.create_nfo_file(state["merged_filename"])

                # Cleanup (deletes .info.json and original subtitles)
                self.cleanup_files(selected_langs)
                # Fallback: if filename wasn't captured by regex, try to find it
                if not state["merged_filename"]:
                    base_path = self.get_full_path()
                    # Check for common extensions
                    for ext in [".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".opus"]:
                        if os.path.exists(base_path + ext):
                            state["merged_filename"] = base_path + ext
                            logger.debug(f"Fallback found file: {state['merged_filename']}")
                            break

                # Post Download Analysis (uses cached metadata)
                # Skip if we only downloaded subtitles (as no new media was created)
                if state["merged_filename"] and self.video_state.get("media_type") != "subtitles":
                    self.signals.append_output.emit(f"🔍 Analyzing: {os.path.basename(state['merged_filename'])}")
                    self._analyze_downloaded_file(state["merged_filename"])
                elif not state["merged_filename"] and self.video_state.get("media_type") != "subtitles":
                    self.signals.append_output.emit("🚩 Could not find filename for analysis")
                # all done
                self.signals.append_output.emit("\n✅ Download finished.")
                success = True
            else:
                self.signals.append_output.emit(
                    f"🚩 yt-dlp process failed with exit code {exit_code}"
                )

        except Exception as e:
            self.signals.append_output.emit(f"🚩 An unexpected error occurred: {e}")
            logger.error(f"Download failed with exception: {e}")
        finally:
            # Log SponsorBlock segments separately if available
            if success and removed_sb_segments:
                segment_info = []
                for seg in removed_sb_segments:
                    category = seg.get('category', 'unknown')
                    start = seg.get('start', 0)
                    end = seg.get('end', 0)
                    segment_info.append(f"{category} ({start:.1f}s-{end:.1f}s)")
                logger.info(f"SponsorBlock removed: {', '.join(segment_info)}")
            
            # Log the result
            logger.info(f"Download {'succeeded' if success else 'failed'}")

            # Clean up state and UI in a single batch to avoid cascading updates
            self.video_state["is_download_running"] = False
            # Subtitle transfers never touch the bars, so for subtitles-only
            # runs the bars are filled once here at the end (on success).
            fill_progress_bars = had_download_progress or (
                success and self.video_state.get("media_type") == "subtitles"
            )
            if fill_progress_bars:
                self.signals.update_download_progress.emit(
                    DownloadProgressManager.PROGRESS_MAX,
                    DownloadProgressManager.PROGRESS_MAX,
                )
            self.signals.set_indeterminate.emit(False)
            self.signals.set_download_button_label.emit(
                "Download Successful ✅" if success else "Download Error 🚨"
            )
            self.signals.set_download_button_status.emit(
                "success" if success else "error"
            )
            self.signals.enable_button.emit()
            self.signals.update_dock_tile.emit("✓" if success else "!")
            self.clearDockProgress()

    def cleanup_files(self, selected_langs):
        # Clean up JSON
        json_file = self.get_full_path(".info.json")
        if os.path.exists(json_file):
            try:
                os.remove(json_file)
            except OSError:
                logger.warning(f"Could not remove info JSON file: {json_file}")
                pass

        # Clean up original/auto-generated files
        for code in selected_langs:
            # We always output to Title.[lang].srt.
            # So we only need to clean up Title.[lang].a.srt if it exists,
            # as it was replaced by the processed Title.[lang].srt.
            auto_srt = self.get_full_path(f".a.{code}.srt")
            if os.path.exists(auto_srt):
                try:
                    os.remove(auto_srt)
                except OSError:
                    pass

            # Also clean up any lingering .resynced or .merged files from previous versions
            for suffix in [".resynced", ".merged"]:
                old_path = self.get_full_path(f".{code}{suffix}.srt")
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass

    def _parse_download_output(self, line, state):
        # A subtitle transfer only ever produces consecutive "[download]"
        # lines; any other non-empty line ends it, so later media lines are
        # accounted normally. (yt-dlp's "\r"-separated progress chunks yield
        # EMPTY lines mid-transfer, which must NOT end it.)
        if line and not line.startswith("[download]"):
            state["downloading_subtitles"] = False

        # If yt-dlp is in a post-processing phase (after downloads), show the spinner.
        # Important: avoid enabling this during the normal video→audio transition,
        # otherwise the spinner could remain visible during active download.
        progress_manager = state.get("progress_manager")
        if progress_manager and progress_manager.is_started():
            if (
                line.startswith(("[Merger]", "[ExtractAudio]", "[Fixup]"))
                or "Merging formats" in line
                or "Deleting original file" in line
            ):
                self.signals.set_indeterminate.emit(True)

        # Capture the final filename
        new_filename = None
        if m := RE_MERGE.search(line):
            new_filename = m.group(1).strip()
        elif m := RE_AUDIO.search(line):
            if self.video_state["media_type"] == "audio":
                new_filename = m.group(1).strip()
        elif m := RE_CONVERT.search(line):
            new_filename = m.group(1).strip()
        elif m := RE_ALREADY.search(line):
            # An "already downloaded" subtitle file is not the media file
            if not self._is_subtitle_path(m.group(1)):
                new_filename = m.group(1).strip()
        elif state["merged_filename"] is None:
            if m := RE_DEST.search(line):
                dest = m.group(1).strip()
                # yt-dlp downloads subtitles first; the first "[download]
                # Destination:" can therefore be a subtitle file, which must
                # not be captured as the final media filename.
                if not self._is_subtitle_path(dest):
                    new_filename = dest

        if new_filename:
            # Clean up trailing quotes if they leaked through
            new_filename = new_filename.strip('"')
            state["merged_filename"] = new_filename
            logger.debug(f"Captured final filename: {new_filename}")

        # Handle known non-progress output types
        if line.startswith(("[youtube]", "[info]", "[debug]", "[Metadata]")):
            self.signals.append_output.emit(line)
            state["last_line_was_progress"] = False

        elif RE_SLEEP.search(line):
            self.signals.append_output.emit(line)
            state["last_line_was_progress"] = False

        # Handle downloading (calls the progress update helper)
        elif line.startswith("[download]"):
            state = self._update_download_progress(line, state)
            state["last_line_was_progress"] = True

        # Handle Merging
        elif RE_MERGE.search(line):
            self.signals.append_output.emit(line)
            state["last_line_was_progress"] = False

        # Handle all other output
        else:
            self.signals.append_output.emit(line)
            state["last_line_was_progress"] = False

        return state

    def _update_download_progress(self, line, state):
        progress_manager = state["progress_manager"]
        
        # yt-dlp downloads subtitles BEFORE the media streams. Those transfers
        # are tiny and would otherwise wreck the bars: the subtitle's 100% lands
        # in the video bar, and the following media destination shifts the real
        # video download into the audio bar. Subtitle transfers are therefore
        # excluded from all progress-bar accounting (they are still logged).
        in_subtitle_transfer = state.get("downloading_subtitles", False)

        if "Destination:" in line:
            m = RE_DEST.search(line)
            in_subtitle_transfer = bool(m and self._is_subtitle_path(m.group(1)))
            state["downloading_subtitles"] = in_subtitle_transfer
            # Only real media destinations start/switch a progress stream.
            # The destination path lets the manager recognize a RETRY
            # re-print of the same file after a network error (yt-dlp
            # re-prints it instead of starting the next stream).
            if not in_subtitle_transfer:
                progress_manager.on_download_destination(
                    m.group(1) if m else None
                )
        elif m := RE_ALREADY.search(line):
            # An "already downloaded" line is a completed item on its own, so
            # it must not inherit a subtitle-transfer flag. An "already
            # downloaded" SUBTITLE must additionally not start the progress
            # accounting (it would turn the spinner off before the actual
            # media download begins).
            in_subtitle_transfer = self._is_subtitle_path(m.group(1))

        if not in_subtitle_transfer:
            if not progress_manager.is_started():
                progress_manager.mark_started()
                self.signals.set_indeterminate.emit(False)

            percent_match = re.search(r"(\d{1,3}(?:\.\d+)?)%", line)
            if percent_match:
                try:
                    percent = float(percent_match.group(1))
                    video_val, audio_val = progress_manager.update_from_ytdlp_percent(
                        percent
                    )
                    self.signals.update_download_progress.emit(video_val, audio_val)
                    self.signals.update_dock_progress.emit(
                        progress_manager.get_combined_fraction()
                    )
                except ValueError:
                    pass

        # Output line
        if state["last_line_was_progress"]:
            self.signals.update_last_line.emit(line)
        else:
            self.signals.append_output.emit(line)

        return state

    def _analyze_downloaded_file(self, filename):
        # Use cached metadata
        metadata = self.cached_video_metadata
        if not metadata:
            self.signals.append_output.emit("🚩 No metadata available for analysis")
            return

        # Display Metadata Summary
        self.signals.append_output.emit("\nFile info by ffprobe:")
        container = filename.split('.')[-1].upper()
        size_info = f"Container: {container}"
        if metadata.get("file_size_formatted"):
            size_info += f"  (Size: {metadata['file_size_formatted']})"
        self.signals.append_output.emit(size_info)

        if metadata["video_codec"]:
            video_info = (
                f"Video: {metadata['video_codec']} ({metadata['video_long_codec']})"
            )
            if metadata["resolution"]:
                video_info += f", {metadata['resolution']}"
            
            # Show strictly video-specific bitrate as requested
            v_bitrate = metadata["video_bitrate"]
            video_info += f", {v_bitrate} kbps" if v_bitrate else ", none"
            
            self.signals.append_output.emit(video_info)

        if metadata["audio_codec"]:
            audio_info = (
                f"Audio: {metadata['audio_codec']} ({metadata['audio_long_codec']})"
            )
            self.signals.append_output.emit(audio_info)

        if metadata["subtitle_streams"]:
            sub_list = [
                f"{s['lang']} ({s['codec']})" for s in metadata["subtitle_streams"]
            ]
            self.signals.append_output.emit(f"Subtitles: {', '.join(sub_list)}")

        if metadata.get("ffmpeg_bitrate_line"):
            self.signals.append_output.emit(metadata["ffmpeg_bitrate_line"])
        else:
            self.signals.append_output.emit(f"Duration: {metadata['duration_formatted']}")

    def _is_subtitle_path(self, path) -> bool:
        """Return True if the given yt-dlp destination path is a subtitle file."""
        name = (path or "").strip().strip('"').lower()
        # Strip transient download suffixes (".part", ".ytdl", ".temp")
        while name.endswith((".part", ".ytdl", ".temp")):
            name = name.rsplit(".", 1)[0]
        return name.endswith(tuple(SUBTITLE_EXTENSIONS))

    def get_output_dir(self):
        # Use cached value when running in a thread or during download to avoid UI access
        if (
            self.video_state.get("is_download_running")
            or threading.current_thread() is not threading.main_thread()
        ):
            return self.video_state.get("output_dir", DEFAULT_OUTPUT_DIR)
        return self.output_dir_entry.text().strip() or DEFAULT_OUTPUT_DIR

    def get_full_path(self, extension="", lang=None):
        base = self.video_state["base_filename"]
        output_dir = self.get_output_dir()

        if not base:
            return ""

        # If a language is provided, inject it before the extension
        if lang:
            base = f"{base}.{lang}"

        if extension:
            if not extension.startswith("."):
                extension = "." + extension
            return os.path.join(output_dir, base + extension)

        return os.path.join(output_dir, base)

    def get_filename_template(self):
        base = self.video_state["base_filename"]
        output_dir = self.get_output_dir()

        if not base:
            return ""

        return f"{output_dir}/{base}.%(ext)s"

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
            self.signals.append_output.emit(
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
            # Execute ffprobe
            with subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            ) as proc:
                stdout, stderr = proc.communicate(timeout=20)
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
            self.signals.append_output.emit(f"🚩 ffprobe failed for: {os.path.basename(file_path)}")
            logger.error(f"ffprobe error for {file_path}: {e}")
        except subprocess.TimeoutExpired:
            self.signals.append_output.emit("👉 ffprobe timed out")
        except json.JSONDecodeError:
            self.signals.append_output.emit("🚩 ffprobe returned unreadable output")
        except Exception as e:
            self.signals.append_output.emit(
                f"🚩 Unexpected error during file analysis: {e}"
            )
        finally:
            # Try to get the actual bitrate line from ffmpeg -i as a source of truth
            if self.ffmpeg_bin:
                try:
                    ffmpeg_cmd = [self.ffmpeg_bin, "-i", file_path]
                    with subprocess.Popen(
                        ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                    ) as f_proc:
                        _, f_stderr = f_proc.communicate(timeout=10)
                        for f_line in f_stderr.splitlines():
                            if "bitrate:" in f_line:
                                metadata["ffmpeg_bitrate_line"] = f_line.strip()
                                break
                except Exception:
                    pass
            # release file handles
            gc.collect()

        return metadata

    def create_nfo_file(self, video_path):
        if not video_path:
            return

        nfo_path = os.path.splitext(video_path)[0] + ".nfo"

        # Get metadata from state
        # Use base_filename (e.g. "S26E0110 - My Video Title") and strip the S##E#### prefix
        # so the <title> tag contains just the clean title without the episode code
        base_filename = self.video_state.get("base_filename", "")
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
            self.signals.append_output.emit(f"🪪 Created NFO file: {os.path.basename(nfo_path)}")
        except Exception as e:
            self.signals.append_output.emit(f"🚩 Error creating NFO file: {e}")

    def create_edl_file(self, video_path):
        if not video_path:
            self.signals.append_output.emit("📟 create_edl_file called without video_path")
            return []

        json_file = self.get_full_path(".info.json")
        if not os.path.exists(json_file):
            self.signals.append_output.emit(f"📟 .info.json not found at {json_file}")
            return []

        try:
            self.signals.append_output.emit(f"📟 Reading {json_file} for EDL generation...")
            with open(json_file, "r", encoding="utf-8") as f:
                info = json.load(f)

            segments = []
            removed_categories = self.get_selected_sb_categories()
            self.signals.append_output.emit(f"📟 Selected SB categories: {removed_categories}")

            if "sponsorblock_chapters" in info:
                self.signals.append_output.emit(f"📟 Found {len(info['sponsorblock_chapters'])} SB chapters")
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

                    # Check if this segment matches selected categories
                    if any(cat in removed_categories for cat in flat_categories):
                        self.signals.append_output.emit(f"📟 Matching segment: {start} - {end} ({flat_categories})")
                        # Store the first category (main category) with the segment
                        category = flat_categories[0] if flat_categories else "unknown"
                        segments.append({"start": start, "end": end, "category": category})
            else:
                self.signals.append_output.emit("📟 No sponsorblock_chapters in info JSON")

            if not segments:
                self.signals.append_output.emit("📟 No segments to skip found in selected categories")
                return []

            edl_path = os.path.splitext(video_path)[0] + ".edl"
            # Format: [start] [stop] [action]
            # Action 0 = Skip/Cut
            edl_content = "\n".join([f"{seg['start']} {seg['end']} 0" for seg in segments])

            with open(edl_path, "w", encoding="utf-8") as f:
                f.write(edl_content + "\n")

            self.signals.append_output.emit(f"🎬 Created EDL file: {os.path.basename(edl_path)}")
            return segments

        except Exception as e:
            self.signals.append_output.emit(f"🚩 Error creating EDL file: {e}")
            return []
