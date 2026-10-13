"""Preferences editor dialog (loads/saves preferences.json).

Mixin for YTDLPDownloaderGUI (assembled in ytdl/app.py);
methods access shared state via self."""

import json
import os
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QVBoxLayout
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

        # Show/hide explanations link
        self._prefs_show_explanations = False
        toggle_label = QLabel(
            '<small><a href="toggle">Show explanations</a></small>'
        )
        toggle_label.setOpenExternalLinks(False)
        toggle_label.linkActivated.connect(lambda _: self._toggle_explanations(toggle_label, hint_label))
        layout.addWidget(toggle_label)

        hint_label = QLabel(
            "<small>strip_bracketed_soundcues: true or false (default "
            f"{'true' if prefs.STRIP_BRACKETED_SOUNDCUES_DEFAULT else 'false'}). "
            "Removes bracketed sound cues such as [laughter], [snorts] and "
            "[musik] from subtitles; false keeps them.<br>"
            "cookies_from_browser: empty string (default) or one of "
            "brave, chrome, chromium, edge, firefox, opera, safari, vivaldi, whale "
            "(or firefox:profile_dir). Reads cookies from your browser for authenticated "
            "requests (fixes 403/Sign-in/age-restricted videos).<br>"
            "For Firefox: use the full profile directory name (e.g. "
            "<code>firefox:1abc2def.default-release</code>), not just <code>default-release</code>.<br>"
            "show_fetchinfo_command: true or false (default false). Logs the info-fetch "
            "yt-dlp command (with cookies/JS runtime) to the output panel for debugging.<br>"
            "Chromium-based browsers must be closed first (they lock the cookie DB).</small>"
        )
        hint_label.setWordWrap(True)
        hint_label.setVisible(False)
        layout.addWidget(hint_label)

        # Text editor for JSON
        text_edit = QPlainTextEdit()
        text_edit.setStyleSheet("""
            QPlainTextEdit {
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
                content = json.dumps(
                    {"channel_name_map": {},
                     "strip_bracketed_soundcues":
                         prefs.STRIP_BRACKETED_SOUNDCUES_DEFAULT,
                     "cookies_from_browser": "",
                     "show_fetchinfo_command": False},
                    indent=2,
                )
        except Exception as e:
            content = f"Error loading preferences: {e}"

        text_edit.setPlainText(content)
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

    def _toggle_explanations(self, toggle_label, hint_label):
        """Toggle visibility of the explanations label."""
        self._prefs_show_explanations = not self._prefs_show_explanations
        hint_label.setVisible(self._prefs_show_explanations)
        toggle_label.setText(
            '<small><a href="toggle">Hide explanations</a></small>'
            if self._prefs_show_explanations
            else '<small><a href="toggle">Show explanations</a></small>'
        )

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

            if "strip_bracketed_soundcues" in parsed:
                if not isinstance(parsed["strip_bracketed_soundcues"], bool):
                    raise ValueError(
                        "strip_bracketed_soundcues must be true or false")

            # Validate cookies_from_browser
            if "cookies_from_browser" in parsed:
                valid, err = prefs.validate_cookies_from_browser(parsed["cookies_from_browser"])
                if not valid and parsed["cookies_from_browser"]:
                    raise ValueError(f"cookies_from_browser: {err}")

            # Validate show_fetchinfo_command
            if "show_fetchinfo_command" in parsed:
                if not isinstance(parsed["show_fetchinfo_command"], bool):
                    raise ValueError("show_fetchinfo_command must be true or false")

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
