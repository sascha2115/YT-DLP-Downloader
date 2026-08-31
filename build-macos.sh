#!/bin/bash
# macOS build: windowed .app bundle (→ dist/YT-DLP Downloader.app).
# Same pyinstaller resolution as build-linux.sh (venv → ~/.local/bin → PATH):
# no venv activation required, works from any shell.

PYINSTALLER=""
for candidate in \
    "$(dirname "$0")/.venv/bin/pyinstaller" \
    "$HOME/.local/bin/pyinstaller" \
    "$(command -v pyinstaller 2>/dev/null)"
do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then
        PYINSTALLER="$candidate"
        break
    fi
done
if [ -z "$PYINSTALLER" ]; then
    echo "pyinstaller not found — install it into the project venv:"
    echo "    .venv/bin/pip install pyinstaller"
    exit 1
fi

"$PYINSTALLER" --name "YT-DLP Downloader" --windowed --icon assets/AppIcon.icns --add-data "assets:assets" app.py --clean --noconfirm