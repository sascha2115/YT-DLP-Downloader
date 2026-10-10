"""Subtitle display layout: turn ASR cues into what a viewer should read.

Pure logic - no Qt, no filesystem. Two stages live here, both module-level
functions so the behaviour can be tested without the app (see
temp/test_subtitle_rolling.py, temp/test_subtitle_layout.py and the seven real
captures temp/subtitle-capture-*.srt):

  rolling_cues()   - the stage the app uses. Groups the ASR's own windows in
                     twos, one window per line, so two caption lines show at once
                     exactly as the YouTube player shows them - shown whole
                     instead of rolling in word by word. Nothing is repeated
                     and nothing is re-cut.

  layout_cues()    - the sentence-aware stage (shipped as v1.4.0, no longer
                     called by the app). Re-packs the word stream into
                     sentence-aligned cues with durations from a reading-speed
                     model. Kept and tested as the alternative; see the
                     "Subtitle display layout" section of AGENTS.md.

Why the ASR file needs no re-packing: the captions already ARE the display.
Each window is a short phrase, every window overlaps the next, and the file has
no line breaks at all - the two lines a viewer sees are two consecutive
windows. So rolling_cues() groups those windows in twos and changes nothing
else; the older sentence-aware stage threw the ASR's boundaries away and rebuilt
them, which fixed sentence alignment at the cost of no longer matching the
player.
"""

import re

# Display-layout targets.
#
# Sized in WORDS, not characters: sentence length is stable across languages and
# channels (median 8-12 words across the real captures) while character counts
# swing with the language alone (11.7 vs 18.8 chars/sec at the same word rate).
#
# Reading speed is a property of a reader, NOT of the video, so TARGET_WPS is a
# constant. Real captures span 2.1-3.3 words/sec - 0.9x to 1.4x of the target -
# and the same speaker varies by 1.5x between episodes, so nothing here can be
# calibrated per channel. MAX_WPS is the grace band: a sentence end always wins
# up to it, and a cue is only cut early when even that does not fit.
LAYOUT_TARGET_WPS = 2.4
LAYOUT_MAX_WPS = 3.4
LAYOUT_MIN_DUR = 1.0
LAYOUT_MAX_DUR = 7.0
# A subtitle lingers into the pause that follows it, but never more than this
# past the time it needs to be read. Fills the ~0.5s flicker between cues (40%
# of transitions were blank) without leaving stale text through a real pause.
LAYOUT_GAP_FILL_S = 1.5
LAYOUT_TARGET_WORDS = 8
LAYOUT_CEILING_WORDS = 10
# A fragment smaller than this cannot be merged back into the previous
# cue (the word ceiling blocks the merge), so split_long_sentence
# rebalances the cut instead of leaving a one-word orphan.
LAYOUT_MIN_FRAGMENT_WORDS = 3
# A pause at least this long inside a long sentence is a good place to break it
LAYOUT_SPLIT_PAUSE_S = 0.6

# ---------------------------------------------------------------------------
# Tuning guide - which knob actually moves something.
#
# If subtitles ever need to feel calmer or snappier, change TARGET_WORDS
# (and CEILING_WORDS = TARGET_WORDS + 2, which is how split_long_sentence and
# pack_sentences keep their one-line/merge limits consistent). 8 is the
# default (and the preference default). That knob sets
# how much text one subtitle carries, so it changes the cue count and how long
# each cue stays up, while leaving the reading speed alone. Measured on a
# 12-minute capture at 3.20 wps:
#
#     words/sub   8       10      12      14
#     cues      283      247     215     188
#     median    2.5s     2.7s    2.9s    3.1s
#
# Do NOT reach for TARGET_WPS to make subtitles "easier": it is only a floor,
# and every capture already speaks faster than it, so moving it between 1.6 and
# 2.4 changes nothing at all on any real video (measured; only a 2.13 wps video
# reacts, and only above 2.4). Nobody can be slowed down by a subtitle setting
# either - a fast speaker is a property of the material.
#
# Pass per-run values via layout_cues(cues, targets={...}) rather than editing
# these constants; that is what keeps a future pace preference safe. Existing
# .srt files are never re-laid-out (no such feature), so changing this only
# affects newly processed subtitles.
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"[.!?][\"'’”)\]]*$")
_CLAUSE_END = (",", ";", ":", "—", "–", "-")

