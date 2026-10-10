# Changelog

Every user-visible change to YT-DLP Downloader, newest first.

This file is the **canonical** record of what each version does. GitHub
release notes are written from it and link back here — if the two ever
disagree, this file is right.

**Conventions**

- Versions are the `APP_VERSION` values the app actually ran as
  (`ytdl/__init__.py`), not git tags. A version listed here *without* a
  release link was committed and shipped, but never published as a
  GitHub Release; its notes are folded into the next published release.
- Dates are the commit dates.
- Entries describe what a **user** observes. The commit history has the
  internals.

**Markers**

- **⚠ Changed on disk** — the release renames, moves or rewrites files
  you already have, or changes what gets written from now on. Read these
  before upgrading.
- **🧪 Verified** — the release's verification story, where it is a claim
  worth checking rather than assuming.

---


## 1.4.2 — 2026-10-10

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.4.2) · tag `v1.4.2`

### ⚠ Changed on disk — subtitles match the YouTube player again

- Subtitles are laid out from the captions' **own lines**, two at a time,
  instead of being regrouped into sentence-aligned cues:

      LINE 1: the Peruvian skulls and
      LINE 2: No, before I thought Oh, go ahead.

  Nothing is repeated and nothing is re-cut — every caption line appears
  exactly once, on the line it occupies in the player — and a long line is
  left exactly as long as the captions make it rather than being wrapped.
  The top line of each pair starts at exactly the time the caption file
  gives it, so it appears when YouTube's would.
- This **reverses the 1.4.0 layout**, which broke subtitles at sentence
  ends (75% mid-sentence → 20–44%). Copying the player means a subtitle can
  end mid-sentence again — the captions' lines are phrases, not sentences —
  and that is the trade. `layout_cues()` and the `subtitle_words_per_cue`
  preference from 1.4.1 are both still in the code and still tested, so the
  sentence-aware layout is one call site away if it is ever wanted back;
  the preference has **no effect** on new downloads until then, and the
  Preferences dialog now says so.
- Affects newly processed subtitles only; existing `.srt` files are never
  re-laid-out.

### Also

- **No more one-word subtitles from a cut.** In the sentence-aware layout,
  a sentence one word over the word ceiling used to be cut as a full cue
  plus a single-word cue ("…take care of" / "them.") that no packing rule
  could merge back. The cut is now rebalanced so the tail carries at least
  three words (the 11-word sentence splits 8+3, not 10+1). A subtitle that
  is genuinely a one-word sentence ("Yeah.", "Mhm.") still stands alone —
  that is what was said.

## 1.4.1 — 2026-10-08

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.4.1) · tag `v1.4.1`

- **Subtitle pace is now a preference.** `subtitle_words_per_cue` in
  `preferences.json` (whole number, 6–16, default 8) sets how many
  words one subtitle carries. Lower values split subtitles sooner, so
  the next cue starts earlier — for videos where subtitles feel a
  moment too late or one line runs long. Affects newly processed
  subtitles only; existing `.srt` files are not re-laid-out. The
  setting is also editable in the Preferences dialog, which now
  explains it. Reading speed itself stays fixed — it is a property of
  the video, not an adjustable pace.

Docs:

- `README.md` drops its prerequisites table for a three-line summary plus
  a link to `setup.md` — two copies of a dependency list drift apart.
- The "Common Gotchas" list is merged into a single copy in `AGENTS.md`.
  The two items that were user-facing (the yt-dlp nightly escape hatch,
  and the ARD/ZDF "not available but it downloads anyway" note) move to
  `setup.md` §6, which the README already links to.

## 1.4.0 — 2026-10-01

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.4.0) · tag `v1.4.0`

The release that ships the new subtitle layout. The layout landed in code
as 1.3.13 and waited here until its output had been checked against real
downloads — **1.4.0 is the first version you get it in**.

### ⚠ Changed on disk — subtitles get a new layout

