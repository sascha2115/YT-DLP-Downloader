#!/bin/bash
pyinstaller --name "YT-DLP Downloader" --windowed --icon assets/AppIcon.icns --add-data "assets:assets" app.py --clean --noconfirm