DEFAULT_ABBREVIATIONS = frozenset(
    {"dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e"}
)


def word_stream(cues):
    """Flatten cues to (word, time, source cue index).

    Times are what cue starts are derived from, so they have to stay on
    YouTube's clock. The ASR windows ROLL: every window overlaps the next one
    (100% of pairs, by a median of 1.6s on real captures), so a window's words
    cannot be spread across its own start->end span - its later words would be
    stamped after the NEXT window had already opened, and the timestamps
    ratchet forward. Measured: +2.05s mean against YouTube's grid.

    Spreading each window's words up to the next window's start instead uses
    the interval where they can actually have been spoken: +0.74s mean, and
    non-decreasing by construction because the window starts increase.
    """
    usable = [
        (index, cue) for index, cue in enumerate(cues)
        if (cue.get("text") or "").split()
    ]
    words = []
    previous = 0.0
    for position, (index, cue) in enumerate(usable):
        parts = cue["text"].split()
        start = cue.get("start") or 0.0
        if position + 1 < len(usable):
            span = max((usable[position + 1][1].get("start") or start) - start, 0.001)
        else:
            span = max((cue.get("end") or 0) - start, 0.001)
        step = span / len(parts)
        for i, word in enumerate(parts):
            # The max() is a guard for malformed input with non-increasing
            # window starts; it never fires on real data and so adds no drift.
            when = max(start + step * i, previous)
            words.append((word, when, index))
            previous = when
    return words


def word_ends_sentence(word, abbreviations=DEFAULT_ABBREVIATIONS):
    """Whether `word` closes a sentence (abbreviations and ellipses excluded)."""
    if not _SENTENCE_END.search(word):
        return False
    return word.strip("\"'”’)]").rstrip(".!?…").lower() not in abbreviations


def split_sentences(words, abbreviations=DEFAULT_ABBREVIATIONS):
    """Group the word stream into sentences, cutting after each sentence end."""
    sentences, current = [], []
    for item in words:
        current.append(item)
        if word_ends_sentence(item[0], abbreviations):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    return sentences


def split_long_sentence(sentence, ceiling=LAYOUT_CEILING_WORDS,
                        pause_s=LAYOUT_SPLIT_PAUSE_S,
                        min_fragment_words=LAYOUT_MIN_FRAGMENT_WORDS):
    """Break a sentence that cannot fit the shape into fragments.

    Only ~70% of sentences fit two lines, so this path is the exception, not the
    rule: prefer a real pause, then clause punctuation, and only then fall back
    to the hard ceiling.

    A hard-ceiling cut can leave a one-word remainder ("...take care of" /
    "them.") that no cue can absorb - merging it back would exceed the word
    ceiling - so words move from the previous fragment until the remainder is
    a readable cue, or until the previous fragment would starve.
    """
    if len(sentence) <= ceiling:
        return [sentence]

    fragments, current = [], []
    for item in sentence:
        current.append(item)
        if len(current) < ceiling:
            continue
        cut = len(current)
        for j in range(len(current) - 1, max(len(current) - 5, 1) - 1, -1):
            previous, following = current[j - 1], current[j]
            if (following[1] - previous[1] >= pause_s
                    or previous[0].endswith(_CLAUSE_END)):
                cut = j
                break
        fragments.append(current[:cut])
        current = current[cut:]
    if current:
        fragments.append(current)

    for index in range(len(fragments) - 1):
        shortfall = min_fragment_words - len(fragments[index + 1])
        donor = len(fragments[index]) - shortfall
        if shortfall > 0 and donor >= min_fragment_words:
            fragments[index + 1] = (
                fragments[index][donor:] + fragments[index + 1]
            )
            fragments[index] = fragments[index][:donor]
    return fragments