- Subtitles break at **sentence ends**, not wherever YouTube's rolling
  ASR window happened to end. Measured across six real videos, cues
  ending mid-sentence drop from **75% to 20–44%**.
- Time on screen comes from a reading-speed model (2.4 words/sec target,
  the video's own pace used inside a 2.4–3.4 band) instead of being
  inherited from the ASR window — so a 10-word subtitle and a 30-word
  one no longer flash past at the same speed.
- Start times stay on the video's own clock. Cues never overlap, never
  exceed two lines, and no sentence is ever dropped.
- The output panel reports what it did:
  `📊 Title.srt: 2957 in → 1132 out · 1114 two-line · 3.28 wps · 638 fast-paced`.
  "fast-paced" describes the speaker, not a problem with the file.
- **Existing `.srt` files are never re-laid out** — there is deliberately
  no re-layout feature, so only newly downloaded subtitles use the new
  layout. The `subtitle-layout-baseline` tag marks the last pre-refactor
  output if you want to compare against it.

### ⚠ Changed on disk — subtitle naming and SponsorBlock

- A video's only subtitle is stored as `Title.srt` rather than
  `Title.en.srt`. Two or more languages keep their codes, since the code
  is then the only thing telling them apart. *(Shipped in 1.3.10; listed
  here because this is the release people upgrade to.)*
- SponsorBlock ranges are **marked** and written to an `.edl`; the media
  is never cut or re-encoded. The subtitle-retiming code that belonged
  to the old cut-the-video workflow is now **removed** — subtitles stay
  in the video's own timeline, which is what an EDL-skipping player
  expects. This is a no-op in practice: the code was already dead, and
  subtitles already used source timestamps.

### Downloads

- **Cancel** a running download with the new button or `Esc`. It kills
  the whole process group, so `ffmpeg` stops with `yt-dlp` instead of
  being orphaned mid-merge. *(1.3.9)*
- The panel names what it is downloading — "Video + Audio", "Video
  only", "Audio only" or "Subtitles only" — so the two adjacent radio
  buttons can no longer be confused. *(1.3.15)*

### Fixed

- YouTube's automatic captions are classified from the info fetch again
  instead of from the filename, restoring the two-line treatment for
  every ASR video. *(1.3.12)*
- Subtitle sync, line width, and the flicker between cues. *(1.3.14)*
- The UI no longer stays disabled while a slow SponsorBlock lookup is in
  flight. *(1.3.8)*
- The output folder and the files written into it can no longer disagree.
  *(1.3.8)*
- Hardened URL gating and download finalization. *(1.3.7)*

### Smaller things

- The dock progress bar no longer jumps backwards at the video→audio
  handover. *(1.3.11)*
- The supported-sites line reads "ARD/ZDF Mediathek" instead of listing
  both.
- The layout is tunable per run via
  `layout_cues(cues, targets={...})`, so pacing can be adjusted without
  editing code.

### 🧪 Verified

- The word stream in equals the word stream out — asserted per capture
  across six real `.srt` fixtures. Text only ever moves between
  neighbouring cues; it is never rewritten or lost.
- Sync, width and gap fixes were each measured before and after:
  +2.05 s → **+0.74 s** mean subtitle lag against YouTube's own grid,
  median 15 → **12** words per subtitle, blank cue transitions
  6200 → **1368**.
- Full-length check on a 291-minute capture: 0 overlaps, 0 dropped cues,
  word stream intact.

## 1.3.15 — 2026-10-01

The output panel names the requested format at the start of every
download, read out of the command that is about to run:

```
▶ Video + Audio · bestvideo*+bestaudio/best
▶ Video only · bestvideo/best
▶ Audio only · extract m4a
▶ Subtitles only · no media download
```

Reported as "the app downloaded no audio": the media type alone decides
whether an audio track is fetched, and the resulting file looks complete
either way. It was verified not to be a selection bug first — with the
app's own format string the download and merge succeed, with and without
`--js-runtimes deno`.

## 1.3.14 — 2026-10-01

Three subtitle problems found by watching real output, each measured
before it changed.

- **Sync** — subtitles ran ~2 s behind the speech, and that was our bug.
  YouTube's ASR windows *roll*: 100% of window pairs overlap, by a median
  of 1.6 s, and each window's words were being stamped across its own
  span, so timestamps ratcheted forward without bound. Measured against
  YouTube's own grid: **+2.05 s mean, up to +6.7 s** → **+0.74 s mean**,
  still non-decreasing.
- **Width** — the word ceiling did nothing, because the forced-merge rule
  overrode it. A forced merge now respects the same ceiling, set to 12
  words: median **15 → 12** words per subtitle, 36 → 31 chars per line.
- **Gaps** — 40% of subtitle transitions went blank for a median of 0.6 s.
  A cue now lingers into the pause that follows it, never more than 1.5 s
  past the time it needs to be read. Blank transitions: **6200 → 1368**.

Also: the written `.srt` ends with a newline (a parser can drop a
trailing block that has none), and a cue that still cannot be placed is
now *named* — file and count in the panel, its text in the log — rather
than silently dropped.

## 1.3.13 — 2026-10-01

**⚠ Changed on disk** — the subtitle layout stage is replaced. The three
heuristic passes (merge choppy cues → shift words across pauses → merge
again) are gone, replaced by one stage that ends cues at sentence ends.
The version bump to 1.4.0 came later, once the new output had been
checked against real downloads — see 1.4.0 above for the verified
numbers.

Also in this commit: the `📊` summary line gained the words/sec rate and
the overshoot count, so a dense video is distinguishable from a packing
failure; and the layout constants moved to the top of `subtitles.py`,
pinned by a test so retuning them has to be deliberate.

## 1.3.12 — 2026-10-01

Fixed a regression that had disabled the two-line subtitle merge for
*every* YouTube ASR video.

The multi-site work had started deciding `real` vs `auto` from the
downloaded filename alone. Current yt-dlp merges `--write-subs` and
`--write-auto-subs` into one language namespace and writes YouTube's
automatic captions as plain `<title>.en.srt` — the `a.` prefix is never
produced. Every automatic track was therefore classified "real", which
skips the merge.

Symptom on an 87-minute episode: **2965 overlapping single-line cues
instead of the 2-line layout, 0 two-line cues.** Players stack
overlapping rolling cues newest-above-previous, which reads as "the
second line appears above the first".

Tag `subtitle-layout-baseline` marks this commit as the last
known-good layout, to compare against when the refactor proceeded.

## 1.3.11 — 2026-09-26

The dock progress bar no longer jumps backwards. It filled to 100% during
the video transfer and then dropped to ~85% when the audio transfer
started.

The video transfer now fills only its share of the bar and the audio
fills the rest, so the overlay keeps rising when the second stream
starts. The reserved share is released at completion for a download that
turned out to be a single muxed stream, so it still finishes at exactly
1.0 — the final dock value is now pushed explicitly, since nothing
emitted it before.

The trade-off: a single-stream download fills 0–85% and fills the
reserved share once at the end. That is the price of a bar that never
moves backwards.

  median 15 → **12** words per subtitle, blank cue transitions
  6200 → **1368**.
- Full-length check on a 291-minute capture: 0 overlaps, 0 dropped cues,
  word stream intact.

## 1.3.10 — 2026-09-26

**⚠ Changed on disk** — a video with a single subtitle file now gets
`Title.srt` instead of `Title.en.srt`. With nothing to disambiguate, the
code repeats what the name already implies, and `Title.srt` is the name
players and editors look for.

Two or more files keep their codes — that is what tells them apart. The
rule is literal: the number of subtitle files that actually landed, not
the number of languages selected or offered. Requesting `en`+`de` on a
video that only delivers `en` ends up as `Title.srt` too.

The processed file is still always written under a name the app picks:
site-named files (`<title>.en-auto.srt`, `<title>.deu.srt`,
`<title>.a.en.srt`) are renamed to the canonical name first and then
overwritten in place, so a duplicate can never be left behind. A leftover
bare file from an earlier run is deleted once a later run produces two or
more tagged files.

## 1.3.9 — 2026-09-25

**Cancel** a running download, via a new button in the download row or
`Esc`. Closing the window cancels too.

It works by killing the yt-dlp **process group**, so an `ffmpeg` merge
child dies with it instead of writing into a torn-down directory. The
Cancel button stays clickable while the rest of the UI is disabled, and
is shown only after every early return in the download path — so it never
lingers with no download behind it.

A cancelled run reports "Cancelled" in neutral styling, not the red
error state, and partial `.part` files are intentionally left for you to
remove.

## 1.3.8 — 2026-09-25

- The UI no longer stays disabled while a slow SponsorBlock lookup is in
  flight. The lookup moved off the metadata path onto its own thread, so
  a failing API no longer holds the hand-off for ~49 s; one request on a
  4 s timeout, no retry loop.
- Subtitle state can no longer go stale. A failed info fetch (nonzero
  exit, timeout, no audio, parse error) can no longer leave the previous
  video's languages enabled and requested.
- **⚠ Changed on disk** — the output folder and the files written into
  it can no longer disagree. The `-o` template previously pointed at a
  subpath of the created folder; both now store the same sanitized
  basename. A title or channel that sanitizes to an empty string falls
  back to `downloaded_video` / `Unknown Channel` instead of collapsing
  the path to the output root.
- A missing or malformed `upload_date` no longer produces a title like
  `" - Title"` — an orphaned dash that became the folder and file name.
- Dock tile repaints are throttled (~96% fewer; each one allocates an
  `NSImage` and repaints the app icon). The 0% and 100% endpoints always
  repaint, so a download never appears to stall just short of full.

## 1.3.7 — 2026-09-25

Hardening pass over URL gating, download finalization and subtitles.

- Look-alike hosts (`evilrumble.com`, `notyoutube.com`) are rejected, and
  `/watch?list=...` is caught by the channel gate.
- The output artifact is resolved and verified before EDL/NFO/metadata
  post-processing; a download with no expected file present now **fails**
  instead of reporting success.
- Progress is marked complete only on exit code 0, and reset on failure.
- Subtitle downloads are skipped when a matching `.srt`/`.vtt` already
  exists; overlapping SRT cues are clamped, out-of-order cues dropped,
  malformed files preserved untouched, and results written atomically.
- A corrupt preferences file can no longer break startup.

## 1.3.6 — 2026-09-25

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.3.6) · tag `v1.3.6`

