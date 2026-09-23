# Headless GUI smoke test: constructs the full main window (no event loop).
# Run from the repo root:  python3 temp/smoke_gui.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
import main  # noqa: E402

window = main.YTDLPDownloaderGUI()
print("GUI constructed OK")
print("mixin bases:", [b.__name__ for b in type(window).__mro__[:7]])
print("reload icon set:", not window.reload_button.icon().isNull())
print("sb bar:", type(window.sb_bar).__name__)
print(
    "header OK:",
    "Supported: YouTube, Rumble" in window.output_text.toPlainText(),
)
