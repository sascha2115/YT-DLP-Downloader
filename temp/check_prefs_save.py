"""End-to-end: save through the live Preferences dialog into a temp file.

Run from the repo root:
  QT_QPA_PLATFORM=offscreen python3 temp/check_prefs_save.py

Builds the real dialog, flips the checkboxes, presses Save, and prints the
file that landed on disk.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QDialog, QPlainTextEdit, QPushButton,
)

import main as app  # noqa: E402
from ytdl import preferences as prefs  # noqa: E402


def main():
    tmp = tempfile.mkdtemp()
    prefs.PREFERENCES_DIR = tmp
    prefs.PREFERENCES_FILE = os.path.join(tmp, "preferences.json")
    with open(prefs.PREFERENCES_FILE, "w", encoding="utf-8") as handle:
        json.dump({
            "channel_name_map": {"PowerfulJRE": "Joe Rogan"},
            "strip_bracketed_soundcues": True,
            "cookies_from_browser": "firefox",
            "cookies_from_browser_enabled": True,
            "show_fetchinfo_command": False,
            "my_own_key": 42,
        }, handle)

    qt_app = QApplication.instance() or QApplication(sys.argv)
    window = app.YTDLPDownloaderGUI()
    # Keep a reference to the QApplication: it must outlive the widgets.
    assert qt_app is not None

    def do_save():
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, QDialog):
            print("no modal dialog found")
            return
        dialog.findChild(QCheckBox, "strip_bracketed_soundcues").setChecked(False)
        dialog.findChild(QCheckBox, "show_fetchinfo_command").setChecked(True)
        dialog.findChild(QCheckBox, "cookies_from_browser").setChecked(False)
        editor = dialog.findChild(QPlainTextEdit)
        editor.setPlainText(json.dumps(
            {"channel_name_map": {"New": "Name"}}, indent=2))
        for button in dialog.findChildren(QPushButton):
            if button.text() == "Save":
                button.click()
                break
        with open(prefs.PREFERENCES_FILE, encoding="utf-8") as handle:
            print("saved file:", handle.read())

    QTimer.singleShot(300, do_save)
    window.open_preferences_dialog()


if __name__ == "__main__":
    main()