Tooling and correctness; the first release after 1.1.24.

- A project-tailored `.pylintrc` (pylint 10.00/10, 0 messages). Every
  disabled check carries its reason in the file, so it can be re-enabled
  selectively. The genuine findings it surfaced were fixed: dead code in
  the info-fetch path, an unread `stderr`, a `sb_bar` signal connected
  through a deferred lookup (which broke startup).
- 202 unit tests, up from 35.

## 1.3.5 — 2026-09-25

The log viewer gained a **Clear** action, with a dialog-local `Ctrl`/`Cmd+K`
shortcut. It deletes the actual `app.log` on disk, not just the view, after
a confirmation prompt that shows the full path. It is disabled when the log
is missing or empty — and the shortcut cannot bypass that.

The truncation goes through the logging handler's own stream while holding
its lock, so the stream offset stays consistent in any file mode and the
truncation cannot interleave with an in-flight write from the download
thread.

## 1.3.4 — 2026-09-25

**Fixed** — the thumbnail dialog stopped opening in 1.3.3. The fetch
worker handed the bytes back with `QTimer.singleShot(0, ...)`, which Qt 6
silently drops because a plain worker thread has no event loop — no
warning, no exception, and the dialog simply never appeared. It now
returns through a queued signal.

Also:

- Per-channel episode-number extraction (PowerfulJRE, Shawn Ryan Show,
  Lex Fridman, PBD Podcast) moved into a pure, tested rule table — adding
  a channel is now a one-line entry.
