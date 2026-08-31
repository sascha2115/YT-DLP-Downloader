# YT‑DLP Downloader

A lightweight PyQt6 GUI for downloading YouTube videos via `yt‑dlp`.  It supports advanced options such as quality selection, subtitle handling, and SponsorBlock integration.

## Features
- **Video / Audio / Subtitles** selection
- **SponsorBlock** segment highlighting inside a custom bar
- Progress shown in the dock icon and the main window (dock icon on macOS only)
- Simple preferences file (macOS: `~/Library/Application Support/YT‑DLP Downloader`,
  Linux: `~/.config/YT‑DLP Downloader`)
- Built‑in packaging instructions using `pyinstaller`

## Prerequisites
| Item | Installation |
| To install via pip | pip install -r requirements.txt |
| External binaries (macOS) | brew install yt-dlp ffmpeg deno |
| External binaries (Linux) | pip install yt-dlp · distro package for ffmpeg · deno installer script |

> The external binaries are not bundled by PyInstaller: `yt-dlp` (pip or distro package), `ffmpeg` **and** `ffprobe` (distro `ffmpeg` package provides both; `ffprobe` is used by the app even though the startup warning doesn't list it), and `deno` (optional but recommended — passed to yt-dlp as a JS runtime for YouTube extraction; the app warns but still runs without it). All are checked at startup; a `pip`-installed `yt-dlp` in the Python that launches the app is detected as well.

## Installation
```bash
# 1. Install Python dependencies
pip install -r requirements.txt
```

> 📖 **Full step-by-step guide for a new machine (macOS & Linux): see [setup.md](setup.md)** — system packages, external binaries, venv, packaging, and troubleshooting.

## Running the GUI
```bash
python app.py
```
> No directory change is required – run in the repository root.

## Packaging a MacOS App
```bash
# Canonical build: windowed .app bundle with icon and stylesheet
./build.sh
# The bundle will be in "dist/YT-DLP Downloader.app"
```
Alternative bare binary: `pyinstaller --onefile app.py` → `dist/app`.

## Building / Running on Linux
The app is cross-platform; the macOS-only dock-icon integration is disabled
automatically on Linux (`pyobjc` is skipped via a requirements marker).

Run from source:
```bash
python3 app.py
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
python3 -m unittest temp.test_subtitle_progress -v
```
(Unit tests for the yt-dlp output parser / progress handling; run from the repository root. `temp/test_dock_progress.py` is a manual dock-icon demo script, not a test suite.)

## Common Gotchas
- **Signals**: Do not update UI widgets directly from background threads; use the `SignalEmitter` class.
- **External binaries**: Use `find_binary()` internally (PATH → pip scripts dir → common macOS/Linux install locations); use absolute paths in your own scripts.
- **yt-dlp extraction errors**: try the nightly pre-release — `python3 -m pip install -U --pre "yt-dlp[default]"` (inside the venv; add `--break-system-packages` for system-wide installs on PEP 668 distros). Roll back with the same command without `--pre`.
- **Archives**: The `archive/` directory contains legacy releases and should not be touched. (It exists only in the developer's local workspace — it is not part of this repository.)

## License
MIT © 2026
