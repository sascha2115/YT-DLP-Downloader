# Project Instructions

## Conventions
- `APP_VERSION` in `ytdl/__init__.py` is bumped **only at an explicit git commit** – not per working-tree change. Between commits, leave the version at the value it had at the last commit (or the pre-existing uncommitted value).

## How to Investigate
- `main.py` – the thin launcher at the **repo root**: parses argv, starts the app, and re-exports the public names of `ytdl.app` so `main.detect_site` / `main.YTDLPDownloaderGUI` etc. remain the single import surface for tests and tooling.
- `ytdl/` – the application package: GUI class in `ytdl/app.py`, pure logic in the sibling modules (see Repository layout).
- `requirements.txt` – Python dependencies (PyQt6, requests, pyobjc-framework-Cocoa). The external binaries (`yt-dlp`, `ffmpeg`, `deno`) are only listed as comments – install them via Homebrew or pip, not via this file.
- No CI workflow exists (no `.github/`, no `ci.yml` / `pre-commit.yml`). Build and test are manual, see below.
- Runtime preferences are NOT the repo's `preferences.json` – the app reads and writes `~/Library/Application Support/YT-DLP Downloader/preferences.json` (see `PREFERENCES_DIR` in `ytdl/app.py`, edited via the in-app dialog). The repo-root `preferences.json` is only a reference sample (`channel_name_map`).

## Repository layout (flat at the root, plus `assets/` and the `ytdl/` package)
- `main.py` – thin launcher + facade; run from the repo root with `python3 main.py` (works from any cwd).
- `ytdl/app.py` – class assembly (`YTDLPDownloaderGUI` inherits the per-concern mixins), `__init__` shared-state init, URL/clipboard handlers, output-panel helpers, dock methods, `APP_VERSION` re-export.
- `ytdl/config.py` – app-wide constants (`DEFAULT_OUTPUT_DIR`, `TITLE_FETCH_DELAY_MS`).
- `ytdl/preferences.py` – preferences file path/loading and in-memory state (`preferences`, `CHANNEL_NAME_MAP`); mutate via `apply_preferences()` and read live values through the module.
- `ytdl/info_fetch.py` – `InfoFetchMixin`: video-info fetch (yt-dlp subprocess), parsing, description summary, SponsorBlock API. Also exports `apply_episode_rules(channel, title) -> (episode_code, clean_title)` and `_EPISODE_RULES` — a pure, Qt-free function that handles per-channel episode-number extraction (PowerfulJRE, Shawn Ryan Show, Lex Fridman, PBD Podcast); adding a new channel is a one-line table entry.
- `ytdl/download.py` – `DownloadMixin`: download orchestration (start/build command/run), yt-dlp output/progress parsing, file metadata, NFO/EDL creation.
- `ytdl/subtitles.py` – `SubtitleMixin`: subtitle selection/file mapping and the resync/2-line-merge pipeline.
- `ytdl/ui_build.py` – `UiBuildMixin`: `init_ui` widget construction, styling, menus, dialogs, UI enable state.
- `ytdl/preferences_dialog.py` – `PreferencesDialogMixin`: the preferences editor dialog.
- `ytdl/sites.py` – site profiles (`SUPPORTED_SITES`, `DEFAULT_SITE`, `SUPPORTED_SITES_LABEL`), ID regexes, `detect_site()`, `is_known_site()` (strict domain gate), `is_channel_url()` (channel/playlist gate), `site_*()` profile helpers, `is_plausible_url()`, `normalize_url()`.
- `ytdl/progress.py` – yt-dlp output parsing tables (`RE_*`, `SUBTITLE_EXTENSIONS`) and `DownloadProgressManager`.
- `ytdl/widgets.py` – small Qt support classes (`SignalEmitter`, `SponsorBlockBar`, `BusySpinner`, `CustomTextEdit`) and the `SB_*` category constants.
- `ytdl/description.py` – description/plot cleaning heuristics (`clean_youtube_description` and friends).
- `ytdl/utils.py` – `resource_path()` (dev branch resolves the repo ROOT, one level above the package), `get_log_dir()`, `find_binary()`, `sanitize_title()`, `canonical_subtitle_lang()` / `SUBTITLE_LANG_ALIASES` (site subtitle key → UI code, e.g. `deu`→`de`), duration/filesize/SRT-time formatters.
- `archive/` – frozen legacy snapshots of past versions; local only, not part of the published repo; do not edit.
- `temp/` – isolated test scripts and scratch files.
- `build-macos.sh` – macOS packaging one-liner (PyInstaller windowed `.app` bundle).
- `build-linux.sh` – packaging one-liner for Linux (run on a Linux machine; PyInstaller cannot cross-compile).
- `setup.md` – complete from-source install guide for macOS and Linux (system packages, external binaries, venv, packaging, troubleshooting).
- `TODO.md` – planned future work: sites queued for support (video platforms, social media) and how to add them.
- `YT-DLP Downloader.spec`, `build/`, `dist/` – PyInstaller spec and build outputs (spec entry stays `main.py`; `Analysis` follows the `ytdl.*` imports).
- `assets/` – icons (`AppIcon.icns`, `AppIcon.png`) and stylesheet (`styles.qss`); `styles.qss` is loaded via `resource_path()`, which resolves relative to the repo root in source runs and to the bundle in PyInstaller builds.
- `scratch/` – scratch space.

