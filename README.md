# YT‑DLP Downloader

A lightweight PyQt6 GUI for downloading videos via `yt‑dlp`.  It supports advanced options such as quality selection, subtitle handling, and SponsorBlock integration.

Supported sites (via yt-dlp's extractors): **YouTube**, **Rumble**, **Odysee** (odysee.com / lbry.tv), **ARD Mediathek** (ardmediathek.de) and **ZDF Mediathek** (zdf.de / zdfheute.de / logo.de).
SponsorBlock is YouTube-only and is skipped automatically for other sites; Odysee has no subtitle tracks (the app says so instead of offering empty language checkboxes), while the ARD/ZDF German captions (ISO key `deu`) are mapped onto the German checkbox. Channel/playlist URLs are rejected — paste a link to a single video.

## Features
- **Video / Audio / Subtitles** selection — a video's only subtitle is saved as
  `<title>.srt`; pick several languages and each keeps its code (`<title>.en.srt`,
  `<title>.de.srt`)
- **SponsorBlock** segment highlighting inside a custom bar
- **Cancel** a running download from the button or `Esc`; closing the window cancels too
- Progress shown in the dock icon and the main window (dock icon on macOS only)
- Simple preferences file (macOS: `~/Library/Application Support/YT‑DLP Downloader`,
  Linux: `~/.config/YT‑DLP Downloader`) — channel name mapping and
  subtitle pace (`subtitle_words_per_cue`, 6–16, default 8; retained but
  not currently in use — subtitles follow the captions' own lines)
- Built‑in packaging instructions using `pyinstaller`

## Requirements

- **Python 3.10+** (3.11+ recommended)
- **Python packages** — `pip install -r requirements.txt`
- **External binaries** — `yt-dlp`, `ffmpeg` (which also provides `ffprobe`) and `deno` (optional but recommended, passed to yt-dlp as a JS runtime for YouTube). These are installed separately and never bundled into the packaged app; all required ones are checked at startup.

## Installation
```bash
# 1. Install Python dependencies
pip install -r requirements.txt
```

> 📖 **Full step-by-step guide for a new machine (macOS & Linux): see [setup.md](setup.md)** — per-OS installs (Homebrew vs. distro packages, the xcb/X11 libraries Linux needs), venv, packaging, and troubleshooting.

## Running the GUI
```bash
python main.py
```
> No directory change is required – run in the repository root.

## Packaging a MacOS App
```bash
# Canonical build: windowed .app bundle with icon and stylesheet
./build-macos.sh
# The bundle will be in "dist/YT-DLP Downloader.app"
```
Alternative bare binary: `pyinstaller --onefile main.py` → `dist/main`.

## Building / Running on Linux
The app is cross-platform; the macOS-only dock-icon integration is disabled
automatically on Linux (`pyobjc` is skipped via a requirements marker).

Run from source:
```bash
python3 main.py
```

Packaging (must run on a Linux machine — PyInstaller cannot cross-compile from macOS):
```bash
./build-linux.sh     # → dist/YT-DLP Downloader/
```
Build-host Qt/X11 deps for the PyQt6 wheel's xcb plugin (Debian/Ubuntu):
`sudo apt install libxcb-cursor0 libxcb-xinerama0 libxkbcommon0 libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libgl1 libglib2.0-0 libfontconfig1 libdbus-1-3`.
On Wayland, run with `QT_QPA_PLATFORM=xcb` (via XWayland) or install the distro `qt6-wayland` package.
The external binaries (`yt-dlp`, `ffmpeg`, `deno`) are not bundled by PyInstaller;
install them on the target machine (`find_binary()` also checks
`/home/linuxbrew/.linuxbrew/bin`).

## Testing
```bash
# whole suite (from the repository root)
python3 -m unittest discover -s temp -p 'test_*.py'

# a single module
python3 -m unittest temp.test_subtitle_progress -v
```
The tests replay captured yt-dlp output through the real parser and run the real
download methods against a fake process — no Qt event loop and no network required.
A headless construction check is available as `python3 temp/smoke_gui.py`.
(`temp/test_dock_progress.py` is a manual dock-icon demo script, not a test.)

## Changelog

Release history lives in [CHANGELOG.md](CHANGELOG.md) — what changed in
each version, and which changes affect files already on disk.

## Disclaimer

This repository contains only a graphical interface that invokes the separately installed [yt-dlp](https://github.com/yt-dlp/yt-dlp) command-line tool; it ships no downloader binaries and contains no media content. The tool is intended for downloading content you have the right to access — your own uploads, Creative Commons-licensed media, public domain works, or content you have explicit permission to download.

Downloading copyrighted content without authorization may violate the Terms of Service of the source platform and/or applicable copyright law, depending on your jurisdiction. Use this app for personal, private purposes only; do not redistribute or monetize downloaded content.

You are solely responsible for ensuring that your use of this software complies with the Terms of Service of any site you interact with, as well as applicable local, national, and international law. The author does not condone or encourage copyright infringement and assumes no liability for how this tool is used. The MIT license covers this project's code — it does not grant any rights to third-party content.

## License
MIT © 2026
