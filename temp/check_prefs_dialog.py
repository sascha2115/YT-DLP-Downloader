"""Headless check: build the Preferences dialog and exercise the cookie form.

Run from the repo root:
  QT_QPA_PLATFORM=offscreen python3 temp/check_prefs_dialog.py

The dialog is modal, so a timer closes it right after it opens; the script
then inspects the cookie widgets it contains.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog, QLineEdit  # noqa: E402

import main as app  # noqa: E402
from ytdl import preferences as prefs  # noqa: E402


def main():
    qt_app = QApplication.instance() or QApplication(sys.argv)

    window = app.YTDLPDownloaderGUI()

    def close_dialog():
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QDialog):
            checkbox = dialog.findChild(QCheckBox)
            field = dialog.findChild(QLineEdit)
            print("checkbox label:", checkbox.text())
            print("checkbox checked:", checkbox.isChecked())
            print("field text:", repr(field.text()))
            print("field enabled:", field.isEnabled())
            checkbox.setChecked(True)
            print("field enabled after checking:", field.isEnabled())
            dialog.accept()
        else:
            print("no modal dialog found")

    QTimer.singleShot(300, close_dialog)
    window.open_preferences_dialog()

    print("dialog closed OK")
    print("preferences file:", prefs.PREFERENCES_FILE)
    print("cookie_browser():", repr(prefs.cookie_browser()))
    print("enabled():", prefs.cookies_from_browser_enabled())


if __name__ == "__main__":
    main()
