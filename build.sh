#!/bin/bash
pyinstaller --name "YT-DLP Downloader" --windowed --icon AppIcon.icns --add-data "styles.qss:." app.py --clean --noconfirm