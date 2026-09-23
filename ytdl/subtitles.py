"""Subtitle selection, file mapping and resync/merge pipeline.

Mixin for YTDLPDownloaderGUI (assembled in ytdl/app.py);
methods access shared state via self."""

import os
import re
from ytdl.sites import DEFAULT_SITE, site_resyncs_auto_subs
from ytdl.utils import SUBTITLE_LANG_ALIASES, canonical_subtitle_lang, format_srt_time, parse_srt_time


class SubtitleMixin:
    def get_selected_subtitle_codes(self):
        return [code for code, cb in self.subtitle_checkboxes.items() if cb.isChecked()]

    def _update_subtitle_checkboxes(self, lang_input):
        # Normalize the input language (e.g., 'en-US' -> 'en')
        compare_code = lang_input.split("-")[0].lower() if lang_input else ""
        
        available = self.video_state.get("available_subtitles", {})
        for code, cb in self.subtitle_checkboxes.items():
            # Update label
            status = available.get(code)
            lang_names = {"en": "English", "de": "German", "es": "Spanish"}
            name = lang_names.get(code, code)
            
            if status:
                cb.setText(f"{name} ({status})")
                cb.setEnabled(True)
                self.subtitle_unavailable.discard(code)
            else:
                # No subtitles available: label it "(none)", uncheck it and
                # keep it disabled (also across UI re-enables, see
                # _set_ui_enabled_state)
                cb.setText(f"{name} (none)")
                cb.setEnabled(False)
                cb.setChecked(False)
                self.subtitle_unavailable.add(code)

        # Subtitle selection is manual: after a fetch the checkboxes keep
        # whatever the user checked (only "(none)" languages are force-
        # unchecked above, since they are disabled). The one exception is
        # "Subtitles only" mode, which downloads nothing else — there the
        # selection is auto-filled (same priority rule the mode's radio
        # button applies via _auto_select_subtitles()).
        if self.video_state.get("media_type") == "subtitles":
            self._auto_select_subtitles()

    def _auto_select_subtitles(self):
        # Identify types
        real_codes = []
        available_codes = []
        
        for code, cb in self.subtitle_checkboxes.items():
            text = cb.text().lower()
            if "(real)" in text:
                real_codes.append(code)
            if "(none)" not in text and cb.isEnabled():
                available_codes.append(code)
        
        # Apply selection rules
        if real_codes:
            # Check all real ones, uncheck others
            for code, cb in self.subtitle_checkboxes.items():
                cb.setChecked(code in real_codes)
        elif available_codes:
            # Check all available (non-none) ones
            for code, cb in self.subtitle_checkboxes.items():
                cb.setChecked(code in available_codes)

    def _subtitle_lang_patterns(self, selected_langs):
        """
        Build the --sub-langs argument covering every known subtitle-key shape.

        yt-dlp matches --sub-langs entries as regexes (fullmatch) against the
        site's subtitle keys. YouTube uses "en" / "a.en" keys, but other sites
        use different shapes (Rumble: "en-auto", with generated subs in
        `subtitles`, not `automatic_captions`). Requesting each language in
        every known key shape makes the site's actual key match; unavailable
        shapes are simply ignored by yt-dlp.
        """
        variants = []
        for lang in selected_langs:
            # ISO 639-2 site keys (ARD/ZDF report German as "deu") need their
            # own pattern: yt-dlp fullmatches each entry against the site's
            # actual keys, so "de" alone would not match "deu".
            keys = [lang] + [
                alias for alias, base in SUBTITLE_LANG_ALIASES.items() if base == lang
            ]
            for key in keys:
                variants.extend([key, f"a.{key}", f"{key}-auto", f"a.{key}-auto"])
        return ",".join(variants)

    def _find_downloaded_subtitles(self, selected_langs):
        """
        Locate downloaded subtitle files for the requested languages.

        Sites name subtitle files differently: YouTube "en.srt"/"a.en.srt",
        Rumble "en-auto.srt" (its generated subs live in `subtitles` under
        "<code>-auto"). The full output filename is "<base>.<lang>.<ext>",
        so scan the output directory once and map every subtitle suffix back
        to the requested base language.

        Returns a list of (lang, path, sub_type) with sub_type
        "real" or "auto", preferring real/manual and canonical names.
        """
        out_dir = self.get_output_dir()
        found_sub_files = {}  # filepath -> (base_lang, sub_type, ext)
        try:
            for fname in os.listdir(out_dir):
                # Suffix match: the base filename (video title) is arbitrary;
                # only the trailing "<lang>[-auto].srt/vtt" part identifies a
                # subtitle file. "a." prefix marks YouTube auto-subs.
                m = re.search(
                    r"(?P<prefix>a\.)?(?P<lang>[A-Za-z0-9]+)(?P<auto>-auto)?\.(?P<ext>srt|vtt)$",
                    fname,
                )
                if not m:
                    continue
                lang = m.group("lang").lower()
                sub_type = "auto" if (m.group("prefix") or m.group("auto")) else "real"
                found_sub_files[os.path.join(out_dir, fname)] = (lang, sub_type, m.group("ext"))
        except OSError:
            pass

        downloaded_subs = []
        for lang in selected_langs:
            # canonical_subtitle_lang maps the site's file key (ARD/ZDF write
            # "<base>.deu.srt") onto the requested UI code ("de").
            base = canonical_subtitle_lang(lang)
            candidates = [
                path
                for path, (flang, _ftype, _ext) in found_sub_files.items()
                if canonical_subtitle_lang(flang) == base
            ]
            if not candidates:
                continue
            # Prefer: manual "real" over "auto", canonical names over
            # "-auto", converted .srt over raw .vtt, then a stable tiebreak.
            candidates.sort(
                key=lambda p: (
                    found_sub_files[p][1] != "real",
                    "-auto" in p,
                    found_sub_files[p][2] != "srt",
                    p,
                )
            )
            chosen = candidates[0]
            downloaded_subs.append((lang, chosen, found_sub_files[chosen][1]))
        return downloaded_subs

    def _normalize_subtitle_names(self, downloaded_subs):
        """
        Rename site-specific subtitle files to the canonical "<base>.<lang>.srt"
        name (YouTube-style), returning an updated (lang, path, sub_type) list.

        yt-dlp names subtitle files after the site's subtitle key, e.g. Rumble
        writes "<base>.en-auto.srt". Post-processing (_resync_subtitle_for_language)
        always writes the canonical "<base>.en.srt" - without this rename, both
        the site-named original and the processed canonical file end up on disk.
        After renaming, resync overwrites the single file in place.
        """
        normalized = []
        for lang, srt_path, sub_type in downloaded_subs:
            canonical = self.get_full_path(f".{lang}.srt")
            if srt_path != canonical and srt_path and os.path.exists(srt_path):
                try:
                    os.replace(srt_path, canonical)
                    srt_path = canonical
                except OSError as e:
                    # Non-fatal: resync then writes the canonical file and the
                    # site-named original stays (old, pre-fix behavior).
                    print(f"Could not rename subtitle file {srt_path}: {e}")
            normalized.append((lang, srt_path, sub_type))
        return normalized

    def _subtitle_needs_resync(self, sub_type):
        """
        Whether a downloaded subtitle file needs the 2-line merge/resync.

        Manual ("real") subs never do. Auto subs do on sites whose generated
        captions arrive as choppy fragments (YouTube), but not on sites that
        deliver them pre-formatted (Rumble) - decided per site profile.
        """
        if sub_type == "real":
            return False
        site = self.video_state.get("site", DEFAULT_SITE)
        return site_resyncs_auto_subs(site)

    def _resync_subtitle_for_language(self, lang, srt_path, removed_segments):
        if not os.path.exists(srt_path):
            self.signals.append_output.emit(f"No srt file: {srt_path}")
            return

        # output_srt is just "Title.en.srt" (no "merged" or "resynced" suffix)
        output_srt = self.get_full_path(f".{lang}.srt")
        
        # If output_srt is different from srt_path (e.g. srt_path was .a.en.srt),
        # we process it into the final .en.srt.
        # If they are the same, we overwrite it (safe because resync_subtitles reads into memory).
        self.resync_subtitles(srt_path, removed_segments, output_srt)

    def resync_subtitles(self, srt_path, removed_segments, output_path):
        if not os.path.exists(srt_path):
            print(f"Subtitle file not found: {srt_path}")
            return False

        has_segments = bool(removed_segments)
        if has_segments:
            self.signals.append_output.emit("Resyncing subtitles...")

        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Split into subtitle blocks
        blocks = content.strip().split("\n\n")
        subtitles = []
        max_time = 0

        # First pass: collect all subtitles and find max time
        for block in blocks:
            lines = block.split("\n")
            if len(lines) < 3:
                continue

            # Parse timestamps
            time_line = lines[1]
            match = re.match(r"([\d:,]+)\s+-->\s+([\d:,]+)", time_line)
            if not match:
                continue

            start_str, end_str = match.groups()
            start_sec = parse_srt_time(start_str)
            end_sec = parse_srt_time(end_str)

            max_time = max(max_time, end_sec)

            # Store subtitle data with original times
            # Convert any multi-line subtitle block into a single line
            # This is crucial for the 2-line merging logic later
            raw_text = "\n".join(lines[2:])
            # Filter out ">>" artifacts often found in auto-generated captions
            clean_text = re.sub(r'^>>\s*', '', raw_text, flags=re.MULTILINE).strip()
            # Remove non-spoken tokens in square brackets, e.g. [laughter], [snorts]
            clean_text = self._strip_nonspoken_brackets(clean_text)
            # Replace all newlines and extra whitespace with a single space
            text = " ".join(clean_text.split())

            if text:
                subtitles.append(
                    {"original_start": start_sec, "original_end": end_sec, "text": text}
                )

        # Build consistent time mapping
        print(f"Building time map for {max_time:.2f}s of content...")
        time_map = self._build_time_map(removed_segments, max_time)

        # Second pass: adjust all timestamps using the consistent time map
        adjusted_subtitles = []
        for sub in subtitles:
            new_start = self._adjust_timestamp_with_map(
                sub["original_start"], time_map, removed_segments
            )
            new_end = self._adjust_timestamp_with_map(
                sub["original_end"], time_map, removed_segments
            )

            # Skip subtitles that fall entirely within removed segments or are too short/empty
            if new_start < 0 or new_end <= new_start or new_end - new_start < 0.15:
                continue

            adjusted_subtitles.append(
                {"start": new_start, "end": new_end, "text": sub["text"]}
            )

        print(
            f"Adjusted {len(adjusted_subtitles)} subtitles using consistent time mapping"
        )

        # Merge choppy cues, optimize around pauses/sentences, build 2-line display
        merged_subtitles = self._format_subtitles_for_display(adjusted_subtitles)

        # Write merged and resynced subtitles
        resynced_blocks = []
        for i, sub in enumerate(merged_subtitles, 1):
            time_line = (
                f"{format_srt_time(sub['start'])} --> {format_srt_time(sub['end'])}"
            )
            block = f"{i}\n{time_line}\n{sub['text']}"
            resynced_blocks.append(block)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(resynced_blocks))

        final_msg = (
            "Subtitles resynced and merged." if has_segments else "Subtitles merged."
        )
        self.signals.append_output.emit(f"💬 {final_msg}")
        return True

    def _calculate_time_adjustment(self, timestamp, removed_segments):
        # Calculates the cumulative duration of all removed segments that occur BEFORE the given timestamp
        adjustment = 0

        for segment in removed_segments:
            seg_start = segment["start"]
            seg_end = segment["end"]
            seg_duration = seg_end - seg_start

            if timestamp <= seg_start:
                # Timestamp is before this segment starts, no more adjustments needed
                break
            elif timestamp >= seg_end:
                # Timestamp is after this segment ends, subtract the full segment duration
                adjustment += seg_duration
            else:
                # Timestamp falls within a removed segment
                # This shouldn't happen if segments were properly removed, but handle it
                # Subtract only the portion before the timestamp
                adjustment += timestamp - seg_start
                break

        return adjustment

    def _build_time_map(self, removed_segments, max_time):
        # creates a lookup that can be used to ensure consistent time adjustmentsacross all subtitles, preventing drift
        time_map = {}

        # Check if we have a drift correction factor
        drift_factor = (
            removed_segments[0].get("drift_factor", 1.0) if removed_segments else 1.0
        )

        # Sample every 0.1 seconds for precise mapping
        for original_time in range(0, int(max_time * 10) + 1):
            original_sec = original_time / 10.0
            adjustment = self._calculate_time_adjustment(original_sec, removed_segments)

            # Apply drift correction to the adjustment
            adjusted_sec = original_sec - (adjustment * drift_factor)
            time_map[original_sec] = adjusted_sec

        return time_map

    def _adjust_timestamp_with_map(self, timestamp, time_map, removed_segments):
        # Find the closest mapped time
        rounded = round(timestamp * 10) / 10

        if rounded in time_map:
            return time_map[rounded]

        # If exact match not found, interpolate
        lower = int(timestamp * 10) / 10
        upper = lower + 0.1

        if lower in time_map and upper in time_map:
            # Linear interpolation
            ratio = (timestamp - lower) / 0.1
            adjusted = time_map[lower] + ratio * (time_map[upper] - time_map[lower])
            return adjusted

        # Fallback to direct calculation
        return timestamp - self._calculate_time_adjustment(timestamp, removed_segments)

    def _smart_wrap(self, text, max_chars=42):
        """
        Splits a single line of text into at most two lines at the best space near the midpoint
        if the text exceeds max_chars. Returns the original text if no split is possible.
        """
        if len(text) <= max_chars:
            return text

        words = text.split()
        if len(words) < 2:
            return text

        # Target midpoint for a balanced split
        midpoint = len(text) // 2
        best_split_idx = 1  # Index of word to start second line
        min_dist = float("inf")

        current_len = 0
        for i in range(len(words) - 1):
            current_len += len(words[i])
            # The space is after words[i]. Its position is current_len.
            dist = abs(current_len - midpoint)
            if dist < min_dist:
                min_dist = dist
                best_split_idx = i + 1
            current_len += 1  # for the space

        line1 = " ".join(words[:best_split_idx])
        line2 = " ".join(words[best_split_idx:])

        return f"{line1}\n{line2}"

    def _strip_nonspoken_brackets(self, text: str) -> str:
        """
        Remove stage directions / non-spoken tokens that commonly appear in subtitles
        inside square brackets, e.g. "[laughter]", "[snorts]".
        """
        if not text:
            return ""

        # Remove any bracketed chunks (non-greedy, no nesting support needed here)
        cleaned = re.sub(r"\[[^\[\]]*?\]", " ", text)
        # Normalize whitespace
        cleaned = " ".join(cleaned.split())
        return cleaned.strip()

    def _flatten_subtitle_text(self, text: str) -> str:
        return " ".join((text or "").replace("\n", " ").split())

    def _subtitle_gap(self, cur: dict, nxt: dict) -> float:
        return (nxt.get("start") or 0) - (cur.get("end") or 0)

    def _last_sentence_boundary_index(self, text: str, min_words_before: int = 1) -> int:
        """Index immediately after the last valid sentence-ending .!? in text, or -1."""
        text = self._flatten_subtitle_text(text)
        if not text:
            return -1

        last_good = -1
        for i, ch in enumerate(text):
            if ch not in ".!?":
                continue

            next_char = text[i + 1] if i + 1 < len(text) else ""
            if next_char not in ("", " "):
                continue

            before = text[:i].rstrip()
            if len(before.split()) < min_words_before:
                continue

            last_token = before.split()[-1] if before else ""
            token_key = last_token.lower().strip("()[]{}\"'“”‘’.,:;")
            if token_key in self._SUBTITLE_SENTENCE_ABBREVS:
                continue

            last_good = i + 1

        return last_good

    def _ends_with_sentence(self, text: str) -> bool:
        flat = self._flatten_subtitle_text(text)
        boundary = self._last_sentence_boundary_index(flat)
        return boundary > 0 and not flat[boundary:].strip()

    def _merge_choppy_subtitle_cues(
        self,
        subtitles,
        max_line_chars: int = 50,
        max_gap_s: float = 1.0,
        max_duration_s: float = 6.0,
    ):
        """Pair rapid consecutive cues into one two-line subtitle event."""
        if not subtitles:
            return []

        merged = []
        i = 0
        while i < len(subtitles):
            cur = subtitles[i]
            if i + 1 < len(subtitles):
                nxt = subtitles[i + 1]
                cur_line = self._flatten_subtitle_text(cur.get("text"))
                nxt_line = self._flatten_subtitle_text(nxt.get("text"))
                gap = self._subtitle_gap(cur, nxt)
                duration = (nxt.get("end") or 0) - (cur.get("start") or 0)

                if (
                    cur_line
                    and nxt_line
                    and gap < max_gap_s
                    and duration < max_duration_s
                    and len(cur_line) < max_line_chars
                    and len(nxt_line) < max_line_chars
                ):
                    merged.append(
                        {
                            "start": cur["start"],
                            "end": nxt["end"],
                            "text": f"{cur_line}\n{nxt_line}",
                        }
                    )
                    i += 2
                    continue

            merged.append(
                {
                    "start": cur["start"],
                    "end": cur["end"],
                    "text": self._flatten_subtitle_text(cur.get("text")),
                }
            )
            i += 1

        return [s for s in merged if s.get("text")]

    def _optimize_subtitle_pause_boundaries(
        self,
        subtitles,
        pause_threshold_s: float = 1.0,
        max_tail_words: int = 3,
        min_words_keep: int = 3,
    ):
        """
        Move loose words off the end of a subtitle so pauses and sentence breaks read cleanly.

        - After a sentence end: push any trailing words to the next cue (any gap).
        - After a long pause with no sentence end: push a short tail (or whole tiny cue) forward.
        """
        if len(subtitles) < 2:
            return subtitles

        subs = [dict(s) for s in subtitles]
        i = 0
        while i < len(subs) - 1:
            cur = subs[i]
            nxt = subs[i + 1]
            gap = self._subtitle_gap(cur, nxt)

            cur_text = self._flatten_subtitle_text(cur.get("text"))
            nxt_text = self._flatten_subtitle_text(nxt.get("text"))
            if not cur_text or not nxt_text:
                i += 1
                continue

            boundary = self._last_sentence_boundary_index(cur_text)
            long_pause = gap >= pause_threshold_s

            if boundary > 0:
                before = cur_text[:boundary].strip()
                tail = cur_text[boundary:].strip()
                tail_words = tail.split()
                if tail_words and (
                    long_pause or len(tail_words) <= max_tail_words
                ):
                    if before and len(before.split()) >= min_words_keep:
                        cur_text = before
                        nxt_text = f"{tail} {nxt_text}".strip()
                    elif not before:
                        cur_text = ""
                        nxt_text = f"{tail} {nxt_text}".strip()

            elif long_pause:
                words = cur_text.split()
                if len(words) <= max_tail_words:
                    nxt_text = f"{cur_text} {nxt_text}".strip()
                    cur_text = ""
                elif len(words) > max_tail_words + min_words_keep:
                    tail = " ".join(words[-max_tail_words:])
                    keep = " ".join(words[:-max_tail_words])
                    cur_text = keep
                    nxt_text = f"{tail} {nxt_text}".strip()

            if cur_text:
                cur["text"] = cur_text
                nxt["text"] = nxt_text
                i += 1
            else:
                nxt["text"] = nxt_text
                subs.pop(i)

        return [s for s in subs if self._flatten_subtitle_text(s.get("text"))]

    def _merge_subtitle_continuations(
        self,
        subtitles,
        max_line_chars: int = 50,
        max_gap_s: float = 1.0,
        max_duration_s: float = 6.0,
    ):
        """Re-merge short continuation cues that the boundary pass left as separate one-liners."""
        if not subtitles:
            return []

        merged = []
        i = 0
        while i < len(subtitles):
            cur = subtitles[i]
            if i + 1 < len(subtitles):
                nxt = subtitles[i + 1]
                cur_line = self._flatten_subtitle_text(cur.get("text"))
                nxt_line = self._flatten_subtitle_text(nxt.get("text"))
                gap = self._subtitle_gap(cur, nxt)
                duration = (nxt.get("end") or 0) - (cur.get("start") or 0)

                if (
                    cur_line
                    and nxt_line
                    and gap < max_gap_s
                    and duration < max_duration_s
                    and len(cur_line) < max_line_chars
                    and len(nxt_line) < max_line_chars
                    and not self._ends_with_sentence(cur_line)
                ):
                    merged.append(
                        {
                            "start": cur["start"],
                            "end": nxt["end"],
                            "text": f"{cur_line}\n{nxt_line}",
                        }
                    )
                    i += 2
                    continue

            merged.append(dict(cur))
            i += 1

        return merged

    def _wrap_subtitle_text(self, text: str, max_chars: int = 50) -> str:
        flat = self._flatten_subtitle_text(text)
        if not flat:
            return ""
        if "\n" in (text or ""):
            lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
            if len(lines) == 2 and all(len(ln) <= max_chars for ln in lines):
                return f"{lines[0]}\n{lines[1]}"
        return self._smart_wrap(flat, max_chars)

    def _fix_subtitle_time_overlaps(self, subtitles):
        for i in range(len(subtitles) - 1):
            current_end = subtitles[i]["end"]
            next_start = subtitles[i + 1]["start"]
            if current_end > next_start:
                subtitles[i]["end"] = max(
                    next_start - 0.05, subtitles[i]["start"] + 0.1
                )
        return subtitles

    def _format_subtitles_for_display(self, subtitles):
        """
        Full auto-caption layout pass:
        1. Merge choppy single-line cues into two-line events
        2. Shift loose words across pauses / sentence ends
        3. Merge same-sentence continuations again
        4. Wrap long single lines and fix timestamp overlaps
        """
        if not subtitles:
            return []

        subs = self._merge_choppy_subtitle_cues(subtitles)
        subs = self._optimize_subtitle_pause_boundaries(subs)
        subs = self._merge_subtitle_continuations(subs)

        formatted = []
        for sub in subs:
            wrapped = self._wrap_subtitle_text(sub.get("text") or "")
            if wrapped:
                formatted.append({**sub, "text": wrapped})

        return self._fix_subtitle_time_overlaps(formatted)

    _SUBTITLE_SENTENCE_ABBREVS = frozenset(
        {
            "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st",
            "vs", "etc", "e.g", "i.e",
        }
    )
