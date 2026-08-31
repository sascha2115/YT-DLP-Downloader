#!/bin/bash
# Build for Linux — MUST run on a Linux machine (PyInstaller cannot cross-compile
# from macOS). Output: dist/YT-DLP Downloader/ (onedir bundle; add --onefile for
# a single binary).
#
# Build host requirements (Debian/Ubuntu example):
#   python3 -m venv .venv-linux && source .venv-linux/bin/activate
#   pip install -r requirements.txt pyinstaller
#   sudo apt install -y libxcb-cursor0 libxcb-xinerama0 libxkbcommon0 \
#       libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
#       libxcb-render-util0 libxcb-shape0 libgl1 libglib2.0-0 \
#       libfontconfig1 libdbus-1-3          # Qt6/X11 runtime deps (xcb plugin)
#
# External binaries on the TARGET machine (not bundled by PyInstaller):
#   yt-dlp, ffmpeg, deno  — e.g. sudo apt install ffmpeg; pip install yt-dlp
#
# NOTE: styles.qss is bundled via --add-data; the ':' separator is correct on
# Linux (it would be ';' on Windows).
pyinstaller --name "YT-DLP Downloader" --windowed --icon AppIcon.png \
    --add-data "styles.qss:." app.py --clean --noconfirm