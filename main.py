"""Thin launcher for YT-DLP Downloader.

The application lives in the `ytdl` package: the GUI class in
`ytdl/app.py`, pure logic in the sibling modules (sites.py, progress.py,
widgets.py, description.py, utils.py). This module only starts the app
and re-exports its public names, so `main.X` keeps working as the single
import surface for tests and tooling.
"""

import sys

# pylint: disable=wildcard-import,unused-wildcard-import
#   main.X is the deliberate single import surface for the tests and tooling
#   (see the module docstring) — the names are re-exported on purpose.
from ytdl.app import *  # noqa: F401,F403
from ytdl.app import YTDLPDownloaderGUI  # noqa: F401

if __name__ == "__main__":
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtWidgets import QApplication

    simulate_download_error = "--simulate-download-error" in sys.argv

    # Stable app identity: Qt derives the X11 WM_CLASS and the Wayland app_id
    # from these. Together with StartupWMClass=ytdl-downloader in the desktop
    # launcher file, the window manager can associate running windows with the
    # launcher's taskbar entry (icon + grouping) — without this, Python
    # windows fall back to a generic identity like "main.py"/"python3".
    QCoreApplication.setApplicationName("ytdl-downloader")
    app = QApplication(sys.argv)
    app.setDesktopFileName("ytdl-downloader")

    window = YTDLPDownloaderGUI()
    window.simulate_download_error = simulate_download_error
    window.show()
    sys.exit(app.exec())