- A missing stylesheet is now reported in the output panel, not only in
  the log, and the log viewer reports an unreadable file instead of
  failing.
- Log records are written as UTF-8, so non-ASCII channel names match what
  the dialog reads.

## 1.3.3 — 2026-09-25

Code-review cleanup. No intended behaviour change — but it introduced the
thumbnail regression that 1.3.4 fixed.

## 1.3.2 — 2026-09-24

**⚠ Changed on disk — fixed silent video corruption.**

The ffmpeg bitstream filter that strips embedded broadcast captions
(`filter_units=remove_types=6`) is **H.264-specific**: unit type 6 is an
SEI NAL there, but a Frame OBU in AV1. Applied unconditionally, it silently
deleted picture data from AV1 downloads — which matters because YouTube's
`best` format commonly picks AV1.

The filter is now gated behind a per-site flag, enabled only for Rumble,
the only site serving US broadcast feeds with EIA-608 captions.

## 1.3.1 — 2026-09-23

Version bump only.

## 1.3.0 — 2026-09-23

**ARD Mediathek** and **ZDF Mediathek** join the supported sites.

- Channel and show pages are rejected per site, mirroring yt-dlp's own
  extractors — including ZDF's catch-all channel playlist, where only
  `/video/`, `/play/` and legacy single-video paths pass. A ZDF show page
  that resolves to a 30-entry playlist is refused.