## Execution scaffold
- Install dependencies: `pip install -r requirements.txt`.
- Run GUI: `python3 main.py` (cwd-independent; `resource_path()` uses `sys._MEIPASS` or `__file__`).
- Test flag: `python3 main.py --simulate-download-error` (makes downloads raise immediately).
- External binaries `yt-dlp`, `ffmpeg`, `deno` are typically installed via Homebrew (not pip); `find_binary()` also detects a pip-installed copy in the launching Python's bin.
- Packaging: `./build-macos.sh` → `dist/YT-DLP Downloader.app`; a bare single-file binary via `pyinstaller --onefile main.py` → `dist/app`.

## Execution flow
- GUI triggers `fetch_video_info` → `start_download`.
- `fetch_video_info()` pre-checks the URL syntactically (`is_plausible_url()`, also used by the clipboard paste/startup handler) so garbage like "nonsense" never reaches the yt-dlp subprocess; naked 11-char YouTube IDs and schemeless known-site tokens still pass because `normalize_url()` canonicalizes them first. The hostname must be well-formed (dot-separated labels of letters/digits/hyphens) — Python's `urlparse` is lenient and would otherwise accept clipboard text like "YT-DLP Downloader 1.1.25" as a "URL".
- The domain gate is strict: `is_supported_url()` = `is_known_site()` requires the hostname to match a `SUPPORTED_SITES` profile, so unsupported sites (e.g. google.com, vimeo.com) are rejected with "Unsupported site — supported: …" instead of being handed to yt-dlp's generic extractor. `detect_site()` keeps its YouTube fallback for behavior flags; to accept another site, add a profile.
- Info-fetch failures are surfaced in the output panel: `get_video_info()` dumps yt-dlp's captured stdout/stderr via `_dump_ytdlp_error_output()` (headline includes the exit code; a timeout dumps the partial output captured before the kill). Previously stderr was swallowed, so 403/sign-in errors made the app look like it silently stopped.
- Progress reported via `DownloadProgressManager` and `SignalEmitter` signals.

