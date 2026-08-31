# Project Instructions

## How to Investigate
- `app.py` – the single main entrypoint (PyQt6 GUI); it lives at the **repo root**, not in an `app/` directory.
- `requirements.txt` – Python dependencies (PyQt6, requests, pyobjc-framework-Cocoa). The external binaries (`yt-dlp`, `ffmpeg`, `deno`) are only listed as comments – install them via Homebrew or pip, not via this file.
- No CI workflow exists (no `.github/`, no `ci.yml` / `pre-commit.yml`). Build and test are manual, see below.
- Runtime preferences are NOT the repo's `preferences.json` – the app reads and writes `~/Library/Application Support/YT-DLP Downloader/preferences.json` (see `PREFERENCES_DIR` in `app.py`, edited via the in-app dialog). The repo-root `preferences.json` is only a reference sample (`channel_name_map`).

## Repository layout (flat at the root, plus `assets/`)
- `app.py` – main entrypoint; run from the repo root with `python3 app.py`.
- `app-120-copy.py` – frozen copy of the current version (snapshot convention, do not edit). Local only — not in the published repo.
- `archive/` – legacy versioned copies (`app-100-copy.py` … `app-119-findbinary.py`); no edits. Local only — not in the published repo.
- `temp/` – isolated test scripts and scratch files.
- `build.sh` – packaging one-liner (PyInstaller windowed `.app` bundle).
- `build-linux.sh` – packaging one-liner for Linux (run on a Linux machine; PyInstaller cannot cross-compile).
- `setup.md` – complete from-source install guide for macOS and Linux (system packages, external binaries, venv, packaging, troubleshooting).
- `YT-DLP Downloader.spec`, `build/`, `dist/` – PyInstaller spec and build outputs.
- `assets/` – icons (`AppIcon.icns`, `AppIcon.png`) and stylesheet (`styles.qss`); `styles.qss` is loaded via `resource_path()`, which resolves relative to the script in source runs and to the bundle in PyInstaller builds.
- `scratch/` – scratch space.

## Execution scaffold
- Install dependencies: `pip install -r requirements.txt`.
- Run GUI: `python3 app.py` (cwd-independent; `resource_path()` uses `sys._MEIPASS` or `__file__`).
- Test flag: `python3 app.py --simulate-download-error` (makes downloads raise immediately).
- External binaries `yt-dlp`, `ffmpeg`, `deno` are typically installed via Homebrew (not pip); `find_binary()` also detects a pip-installed copy in the launching Python's bin.
- Packaging: `./build.sh` → `dist/YT-DLP Downloader.app`; a bare single-file binary via `pyinstaller --onefile app.py` → `dist/app`.

## Execution flow
- GUI triggers `fetch_video_info` → `start_download`.
- Progress reported via `DownloadProgressManager` and `SignalEmitter` signals.

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
- Languages whose info fetch reports "(none)" availability get their checkbox disabled and unchecked in `_update_subtitle_checkboxes()`; the set `subtitle_unavailable` makes `_set_ui_enabled_state()` keep them disabled across UI re-enables (fetch/download start/end).

## Common gotchas
- GUI updates must use signals, not direct widget modifications.
- Avoid relative paths when invoking external binaries; rely on `find_binary()` (wraps `shutil.which()` and adds macOS/pip fallbacks).
- Do not modify `archive/` files or `app-120-copy.py` – they are frozen snapshots.