def split_lines(sentence, target_words=LAYOUT_TARGET_WORDS):
    """One line when it fits, otherwise two balanced ones."""
    texts = [item[0] for item in sentence]
    if len(texts) <= (target_words + 1) // 2:
        return " ".join(texts)
    half = len(texts) / 2
    cut = round(half)
    for j in range(round(half), max(round(half) - 3, 1) - 1, -1):
        if j < len(texts) and (
            texts[j - 1].endswith(_CLAUSE_END) or word_ends_sentence(texts[j - 1])
        ):
            cut = j
            break
    return " ".join(texts[:cut]) + "\n" + " ".join(texts[cut:])


def effective_wps(natural_wps, target=LAYOUT_TARGET_WPS, ceiling=LAYOUT_MAX_WPS):
    """Reading speed to lay this video out at.

    Never slower than the readability target, and at most the grace band above
    it. A video that speaks faster than the band is simply laid out at its own
    pace - that is reported, not hidden, because exceeding the target is then
    unavoidable rather than a packing failure.
    """
    if natural_wps <= 0:
        return target
    return min(max(natural_wps, target), ceiling)


def pack_sentences(sentences, wps, ceiling_words=LAYOUT_CEILING_WORDS,
                   min_dur=LAYOUT_MIN_DUR, max_dur=LAYOUT_MAX_DUR):
    """Group sentences into cues.

    Greedy with look-ahead: take the furthest sentence end that fits the word
    ceiling AND leaves enough time to read what is already there before the
    next sentence starts. The time test is local, not average - a speaker who
    bursts through a sentence needs a shorter cue there even when the video
    overall is comfortable.

    Two exceptions, both forced rather than chosen:
      * a single sentence is always emitted whole (it cannot be dropped), even
        when it does not fit the time available;
      * two fragments closer together than a minimum display are merged, but
        only up to the same word ceiling - a wider cue is a trade the reader
        feels, while two cues drawn on top of each other is a defect.
    """
    starts = [sentence[0][1] for sentence in sentences]
    lengths = [len(sentence) for sentence in sentences]
    groups = []
    index = 0
    while index < len(sentences):
        best = index
        total = 0
        for j in range(index, len(sentences)):
            total += lengths[j]
            if total > ceiling_words:
                break
            available = starts[j + 1] - starts[index] if j + 1 < len(sentences) else max_dur
            # The boundary created by including sentence j sits between its
            # last word and the next sentence's first word - that is the gap
            # that decides whether two cues would have to be shown at once.
            boundary_gap = (
                starts[j + 1] - sentences[j][-1][1] if j + 1 < len(sentences) else max_dur
            )
            fits = total / wps <= available or boundary_gap < min_dur
            if j == index or (fits and total <= ceiling_words):
                best = j
            else:
                break
        groups.append([w for k in range(index, best + 1) for w in sentences[k]])
        index = best + 1
    return groups