- Both key their captions by ISO 639-2 (`deu`); that is mapped onto the
  German checkbox in the UI and onto `<title>.de.srt` on disk.
- **Note:** a browser reporting a video as "not available" (geo or rights)
  is a separate client-side check and does not predict yt-dlp. Observed
  live: a video the browser called unavailable downloaded fine.

## 1.2.1 — 2026-09-23

**Odysee** (odysee.com / lbry.tv) joins the supported sites.

- Odysee has **no subtitle tracks at all**, so the info panel prints
  "Subtitles: Not available on Odysee" instead of an empty language list.
- Every Odysee format is muxed, so audio-only extraction is forced —
  otherwise "Best" audio would hand you the source MP4.
- The info-fetch budget is 90 s for this site, not the default 15 s: the
  LBRY `resolve` call consistently takes ~40 s and short claim ids are
  slower. Long fetches report progress every 20 s rather than looking hung.

- Look-alike hosts (`evilrumble.com`, `notyoutube.com`) are rejected, and
  `/watch?list=...` is caught by the channel gate.
- The output artifact is resolved and verified before EDL/NFO/metadata
  post-processing; a download with no expected file present now **fails**
  instead of reporting success.
- Progress is marked complete only on exit code 0, and reset on failure.
- Subtitle downloads are skipped when a matching `.srt`/`.vtt` already
  exists; overlapping SRT cues are clamped, out-of-order cues dropped,
  malformed files preserved untouched, and results written atomically.
- A corrupt preferences file can no longer break startup.

## 1.2.0 — 2026-09-23

Internal refactor: the 4.9k-line `app.py` became the `ytdl/` package
(`app.py` assembling per-concern mixins, with `info_fetch`, `download`,
`subtitles`, `ui_build`, `preferences_dialog`, `sites`, `progress`,
`utils` and others holding the logic), plus a thin `main.py` launcher at
the repo root. No behaviour change.

## 1.1.26 — 2026-09-23

**Rumble** joins the supported sites, and per-site behaviour profiles are
introduced as the mechanism for it. Unknown domains keep the historical
YouTube behaviour.

