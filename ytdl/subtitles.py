"""Subtitle selection, file mapping and resync/merge pipeline.

Mixin for YTDLPDownloaderGUI (assembled in ytdl/app.py);
methods access shared state via self."""

import logging
import os
import re
from ytdl.sites import DEFAULT_SITE, site_resyncs_auto_subs
from ytdl.subtitle_layout import layout_cues
from ytdl.utils import (
    SUBTITLE_LANG_ALIASES,
    canonical_subtitle_lang,
    format_srt_time,
    format_subtitle_stats,
    parse_srt_time,
)

logger = logging.getLogger(__name__)


class SubtitleMixin:
    def get_selected_subtitle_codes(self):
        return [code for code, cb in self.subtitle_checkboxes.items() if cb.isChecked()]

    def _update_subtitle_checkboxes(self, _lang_input):
        # The language the site reported is not needed for the labels: availability
        # comes from video_state["available_subtitles"], keyed by UI language code.
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

    def _subtitle_type_for_file(self, lang, marked_auto):
        """
        "auto" (site-generated) or "real" (manual) for a downloaded subtitle file.

        A file name only proves a track is auto-generated when the SITE named
        it that way (Rumble's "en-auto"). It does NOT work for YouTube: current
        yt-dlp merges "--write-subs" and "--write-auto-subs" into one language
        namespace (a manual track wins the slot) and writes automatic captions
        as plain "<title>.en.srt" - no "a." prefix, exactly like a manual
        track. Treating every unmarked name as "real" therefore disabled the
        2-line merge for every YouTube ASR video and stored the raw rolling
        captions instead (see _subtitle_needs_resync).

        So an unmarked name is resolved through the info fetch, which knows the
        difference: video_state["available_subtitles"][lang] is "auto" when the
        site offers no manual track for that language. Without an info fetch
        (replays, tests) the name is the only evidence, so it stays "real".
        """
        if marked_auto:
            return "auto"
        base = canonical_subtitle_lang(lang)
        offered = (self.video_state.get("available_subtitles") or {}).get(base)
        if offered in ("auto", "real"):
            return offered
        return "real"

    def _find_downloaded_subtitles(self, selected_langs):
        """
        Locate downloaded subtitle files for the requested languages.

        Sites name subtitle files differently: YouTube writes both manual and
        auto-generated tracks as "<base>.<lang>.srt", Rumble names its generated
        subs "en-auto.srt" (they live in `subtitles` under "<code>-auto"). The
        full output filename is "<base>.<lang>.<ext>", so scan the output
        directory once and map every subtitle suffix back to the requested base
        language.

        Returns a list of (lang, path, sub_type) with sub_type
        "real" or "auto", preferring real/manual and canonical names.
        """
        out_dir = self.get_output_dir()
        found_sub_files = {}  # filepath -> (base_lang, sub_type, ext)
        try:
            for fname in os.listdir(out_dir):
                # Suffix match: the base filename (video title) is arbitrary;
                # only the trailing "<lang>[-auto].srt/vtt" part identifies a
                # subtitle file. An "a." prefix or a "-auto" suffix marks a
                # generated track; an unmarked name is resolved via the info
                # fetch (see _subtitle_type_for_file).
                m = re.search(
                    r"(?P<prefix>a\.)?(?P<lang>[A-Za-z0-9]+)(?P<auto>-auto)?\.(?P<ext>srt|vtt)$",
                    fname,
                )
                if not m:
                    continue
                lang = m.group("lang").lower()
                marked_auto = bool(m.group("prefix") or m.group("auto"))
                sub_type = self._subtitle_type_for_file(lang, marked_auto)
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

    def _uses_bare_subtitle_name(self, subtitle_count=1):
        """Whether subtitle files are stored without a language code.

        "<title>.en.srt" repeats what the name already implies when only one
        subtitle file was produced, so the file is simply "<title>.srt" - the
        name players and editors expect. With two or more files the code is the
        only thing that tells them apart, so it is kept.

        The count is the number of files that actually landed, not the number of
        selected languages: two selected languages resolve to a single file
        when the site offers only one of them.
        """
        return subtitle_count == 1

    def _bare_subtitle_credit(self, selected_langs):
        """Which requested language a language-less "<title>.srt" may count for.

        The bare name does not record its language, so it can only be trusted
        when the request covers every language the site offers for this video:
        the file then has to be one of them. At most ONE language is credited -
        a request for several languages still misses the others, so it is
        re-requested and the folder ends up language-tagged again.

        Returns None when the file cannot be attributed: without an info fetch
        the offered languages are unknown, and when the site offers a language
        that was NOT requested the bare file may well be that other language.
        Crediting it anyway would skip the download and silently leave the
        wrong language on disk.
        """
        if not selected_langs:
            return None
        offered = set(self.video_state.get("available_subtitles") or {})
        if offered and offered.issubset(set(selected_langs)):
            return selected_langs[0]
        return None

    def _subtitle_output_path(self, lang, extension=".srt", bare=False):
        """Path the processed subtitle file for `lang` is written to."""
        if bare:
            return self.get_full_path(extension)
        return self.get_full_path(f".{lang}{extension}")

    def _normalize_subtitle_names(self, downloaded_subs, bare=False):
        """
        Rename site-specific subtitle files to the canonical name -
        "<base>.<lang>.srt", or "<base>.srt" when `bare` (one file for the
        whole video) - returning an updated (lang, path, sub_type) list.

        yt-dlp names subtitle files after the site's subtitle key, e.g. Rumble
        writes "<base>.en-auto.srt". Post-processing (_resync_subtitle_for_language)
        always writes the canonical name - without this rename, both the
        site-named original and the processed canonical file end up on disk.
        After renaming, resync overwrites the single file in place.
        """
        normalized = []
        for lang, srt_path, sub_type in downloaded_subs:
            canonical = self._subtitle_output_path(lang, bare=bare)
            if srt_path != canonical and srt_path and os.path.exists(srt_path):
                try:
                    os.replace(srt_path, canonical)
                    srt_path = canonical
                except OSError as e:
                    # Non-fatal: resync then writes the canonical file and the
                    # site-named original stays (old, pre-fix behavior).
                    logger.warning(f"Could not rename subtitle file {srt_path}: {e}")
            normalized.append((lang, srt_path, sub_type))
        return normalized

    def _remove_superseded_bare_subtitle(self):
        """Drop a language-less "<base>.srt" that language-tagged files replaced.

        Only reachable when a video offers more than one subtitle language now,
        while an earlier run (when it offered just one) stored its single
        subtitle without a code. This run's tagged files supersede that
        content, and the leftover's language can no longer be read from its
        name, so it is removed rather than silently duplicating a track.

        Returns the removed path, or None if there was nothing to remove.
        """
        removed = None
        for extension in (".srt", ".vtt"):
            path = self.get_full_path(extension)
            if not os.path.isfile(path):
                continue
            try:
                os.remove(path)
            except OSError as e:
                logger.warning(f"Could not remove superseded subtitle {path}: {e}")
                continue
            logger.info(f"Removed superseded language-less subtitle: {path}")
            removed = path
        return removed

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

    def _resync_subtitle_for_language(self, lang, srt_path, bare=False):
        if not os.path.exists(srt_path):
            self.signals.append_output.emit(f"No srt file: {srt_path}")
            return

        # output_srt is "Title.en.srt" (no "merged" or "resynced" suffix), or
        # plain "Title.srt" for a video whose only subtitle needs no code.
        output_srt = self._subtitle_output_path(lang, bare=bare)

        # If output_srt is different from srt_path (e.g. srt_path was .a.en.srt),
        # we process it into the final name.
        # If they are the same, we overwrite it (safe because resync_subtitles reads into memory).
        self.resync_subtitles(srt_path, output_srt)

    def resync_subtitles(self, srt_path, output_path):
        """Lay out a downloaded subtitle file and write it to output_path.

        Timestamps are kept in the source file's own timeline. This app never
        cuts the media - SponsorBlock segments are marked and written to an
        .edl for the player to jump - so an EDL-skipping player looks
        subtitles up by current media time, which after a jump is still the
        original time. Subtitles of an earlier version were retimed into an
        edited timeline to match a physically cut video; that workflow was
        dropped because cutting is only clean at keyframes, and the EDL route
        needs no re-encode at all.
        """
        if not os.path.exists(srt_path):
            logger.warning(f"Subtitle file not found: {srt_path}")
            return False

        try:
            with open(srt_path, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeError) as e:
            logger.warning("Could not read subtitle file %s: %s", srt_path, e)
            self.signals.append_output.emit(
                f"⚠️ Could not read subtitle file: {os.path.basename(srt_path)}"
            )
            return False

        # Accept common CRLF/CR subtitle files as well as Unix LF files.
        normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
        blocks = re.split(r"\n\s*\n", normalized_content.strip())
        subtitles = []
        max_time = 0
        no_text_cues = 0

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

            # Convert any multi-line subtitle block into a single line.
            # This is crucial for the 2-line merging logic later
            raw_text = "\n".join(lines[2:])
            # Filter out ">>" artifacts often found in auto-generated captions
            clean_text = re.sub(r'^>>\s*', '', raw_text, flags=re.MULTILINE).strip()
            # Remove non-spoken tokens in square brackets, e.g. [laughter], [snorts]
            clean_text = self._strip_nonspoken_brackets(clean_text)
            # Replace all newlines and extra whitespace with a single space
            text = " ".join(clean_text.split())

            if text:
                subtitles.append({"start": start_sec, "end": end_sec, "text": text})
            else:
                # A block that held nothing but artifacts or stage directions
                # ("[musik]", ">>"), which _strip_nonspoken_brackets removes on
                # purpose. Counted so the summary reconciles with the cue count
                # in the file the user can open.
                no_text_cues += 1

        if not subtitles:
            logger.warning("No valid subtitle cues found in %s", srt_path)
            self.signals.append_output.emit(
                "⚠️ No valid subtitle cues found; keeping the original file"
            )
            return False

        # Drop cues with no usable duration. Timestamps are the source ones -
        # see the docstring for why they are never retimed.
        adjusted_subtitles = [
            sub
            for sub in subtitles
            if sub["start"] >= 0
            and sub["end"] > sub["start"]
            and sub["end"] - sub["start"] >= 0.15
        ]

        logger.debug(f"Adjusted {len(adjusted_subtitles)} subtitle cues")

        # The 0.15s floor is the first of two places that can drop a cue, and
        # it does so silently - counted here for the summary line below.
        too_short_cues = len(subtitles) - len(adjusted_subtitles)

        # Sentence-aligned 2-line layout (see ytdl/subtitle_layout.py)
        merged_subtitles, layout_stats = self._format_subtitles_for_display(
            adjusted_subtitles
        )

        # Write merged and resynced subtitles
        if not merged_subtitles:
            logger.warning("Subtitle processing produced no cues for %s", srt_path)
            self.signals.append_output.emit(
                "⚠️ Subtitle processing produced no cues; keeping the original file"
            )
            return False

        resynced_blocks = []
        for i, sub in enumerate(merged_subtitles, 1):
            time_line = (
                f"{format_srt_time(sub['start'])} --> {format_srt_time(sub['end'])}"
            )
            block = f"{i}\n{time_line}\n{sub['text']}"
            resynced_blocks.append(block)

        # Write beside the destination and replace atomically so a failed write
        # never truncates the original subtitle file. Blocks are joined by a
        # blank line and the file ends with a newline: that is the shape an SRT
        # is expected to have, and a parser can drop a trailing block that has
        # none.
        temp_path = f"{output_path}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(resynced_blocks) + "\n")
            os.replace(temp_path, output_path)
        except OSError:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise

        self.signals.append_output.emit("💬 Subtitles merged.")

        # What the layout actually did, per file. A whole video's subtitles can
        # be skipped by the gate upstream (or merged into nothing) without a
        # single word of this showing up anywhere else in the panel, so the
        # counts are reported next to the file they belong to.
        two_line_cues = sum(
            1 for sub in merged_subtitles if "\n" in (sub.get("text") or "")
        )
        self.signals.append_output.emit(
            "  📊 "
            + format_subtitle_stats(
                len(adjusted_subtitles),
                len(merged_subtitles),
                counters={
                    "two_line": two_line_cues,
                    "wps": layout_stats.get("wps"),
                    "over_target": layout_stats.get("over_target", 0),
                    "no_text": no_text_cues,
                    "too_short": too_short_cues,
                    "dropped": layout_stats.get("dropped", 0),
                },
                name=os.path.basename(output_path),
            )
        )
        self._warn_about_dropped_cues(layout_stats.get("dropped_cues") or [],
                                      os.path.basename(output_path))
        return True

    def _warn_about_dropped_cues(self, dropped, name):
        """Report cues the overlap guard could not place, by name.

        Should never fire - the layout produces a monotonic sequence, and the
        guard only drops what cannot be shifted into place. It exists so that a
        text loss is impossible to miss: the panel line names the file and the
        count, the log file carries the text of each lost cue.
        """
        if not dropped:
            return
        for cue in dropped:
            logger.warning(
                "Subtitle cue dropped by the overlap guard in %s at %.3fs: %s",
                name,
                cue.get("start") or 0.0,
                (cue.get("text") or "").replace("\n", " ")[:80],
            )
        preview = ", ".join(
            f"{(cue.get('text') or '').replace(chr(10), ' ')[:40]}"
            for cue in dropped[:2]
        )
        suffix = f" — {preview}" if preview else ""
        self.signals.append_output.emit(
            f"⚠️ {len(dropped)} subtitle line(s) could not be timed and were "
            f"dropped from {name}{suffix}"
        )

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

    def _fix_subtitle_time_overlaps(self, subtitles):
        """Make the cue sequence monotonic and non-overlapping, without losing text.

        Returns (cues, dropped) where `dropped` lists the cues that could not be
        placed at all, so the caller can report them.

        Placement is preferred over deletion. A cue that would overlap is first
        given room by trimming its neighbour's end (text untouched, no cascade);
        only a cue that claims to start at or before its predecessor - or one
        that arrives with no duration - is shifted to where it fits. A subtitle
        that appears a little late is far better than a missing sentence, so
        nothing is dropped unless it still has no room once shifted.
        """
        if not subtitles:
            return ([], [])

        minimum_duration = 0.2
        fixed = []
        dropped = []
        for sub in subtitles:
            if not sub:
                continue
            start = sub.get("start")
            end = sub.get("end")
            if start is None or end is None:
                dropped.append(sub)
                continue
            if end <= start:
                # No duration at all: keep the text, give it the shortest
                # display rather than losing the line.
                end = start + minimum_duration

            current = dict(sub)
            if fixed:
                previous = fixed[-1]
                if start <= previous["start"]:
                    # Out-of-order cue: push it behind its predecessor.
                    start = previous["end"]
                    if end <= start:
                        dropped.append(sub)
                        continue
                if start < previous["end"]:
                    previous["end"] = start

            current["start"] = start
            current["end"] = end
            fixed.append(current)
        return fixed, dropped

    def _format_subtitles_for_display(self, subtitles):
        """Lay out parsed cues for display: sentence-aligned, two lines, timed.

        Delegates to ytdl.subtitle_layout (pure, no Qt) and returns
        (cues, stats). See that module for the rules; the short version is that
        a cue ends at a sentence end whenever it fits the word ceiling and the
        available time, and its duration comes from the word count rather than
        being inherited from the ASR.

        `stats["dropped"]` counts the cues the final overlap guard could not
        place at all, and `stats["dropped_cues"]` carries them so the caller can
        name them. Both are zero for every real capture - they exist so that a
        text loss can never be silent.
        """
        if not subtitles:
            return ([], {})

        laid_out, stats = layout_cues(
            subtitles, abbreviations=self._SUBTITLE_SENTENCE_ABBREVS
        )
        # Final guard only: layout_cues already produces a monotonic,
        # non-overlapping sequence, so this drops nothing in practice - it is
        # here so a future change cannot write an overlapping or lossy file.
        fixed, dropped = self._fix_subtitle_time_overlaps(laid_out)
        stats["dropped"] = len(dropped)
        stats["dropped_cues"] = dropped
        return fixed, stats

    _SUBTITLE_SENTENCE_ABBREVS = frozenset(
        {
            "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st",
            "vs", "etc", "e.g", "i.e",
        }
    )
