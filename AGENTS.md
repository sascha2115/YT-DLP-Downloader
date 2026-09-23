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
- `ytdl/info_fetch.py` – `InfoFetchMixin`: video-info fetch (yt-dlp subprocess), parsing, description summary, SponsorBlock API.
- `ytdl/download.py` – `DownloadMixin`: download orchestration (start/build command/run), yt-dlp output/progress parsing, file metadata, NFO/EDL creation.
- `ytdl/subtitles.py` – `SubtitleMixin`: subtitle selection/file mapping and the resync/2-line-merge pipeline.
- `ytdl/ui_build.py` – `UiBuildMixin`: `init_ui` widget construction, styling, menus, dialogs, UI enable state.
- `ytdl/preferences_dialog.py` – `PreferencesDialogMixin`: the preferences editor dialog.
- `ytdl/sites.py` – site profiles (`SUPPORTED_SITES`, `DEFAULT_SITE`, `SUPPORTED_SITES_LABEL`), ID regexes, `detect_site()`, `site_*()` profile helpers, `is_plausible_url()`, `normalize_url()`.
- `ytdl/progress.py` – yt-dlp output parsing tables (`RE_*`, `SUBTITLE_EXTENSIONS`) and `DownloadProgressManager`.
- `ytdl/widgets.py` – small Qt support classes (`SignalEmitter`, `SponsorBlockBar`, `BusySpinner`, `CustomTextEdit`) and the `SB_*` category constants.
- `ytdl/description.py` – description/plot cleaning heuristics (`clean_youtube_description` and friends).
- `ytdl/utils.py` – `resource_path()` (dev branch resolves the repo ROOT, one level above the package), `get_log_dir()`, `find_binary()`, `sanitize_title()`, duration/filesize/SRT-time formatters.
- `archive/` – frozen legacy snapshots of past versions; local only, not part of the published repo; do not edit.
- `temp/` – isolated test scripts and scratch files.
- `build-macos.sh` – macOS packaging one-liner (PyInstaller windowed `.app` bundle).
- `build-linux.sh` – packaging one-liner for Linux (run on a Linux machine; PyInstaller cannot cross-compile).
- `setup.md` – complete from-source install guide for macOS and Linux (system packages, external binaries, venv, packaging, troubleshooting).
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
- Info-fetch failures are surfaced in the output panel: `get_video_info()` dumps yt-dlp's captured stdout/stderr via `_dump_ytdlp_error_output()` (headline includes the exit code; a timeout dumps the partial output captured before the kill). Previously stderr was swallowed, so 403/sign-in errors made the app look like it silently stopped.
- Progress reported via `DownloadProgressManager` and `SignalEmitter` signals.

## Multi-site support (YouTube + Rumble proof-of-concept)
- `SUPPORTED_SITES` (top of `main.py`) holds per-site profiles: `domains`, `id_regex`, `sponsorblock`, `js_runtime`, `resync_auto_subs`. `detect_site()` matches the hostname; unknown domains fall back to `DEFAULT_SITE` (YouTube). New profile flags must default to the historical behavior for unknown sites.
- Site-gated behaviors: SponsorBlock API query + `--sponsorblock-mark` (YouTube-only), `--js-runtimes deno` (needed by YouTube's JS sig/nsig challenges, skipped for Rumble), auto-subtitle resync/2-line merge (needed for YouTube's choppy ASR cues; Rumble subs arrive pre-formatted and are kept as-is), video format selector uses `bestvideo*` (not strict `bestvideo`) so muxed HLS formats with unknown codecs (Rumble) are eligible – strict `bestvideo` would degrade Rumble downloads to the tiny video-only timeline strip.
- Embedded broadcast captions (EIA-608 in H.264 SEI T.35 NALs, carried by Rumble's HLS streams; IINA surfaces them as a hidden "eia_608" subtitle track) are always stripped losslessly via `--postprocessor-args Merger/FixupM3u8:-bsf:v filter_units=remove_types=6` in `build_command()`. The app's own SRT files are the intended subtitles.
- Subtitle key shapes differ per site: yt-dlp matches `--sub-langs` entries as regexes with `fullmatch` against the site's subtitle keys (YouTube `en`/`a.en`, Rumble `en-auto` with generated subs in `subtitles`, not `automatic_captions`). `_subtitle_lang_patterns()` therefore requests every known key shape, and `_find_downloaded_subtitles()` + `_normalize_subtitle_names()` map site-named files (`<base>.en-auto.srt`) back to the canonical `<base>.en.srt` **before** post-processing, so resync overwrites in place instead of leaving duplicates.

## SponsorBlock handling
- Visual bar defined in `SponsorBlockBar`.
- Category colors in `SB_CATEGORY_COLORS`, mapping in `SB_API_MAP`.
- Use `self.sb_bar.set_segments(segments, duration)` to update.

## Testing notes
- Unit tests (no Qt event loop needed): `python3 -m unittest temp.test_subtitle_progress -v` from the repo root (also works from `temp/`). They replay real captured yt-dlp output through the real parser methods (`_parse_download_output`, `_update_download_progress`, `_is_subtitle_path`) bound to a lightweight harness.
- `temp/replay_ytdlp_output.py <captured.log> <media_type>` – debug helper: replays a captured yt-dlp log through the parser and prints video/audio bar + dock state per line.
- `temp/test_dock_progress.py` is NOT a unittest – it is a manual AppKit dock-tile demo script.
- No global test suite, no coverage tooling, no CI.

## Subtitle & progress parsing
- yt-dlp downloads subtitles BEFORE the media streams; their `[download] Destination:` and percent lines must not affect the video/audio progress bars or the captured media filename.
- Detection lives in `SUBTITLE_EXTENSIONS` + `YTDLPDownloaderGUI._is_subtitle_path()`; exclusion logic in `_parse_download_output()` / `_update_download_progress()` (state key `downloading_subtitles`).
- Retry re-prints: after a mid-download error ("Got error ... Retrying (n/10)...") yt-dlp RE-PRINTS the `[download] Destination:` of the SAME file. `DownloadProgressManager.on_download_destination(dest)` therefore dedupes by normalized destination path (`last_destination`, strips quotes/`.part`/`.ytdl`/`.temp`) so a retry does not advance the stream accounting video → audio. A captured example lives in `temp/retry-timeout-capture.log` (replay: `python3 temp/replay_ytdlp_output.py temp/retry-timeout-capture.log video`).
- Languages whose info fetch reports "(none)" availability get their checkbox disabled and unchecked in `_update_subtitle_checkboxes()`; the set `subtitle_unavailable` makes `_set_ui_enabled_state()` keep them disabled across UI re-enables (fetch/download start/end). After an info fetch the language checkboxes stay unchecked — selection is manual; `_update_subtitle_checkboxes()` only refreshes labels/availability, and a language the user checked survives a fetch unless it became "(none)". The single exception is "Subtitles only" mode (which downloads nothing else): there the selection is auto-filled via `_auto_select_subtitles()` — "(real)" languages are checked, "(auto)" as fallback — both when the mode's radio button is selected and after a fetch in that mode.

## Common gotchas
- GUI updates must use signals, not direct widget modifications.
- Never bind the same key sequence to both a `QShortcut` and a menu `QAction` — Qt treats the duplicate as ambiguous and fires **neither** (silent failure). One binding per key; menu `QAction`s already work window-globally. Observed with `Ctrl+,`/Preferences (fixed).
- Avoid relative paths when invoking external binaries; rely on `find_binary()` (wraps `shutil.which()` and adds macOS/pip fallbacks).
- Do not modify `archive/` files – they are frozen snapshots.