def assign_timings(groups, wps, min_dur=LAYOUT_MIN_DUR,
                   max_dur=LAYOUT_MAX_DUR, target_words=LAYOUT_TARGET_WORDS,
                   gap_fill=LAYOUT_GAP_FILL_S):
    """Durations from the boundaries the packer chose.

    A cue starts at the estimated time of its own first word - the midpoint of
    that word's slice inside its ASR window, so still YouTube's clock, strictly
    increasing in word order - and lasts until the next cue starts.

    Three things cannot all hold at once when a speaker is faster than readable:
    reading time, on-time starts, and no overlap. Starts win: a subtitle that
    appears a second late is worse than one shown a moment too briefly, and the
    moment is bounded by the (already merged) gap to the next cue. Cues that
    end earlier than their reading time need are counted as `over_target`, so
    the density of the material is visible instead of silent.
    """
    cues = []
    over_target = 0
    tight = 0
    for index, group in enumerate(groups):
        needed = len(group) / wps
        start = group[0][1]
        duration = min(max_dur, max(min_dur, needed))

        if index + 1 < len(groups):
            gap = groups[index + 1][0][1] - start
            if gap <= 0:
                tight += 1
            elif gap < duration:
                # No room: end where the next cue starts, never overlap.
                duration = gap
            else:
                # A pause. Linger into it so short pauses do not flash the
                # screen blank, but never more than LAYOUT_GAP_FILL_S beyond
                # the reading time - a real pause stays blank.
                duration = min(gap, needed + gap_fill, max_dur)

        if duration < needed - 1e-6:
            over_target += 1
        if duration <= 0:
            duration = min_dur

        cues.append({
            "start": round(start, 3),
            "end": round(start + duration, 3),
            "duration": duration,
            "needed": needed,
            "words": len(group),
            "text": split_lines(group, target_words),
        })

    stats = {
        "cues": len(cues),
        "two_line": sum(1 for c in cues if "\n" in c["text"]),
        "over_target": over_target,
        "tight": tight,
        "words": sum(c["words"] for c in cues),
    }
    return cues, stats


def layout_cues(cues, abbreviations=DEFAULT_ABBREVIATIONS, targets=None):
    """Lay out parsed ASR cues for display. Returns (cues, stats).

    `cues` are dicts with start/end/text. Timestamps are the source ones and are
    never retimed - see SubtitleMixin.resync_subtitles.

    `targets` optionally overrides the layout constants for this run, e.g.
    `layout_cues(cues, targets={"target_words": 12, "ceiling_words": 14})`.
    Every key maps to a module constant of the same name minus the LAYOUT_
    prefix: target_wps, max_wps, min_dur, max_dur, target_words,
    ceiling_words, split_pause_s, gap_fill_s, min_fragment_words. This is
    the hook a subtitle-pace preference would use - without it the
    constants are only import-time defaults and cannot be varied per run.
    """
    pick = (targets or {}).get
    target_wps = pick("target_wps", LAYOUT_TARGET_WPS)
    max_wps = pick("max_wps", LAYOUT_MAX_WPS)
    min_dur = pick("min_dur", LAYOUT_MIN_DUR)
    max_dur = pick("max_dur", LAYOUT_MAX_DUR)
    target_words = pick("target_words", LAYOUT_TARGET_WORDS)
    ceiling_words = pick("ceiling_words", LAYOUT_CEILING_WORDS)
    split_pause_s = pick("split_pause_s", LAYOUT_SPLIT_PAUSE_S)
    gap_fill_s = pick("gap_fill_s", LAYOUT_GAP_FILL_S)
    min_fragment_words = pick("min_fragment_words",
                              LAYOUT_MIN_FRAGMENT_WORDS)

    usable = [c for c in cues if (c.get("text") or "").strip()]
    if not usable:
        return [], {"cues": 0, "two_line": 0, "over_target": 0, "tight": 0, "words": 0}

    span = max(usable[-1]["end"] - usable[0]["start"], 0.001)
    natural_wps = sum(len((c.get("text") or "").split()) for c in usable) / span
    wps = effective_wps(natural_wps, target=target_wps, ceiling=max_wps)

    words = word_stream(usable)
    sentences = split_sentences(words, abbreviations)
    fragments = []
    for sentence in sentences:
        fragments.extend(
            split_long_sentence(sentence, ceiling=ceiling_words,
                                pause_s=split_pause_s,
                                min_fragment_words=min_fragment_words)
        )
    groups = pack_sentences(fragments, wps, ceiling_words=ceiling_words,
                            min_dur=min_dur, max_dur=max_dur)

    laid_out, stats = assign_timings(groups, wps, min_dur=min_dur, max_dur=max_dur,
                                     target_words=target_words, gap_fill=gap_fill_s)
    stats["natural_wps"] = round(natural_wps, 2)
    stats["wps"] = round(wps, 2)
    return laid_out, stats


