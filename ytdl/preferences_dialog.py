"""Preferences editor dialog (loads/saves preferences.json).

Mixin for YTDLPDownloaderGUI (assembled in ytdl/app.py);
methods access shared state via self."""

import json
import os
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout
from ytdl import preferences as prefs


class PreferencesDialogMixin:
    def open_preferences_dialog(self):
        """Open a dialog to edit the preferences.json file."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Preferences")
        dialog.resize(500, 400)

        layout = QVBoxLayout(dialog)

        # Label with file path
        path_label = QLabel(f"<small>Editing: {prefs.PREFERENCES_FILE}</small>")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        # Text editor for JSON
        text_edit = QTextEdit()
        text_edit.setStyleSheet("""
            QTextEdit {
                font-family: "Menlo", "Courier New";
                font-size: 12px;
            }
        """)

        # Load current preferences
        try:
            os.makedirs(prefs.PREFERENCES_DIR, exist_ok=True)
            if os.path.exists(prefs.PREFERENCES_FILE):
                with open(prefs.PREFERENCES_FILE, "r", encoding="utf-8") as f:
                    content = f.read()
            else:
                content = json.dumps({"channel_name_map": {}}, indent=2)
        except Exception as e:
            content = f"Error loading preferences: {e}"

        text_edit.setText(content)
        layout.addWidget(text_edit)

        # Buttons
        button_layout = QHBoxLayout()

        save_button = QPushButton("Save")
        save_button.clicked.connect(lambda: self._save_preferences(dialog, text_edit))
        button_layout.addWidget(save_button, 1)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_button, 1)

        layout.addLayout(button_layout)

        dialog.exec()

    def _save_preferences(self, dialog, text_edit):
        """Validate and save the edited preferences JSON."""
        content = text_edit.toPlainText().strip()
        try:
            parsed = json.loads(content)
            # Validate structure
            if not isinstance(parsed, dict):
                raise ValueError("Root must be a JSON object")
            if "channel_name_map" not in parsed:
                parsed["channel_name_map"] = {}
            if not isinstance(parsed["channel_name_map"], dict):
                raise ValueError("channel_name_map must be a JSON object")

            # Write to file
            os.makedirs(prefs.PREFERENCES_DIR, exist_ok=True)
            with open(prefs.PREFERENCES_FILE, "w", encoding="utf-8") as f:
                json.dump(parsed, f, indent=2, ensure_ascii=False)
                f.write("\n")

            # Reload preferences globally
            prefs.apply_preferences(parsed)

            self.signals.append_output.emit("✅ Preferences saved successfully")
            dialog.accept()
        except json.JSONDecodeError as e:
            self.signals.append_output.emit(f"🚩 Invalid JSON: {e}")
        except (ValueError, OSError) as e:
            self.signals.append_output.emit(f"🚩 Error saving preferences: {e}")