## Multi-site support (YouTube + Rumble + Odysee + ARD Mediathek + ZDF Mediathek)
- `SUPPORTED_SITES` (top of `ytdl/sites.py`) holds per-site profiles: `domains`, `id_regex`, `sponsorblock`, `js_runtime`, `resync_auto_subs`, `supports_subtitles`, `always_extract_audio`, `channel_url_regex`, `info_timeout`. `detect_site()` matches the hostname; unknown domains fall back to `DEFAULT_SITE` (YouTube) for behavior flags, while `is_known_site()` is the strict domain gate (unknown domains are rejected before yt-dlp runs) and `is_channel_url()` rejects channel/playlist pages per site. New profile flags must default to the historical behavior for unknown sites.
- Channel/playlist patterns mirror yt-dlp's own extractors: ARD rejects the `sendung`/`serie`/`sammlung` collections (`ARDMediathekCollectionIE`), while ZDF uses an inverse heuristic because `ZDFChannelIE` is a catch-all playlist for every `zdf.de` path — only `/video/`, `/play/` and legacy `<slug>.html` single videos pass (path-anchored in the profile), so a ZDF show page like `/magazine/heute-journal-104` (resolves to a 30-entry playlist) is rejected.
- Website availability ≠ yt-dlp availability: the ARD/ZDF web player's "not available" (geo/rights) is a separate client-side check — yt-dlp only fails when the player API returns **no** formats, and ARD's extractor then raises (`_geoblocked` → "This video is not available due to geoblocking"; `"fsk"` in the page → "only available after 20:00"). Observed live: a video the browser reports as unavailable on ARD downloaded fine through the app; when extraction genuinely fails, the reason lands in the output-panel dump.
- Input gating lives in one place: `YTDLPDownloaderGUI.url_rejection_reason()` (plausible URL → known site → not a channel URL); `fetch_video_info()` and the clipboard paste path both use it.
- Info-fetch budget is per site: `site_info_timeout()` returns the profile's `info_timeout` or `config.INFO_FETCH_TIMEOUT_SECONDS` (15s). Odysee sets 90s because the LBRY API `resolve` call routinely takes ~40s (measured; short claim ids like `:d` are slow) and the old hardcoded 15s killed it mid-resolve.
- Long info fetches report progress: `get_video_info()` runs through `_run_info_command()`, which starts a daemon monitor (`_info_wait_monitor`) that emits "⏳ Still fetching video info (Ns) — <hint>…" after `config.INFO_FETCH_HINT_AFTER_SECONDS` (10s) and every `INFO_FETCH_HINT_EVERY_SECONDS` (20s) while yt-dlp is still resolving. The hint text is per site (`slow_hint`, e.g. Odysee's LBRY API); sites without one get "the site is responding slowly".
- Site-gated behaviors: SponsorBlock API query + `--sponsorblock-mark` (YouTube-only), `--js-runtimes deno` (needed by YouTube's JS sig/nsig challenges, skipped for Rumble/Odysee/ARD/ZDF), auto-subtitle resync/2-line merge (needed for YouTube's choppy ASR cues; Rumble subs arrive pre-formatted and are kept as-is; Odysee has no subs at all), video format selector uses `bestvideo*` (not strict `bestvideo`) so muxed HLS formats with unknown codecs (Rumble, Odysee) are eligible – strict `bestvideo` would degrade Rumble downloads to the tiny video-only timeline strip. `always_extract_audio` forces `-x` for sites without audio-only streams (Odysee and ZDF — every ZDF format is muxed), otherwise "Best" audio would save the muxed source video file.
- Sites without subtitle support (`supports_subtitles: False`, e.g. Odysee — the LBRY extractor exposes no tracks) print "Subtitles: Not available on <label>" instead of the generic "(none)" line during info fetch.
- Embedded broadcast captions (EIA-608 in H.264 SEI T.35 NALs, carried by Rumble's HLS streams; IINA surfaces them as a hidden "eia_608" subtitle track) are stripped **only for sites whose profile sets `strip_embedded_cc` (Rumble alone)** via `--postprocessor-args Merger/FixupM3u8:-bsf:v filter_units=remove_types=6` in `build_command()`. The filter is site-gated because unit type numbering is codec-specific: in AV1 an OBU of type 6 is a Frame OBU, so applying the filter unconditionally deleted picture data and corrupted AV1 downloads (observed on YouTube, where `best` picks AV1). The app's own SRT files are the intended subtitles.
- Subtitle key shapes differ per site: yt-dlp matches `--sub-langs` entries as regexes with `fullmatch` against the site's subtitle keys (YouTube `en`/`a.en`, Rumble `en-auto` with generated subs in `subtitles`, not `automatic_captions`). `_subtitle_lang_patterns()` therefore requests every known key shape, and `_find_downloaded_subtitles()` + `_normalize_subtitle_names()` map site-named files (`<base>.en-auto.srt`) back to the canonical `<base>.en.srt` **before** post-processing, so resync overwrites in place instead of leaving duplicates. German public-media sites (ARD/ZDF) key their tracks by ISO 639-2 (`deu`); `canonical_subtitle_lang()` (`ytdl/utils.py`) maps those keys onto the UI's 639-1 codes at all three touch points (the availability match in `info_fetch.py`, the `--sub-langs` patterns and downloaded-file matching), so the German checkbox shows availability and files end up renamed `<base>.de.srt`.

## SponsorBlock handling
- Visual bar defined in `SponsorBlockBar`.
- Category colors in `SB_CATEGORY_COLORS`, mapping in `SB_API_MAP`.
- Use `self.sb_bar.set_segments(segments, duration)` to update.

## Testing notes
- Unit tests (no Qt event loop needed): `python3 -m unittest temp.test_subtitle_progress -v` from the repo root (also works from `temp/`). They replay real captured yt-dlp output through the real parser methods (`_parse_download_output`, `_update_download_progress`, `_is_subtitle_path`) bound to a lightweight harness.
- Suite overview: `temp/test_subtitle_progress.py` (progress parser + UI enable state), `temp/test_multisite_url.py` (site profiles, URL gate, command construction), `temp/test_subtitle_multisite.py` (subtitle key shapes/normalization), `temp/test_rumble_info_parse.py`, `temp/test_odysee_info_parse.py`, `temp/test_ard_info_parse.py` and `temp/test_zdf_info_parse.py` (info parsing from captured `yt-dlp -J` fixtures: `temp/rumble-info-capture.json`, `temp/odysee-info-capture.json`, `temp/ard-info-capture.json`, `temp/zdf-info-capture.json`), `temp/test_episode_rules.py` (channel episode-number extraction rules — pure function, no Qt needed), `temp/test_log_clear.py` (log-viewer Clear action: log-file truncation keeps the logging FileHandler in sync — pure helper, no Qt).
- `temp/replay_ytdlp_output.py <captured.log> <media_type>` – debug helper: replays a captured yt-dlp log through the parser and prints video/audio bar + dock state per line.
- `temp/replay_rumble_info.py` / `temp/replay_rumble_subtitles.py` / `temp/replay_url_gate.py` / `temp/replay_odysee_info.py` – replay helpers for the info panel, the subtitle post-processing chain, the URL gate and a real (unmocked) Odysee info fetch.
- `temp/smoke_gui.py` – headless full-window construction smoke test (run after structural refactors).
- `temp/analyze_app_split.py` / `temp/analyze_odysee.py` – analysis helpers (method inventory; summarize a captured info JSON).
- `temp/test_dock_progress.py` is NOT a unittest – it is a manual AppKit dock-tile demo script.
- No global test suite, no coverage tooling, no CI.
- **Coverage gaps (UI layer):** `open_thumbnail_dialog` (threaded fetch → queued signal → Qt dialog) and `open_log_dialog` (tail logic, HTML coloring; only its Qt-free `_truncate_log_file()` helper is covered, by `temp/test_log_clear.py`) have no automated tests. Both require a Qt event loop or mock framework to test meaningfully — shallow tests would not have caught the `QTimer.singleShot`-from-worker-thread regression that silently dropped the dialog in Qt 6. The correct pattern for new GUI logic is to extract the pure part (see `apply_episode_rules()` in `info_fetch.py` as a model) and test that independently.

## Subtitle & progress parsing
- yt-dlp downloads subtitles BEFORE the media streams; their `[download] Destination:` and percent lines must not affect the video/audio progress bars or the captured media filename.
- Detection lives in `SUBTITLE_EXTENSIONS` + `YTDLPDownloaderGUI._is_subtitle_path()`; exclusion logic in `_parse_download_output()` / `_update_download_progress()` (state key `downloading_subtitles`).
- Retry re-prints: after a mid-download error ("Got error ... Retrying (n/10)...") yt-dlp RE-PRINTS the `[download] Destination:` of the SAME file. `DownloadProgressManager.on_download_destination(dest)` therefore dedupes by normalized destination path (`last_destination`, strips quotes/`.part`/`.ytdl`/`.temp`) so a retry does not advance the stream accounting video → audio. A captured example lives in `temp/retry-timeout-capture.log` (replay: `python3 temp/replay_ytdlp_output.py temp/retry-timeout-capture.log video`).
- Languages whose info fetch reports "(none)" availability get their checkbox disabled and unchecked in `_update_subtitle_checkboxes()`; the set `subtitle_unavailable` makes `_set_ui_enabled_state()` keep them disabled across UI re-enables (fetch/download start/end). After an info fetch the language checkboxes stay unchecked — selection is manual; `_update_subtitle_checkboxes()` only refreshes labels/availability, and a language the user checked survives a fetch unless it became "(none)". The single exception is "Subtitles only" mode (which downloads nothing else): there the selection is auto-filled via `_auto_select_subtitles()` — "(real)" languages are checked, "(auto)" as fallback — both when the mode's radio button is selected and after a fetch in that mode.

## Common gotchas
- GUI updates must use signals, not direct widget modifications.
- Signals emitted from **worker threads** are queued to the main thread and only delivered while the Qt event loop is running. Headless test harnesses (and replay scripts) have no event loop, so cross-thread emissions are silently dropped: call the emitting code from the main thread, or drive the helper directly (see `TestOdyseeInfoParse.test_wait_hints_emitted_while_waiting`, which runs `_info_wait_monitor()` while a timer thread sets its stop event).
- **Never call `QTimer.singleShot` from a plain `threading.Thread`** — a plain thread has no Qt event loop, so the timer is silently dropped by Qt 6 with no warning or exception. The correct pattern for handing work back to the main thread from a worker is to emit a queued signal (`pyqtSignal`); Qt delivers it to the main-thread slot automatically. `open_thumbnail_dialog` uses this pattern: the worker thread emits `signals.thumbnail_ready` (bytes), and the main-thread slot `_show_thumbnail_dialog` builds the dialog. The `QTimer.singleShot(0, scroll_to_bottom)` in `open_log_dialog` is fine — it is called from the main thread.
- Never bind the same key sequence to both a `QShortcut` and a menu `QAction` — Qt treats the duplicate as ambiguous and fires **neither** (silent failure). One binding per key; menu `QAction`s already work window-globally. Observed with `Ctrl+,`/Preferences (fixed).
- Avoid relative paths when invoking external binaries; rely on `find_binary()` (wraps `shutil.which()` and adds macOS/pip fallbacks).
- Do not modify `archive/` files – they are frozen snapshots.