# ---------------------------------------------------------------------------
# Rolling layout: the YouTube player's own look.
#
# The ASR file is already the display, and it needs only one change: pairing.
# Each window is a short phrase, every window overlaps the next (100% of pairs on
# the real captures, median 1.6s), and the downloaded file has NO line breaks at
# all (0 of 2398 cues across seven captures) - because in the YouTube player the
# two lines a viewer sees are two consecutive windows, the finished one on top
# and the next one rolling in underneath.
#
# So the display is rebuilt by grouping the windows in twos: two consecutive
# ASR windows become one two-line cue, shown whole. Nothing is repeated - each
# window appears exactly once, on the line it occupies in the player:
#
#     SET 1   the Peruvian skulls and                  <- window 1
#             No, before I thought Oh, go ahead.       <- window 2
#     SET 2   Sorry.                                   <- window 3
#             No, go ahead.                            <- window 4
#
# A cue starts at its FIRST window's own timestamp - verbatim from the source
# file, not an interpolated word time and not delayed for reading speed - so the
# top line appears exactly when YouTube's would. It ends where the NEXT set
# starts, which is that set's first window, so cues never overlap and the screen
# changes at a moment the ASR itself marks.
#
# No reading-speed model is involved: durations are the ASR's own gaps, so a fast
# speaker gets short cues and a real pause stays blank - the same trade the
# player makes.
#
# Each window's text is written verbatim on its own line - NEVER re-wrapped. A
# long window stays a long line, exactly as YouTube shows it; a player that wants
# to fit it wraps it itself, and second-guessing that here would make the file
# differ from the captions for no gain.
# ---------------------------------------------------------------------------
ROLLING_MIN_DUR = 0.2
# How many consecutive ASR windows share one cue. 2 is the player's look: the
# finished line on top, the new one below.
ROLLING_WINDOWS_PER_CUE = 2


def rolling_cues(cues, windows_per_cue=ROLLING_WINDOWS_PER_CUE,
                 min_dur=ROLLING_MIN_DUR):
    """Group ASR windows into the two-line cues the YouTube player shows.

    Returns (cues, stats). `windows_per_cue` consecutive source windows become
    one cue (2 by default), each window on its own line and shown whole - never
    repeated, never merged with another window's words, and never re-wrapped.

    `start` is the group's FIRST window's own timestamp, verbatim from the
    source file, so the top line appears exactly when YouTube's would. `end` is
    the next group's first window, so cues never overlap and the display changes
    at an instant the ASR itself marks. The last group has no successor and keeps
    its own last window's end.

    A window longer than one line stays one long line: that is what the ASR says
    and what the player shows.
    """
    usable = [c for c in cues if (c.get("text") or "").strip()]
    if not usable:
        return [], {"cues": 0, "two_line": 0, "windows": 0}

    laid_out = []
    step = max(1, windows_per_cue)
    for index in range(0, len(usable), step):
        group = usable[index:index + step]
        start = group[0].get("start") or 0.0

        # Ends where the next group opens: the next window the ASR starts. The
        # last group has no successor, so it keeps its own end - with a floor, so
        # a zero-length source window still displays.
        if index + step < len(usable):
            end = usable[index + step].get("start") or start
        else:
            end = group[-1].get("end") or start
        if end <= start:
            end = start + min_dur

        # One window per line, text verbatim. Whitespace is normalised (the ASR
        # emits stray double spaces and trailing blanks) but nothing is split.
        lines = [" ".join((w.get("text") or "").split()) for w in group]

        laid_out.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": end - start,
            "words": sum(len(line.split()) for line in lines),
            "text": "\n".join(lines),
        })

    stats = {
        "cues": len(laid_out),
        "two_line": sum(1 for c in laid_out if "\n" in c["text"]),
        "windows": len(usable),
    }
    return laid_out, stats