- Rumble's `en-auto` subtitle keys are requested and mapped back to the
  canonical name; its subtitles arrive pre-formatted and are kept as-is.
- Embedded EIA-608 captions are stripped losslessly, so IINA does not
  surface them as a phantom subtitle track.
- A URL plausibility gate was added, with a well-formed-hostname check, so
  clipboard garbage (e.g. the version string copied from the info panel)
  never reaches yt-dlp or the URL field.

*Note: 1.1.25 was skipped; no code ever carried that version.*

## 1.1.24 — 2026-09-15

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.1.24) · tag `v1.1.24`

When fetching video info fails, yt-dlp's captured stdout/stderr is now
dumped to the output panel with the exit code in the headline, and a
timeout dumps whatever was captured before the kill. Previously stderr was
swallowed, so HTTP 403 and sign-in-wall errors made the app look like it
had silently stopped. Also fixed a `NameError` in the no-audio-formats
branch.

## 1.1.23 — 2026-09-01

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.1.23) · tag `v1.1.23`

Fixed the progress bars on retried downloads. After a mid-download error
(`Got error ... Retrying (n/10)`), yt-dlp re-prints the `[download]
Destination:` of the **same** file, which previously advanced the stream
accounting video → audio and filled the audio bar with video progress.
Destination re-prints are now deduped by normalized path, so only a
genuinely new destination switches the stream.

## 1.1.22 — 2026-08-31

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.1.22) · tag `v1.1.22`

After an info fetch, languages with `(real)` subtitles are checked
automatically; if none exist, `(auto)` captions are checked instead. This
applies in all download modes, and stale checks from a previous video are
cleared first.

## 1.1.21 — 2026-08-31

[Release](https://github.com/sascha2115/YT-DLP-Downloader/releases/tag/v1.1.21) · tag `v1.1.21`

First cross-platform release.

- **Linux support**: optional AppKit import so the macOS dock integration
  degrades cleanly, XDG config and log paths, and `build-linux.sh`.
- Icons and stylesheet moved into `assets/`; `build.sh` renamed to
  `build-macos.sh` for platform symmetry; window icon added.
- The build scripts locate `pyinstaller` themselves (venv, `~/.local/bin`,
  `PATH`) instead of assuming it is on `PATH`.
- Stable app identity on Linux, so the launcher/taskbar association
  survives.
- Fixed a duplicate `Ctrl+,` binding — Qt treats a key bound to both a
  `QShortcut` and a menu action as ambiguous and fires **neither**, so the
  Preferences shortcut silently did nothing.
- `LICENSE` and a legal note covering content types and terms of service.

## 1.1.20 — 2026-08-31

Initial public import. Single-site YouTube downloader: quality selection,
subtitle handling with resync/merge, SponsorBlock, NFO and EDL output,
preferences, and the macOS dock progress overlay.

---

## Reading this against your files

Three releases change what lands on disk, and only one of them announces
itself:

- **1.3.10** — a single subtitle is written as `Title.srt`, not
  `Title.en.srt`.
- **1.3.13 / 1.4.0** — subtitle layout is rewritten. Existing `.srt`
  files are **not** re-laid out, so an old file and a new one downloaded
  from the same app will legitimately differ.
- **1.4.0** — SponsorBlock subtitles are no longer retimed into an edited
  timeline. In practice a no-op (the code was already dead), but it is
  recorded here so the behaviour is not mistaken for a regression.

**SponsorBlock marks, it never cuts.** `--sponsorblock-mark all` has been
in place since the first public release; the media is untouched and an
`.edl` is written for the player to jump. Cutting was abandoned because
SponsorBlock boundaries fall mid-GOP: a clean cut means snapping to the
nearest keyframe, so the cut is not where it was asked to be — or
re-encoding the whole file. Letting the player jump has neither
constraint.
