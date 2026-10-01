"""Subtitle display layout: turn ASR cues into readable, sentence-aligned cues.

Pure logic - no Qt, no filesystem. `layout_cues()` is the whole stage and every
helper is a module-level function, so the behaviour can be tested without the
app (see temp/test_subtitle_layout.py and the six real captures
temp/subtitle-capture-*.srt).

What it replaces: three heuristic passes (pair choppy cues -> shift words across
pauses -> pair again). Those produced two-line cues whose line break was a
character midpoint and whose boundaries were inherited from the ASR's
time-based chunking, so ~75% of subtitles ended mid-sentence - they closed
wherever a 50-character budget happened to fall.

The rule here is a compromise, in this order:
  1. a cue ends at a sentence end whenever one fits the shape and the time;
  2. a sentence too long for the shape is broken at a pause or clause mark;
  3. a cue never overlaps the next one, and never shows for less than
     LAYOUT_MIN_DUR.
Timing: starts stay on the ASR grid (the source cue that carries the first
word), durations come from the word count at the effective reading speed. That
is the one thing the old pipeline never did - it inherited the ASR's durations.
"""

import re

# Display-layout targets.
#
# Sized in WORDS, not characters: sentence length is stable across languages and
# channels (median 9-12 words across six real captures) while character counts
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
LAYOUT_TARGET_WORDS = 10
LAYOUT_CEILING_WORDS = 12
# A pause at least this long inside a long sentence is a good place to break it
LAYOUT_SPLIT_PAUSE_S = 0.6

# ---------------------------------------------------------------------------
# Tuning guide - which knob actually moves something.
#
# If subtitles ever need to feel calmer or snappier, change TARGET_WORDS
# (and CEILING_WORDS = TARGET_WORDS + 2, which is how split_long_sentence and
# pack_sentences keep their one-line/merge limits consistent). That knob sets
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
                        pause_s=LAYOUT_SPLIT_PAUSE_S):
    """Break a sentence that cannot fit the shape into fragments.

    Only ~70% of sentences fit two lines, so this path is the exception, not the
    rule: prefer a real pause, then clause punctuation, and only then fall back
    to the hard ceiling.
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
    ceiling_words, split_pause_s, gap_fill_s. This is the hook a subtitle-pace
    preference would use - without it the constants are only import-time
    defaults and cannot be varied per run.
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
                                pause_s=split_pause_s)
        )
    groups = pack_sentences(fragments, wps, ceiling_words=ceiling_words,
                            min_dur=min_dur, max_dur=max_dur)

    laid_out, stats = assign_timings(groups, wps, min_dur=min_dur, max_dur=max_dur,
                                     target_words=target_words, gap_fill=gap_fill_s)
    stats["natural_wps"] = round(natural_wps, 2)
    stats["wps"] = round(wps, 2)
    return laid_out, stats
