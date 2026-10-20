"""Headless check: build the Preferences dialog and exercise the form.

Run from the repo root:
  QT_QPA_PLATFORM=offscreen python3 temp/check_prefs_dialog.py

The dialog is modal, so a timer closes it right after it opens; the script
then inspects the widgets it contains.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QDialog, QLabel, QLineEdit, QPlainTextEdit,
)

import main as app  # noqa: E402
from ytdl import preferences as prefs  # noqa: E402


def main():
    qt_app = QApplication.instance() or QApplication(sys.argv)

    window = app.YTDLPDownloaderGUI()

    def close_dialog():
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QDialog):
            for box in dialog.findChildren(QCheckBox):
                print("checkbox:", box.text(), "checked:", box.isChecked())
            field = dialog.findChild(QLineEdit)
            print("field text:", repr(field.text()))
            print("field enabled:", field.isEnabled())
            cookies = dialog.findChild(QCheckBox, "cookies_from_browser")
            cookies.setChecked(True)
            print("field enabled after checking:", field.isEnabled())
            # Geometry: the field must sit on the same line as the checkbox.
            print("checkbox geometry:", cookies.geometry().getRect())
            print("field geometry:", field.geometry().getRect())
            print("same line:", cookies.geometry().center().y() == field.geometry().center().y())
            editor = dialog.findChild(QPlainTextEdit)
            print("editor content:", editor.toPlainText())
            # The explanations live in the tooltips now, not in a separate
            # expandable section: every form widget must carry one, and
            # the "Show explanations" link must be gone.
            for name in ("cookies_from_browser", "strip_bracketed_soundcues",
                         "show_fetchinfo_command"):
                box = dialog.findChild(QCheckBox, name)
                print("tooltip", name, "->", repr(box.toolTip()))
            print("field tooltip ->", repr(field.toolTip()))
            print("editor tooltip ->", repr(editor.toolTip()))
            labels = [lbl.text() for lbl in dialog.findChildren(QLabel)]
            print("labels:", labels)
            print("has explanations link:",
                  any("explanation" in t.lower() for t in labels))
            # The path label at the top is gone; the file path is still
            # reachable through the editor's tooltip.
            print("has path label:",
                  any("Editing:" in t for t in labels))
            print("editor tooltip names the file:",
                  prefs.PREFERENCES_FILE in editor.toolTip())
            dialog.reject()
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
