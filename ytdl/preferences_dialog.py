"""Preferences editor dialog (loads/saves preferences.json).

Mixin for YTDLPDownloaderGUI (assembled in ytdl/app.py);
methods access shared state via self."""

import json
import os
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
)
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
            "cookies_from_browser_enabled: true or false (default false). The "
            "on/off switch for browser cookies.<br>"
            "cookies_from_browser: empty string (default) or one of "
            "brave, chrome, chromium, edge, firefox, opera, safari, vivaldi, whale "
            "(or firefox:profile_dir). Reads cookies from your browser for authenticated "
            "requests (fixes 403/Sign-in/age-restricted videos). Only used when "
            "cookies_from_browser_enabled is true.<br>"
            "For Firefox: use the full profile directory name (e.g. "
            "<code>firefox:1abc2def.default-release</code>), not just <code>default-release</code>.<br>"
            "show_fetchinfo_command: true or false (default false). Logs the info-fetch "
            "yt-dlp command (with cookies/JS runtime) to the output panel for debugging.<br>"
            "Chromium-based browsers must be closed first (they lock the cookie DB).</small>"
        )
        hint_label.setWordWrap(True)
        hint_label.setVisible(False)
        layout.addWidget(hint_label)

        # Browser cookies: an on/off switch and the browser to take them from.
        # The form mirrors the two JSON keys ("cookies_from_browser_enabled"
        # and "cookies_from_browser") in both directions: the JSON editor
        # updates the form while typing, and saving writes the form back
        # into the JSON. One source of truth, no surprise overwrites.
        cookie_box = QGroupBox("Browser cookies")
        cookie_layout = QVBoxLayout(cookie_box)

        cookie_enabled = QCheckBox("cookies_from_browser")
        cookie_enabled.setToolTip(
            "Use cookies from your browser for authenticated requests "
            "(fixes 403 / sign-in / age-restricted videos)."
        )
        cookie_layout.addWidget(cookie_enabled)

        # The browser field sits directly next to its label, not in a
        # full-width form row. The field is flat (no padding, no rounded
        # corners) and uses the pane's regular font size.
        browser_row = QHBoxLayout()
        browser_row.setContentsMargins(0, 0, 0, 0)
        browser_row.setSpacing(6)
        browser_label = QLabel("Browser")
        browser_label.setFixedWidth(60)
        browser_row.addWidget(browser_label)

        cookie_browser = QLineEdit()
        cookie_browser.setPlaceholderText("firefox, chrome, …")
        cookie_browser.setToolTip(
            "The browser to read cookies from. For Firefox use the full profile "
            "directory name, e.g. firefox:1abc2def.default-release. "
            "Chromium-based browsers must be closed first."
        )
        cookie_browser.setFixedWidth(240)
        # Flat field: no padding, no rounded corners, and the pane's regular
        # font size (taken from the label) instead of the stylesheet's larger
        # QLineEdit size.
        base_size = browser_label.font().pointSize()
        font_rule = (
            f"font-size: {base_size}pt;" if base_size > 0 else ""
        )
        cookie_browser.setStyleSheet(f"""
            QLineEdit {{
                padding: 0;
                border: 1px solid #525252;
                border-radius: 0;
                background-color: #383838;
                color: #fff;
                {font_rule}
            }}
            QLineEdit:focus {{
                border: 1px solid #777;
            }}
            QLineEdit:disabled {{
                color: #999;
            }}
        """)
        browser_row.addWidget(cookie_browser)
        browser_row.addStretch(1)
        cookie_layout.addLayout(browser_row)
        layout.addWidget(cookie_box)

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
                     "cookies_from_browser_enabled":
                         prefs.COOKIES_FROM_BROWSER_ENABLED_DEFAULT,
                     "show_fetchinfo_command": False},
                    indent=2,
                )
        except Exception as e:
            content = f"Error loading preferences: {e}"

        text_edit.setPlainText(content)
        layout.addWidget(text_edit)

        def _sync_form_from_json():
            """Mirror the JSON editor's cookie keys into the form."""
            try:
                parsed = json.loads(text_edit.toPlainText())
            except (json.JSONDecodeError, TypeError):
                return
            if not isinstance(parsed, dict):
                return
            cookie_enabled.setChecked(prefs.cookies_from_browser_enabled(parsed))
            browser = parsed.get("cookies_from_browser", "")
            cookie_browser.setText(browser if isinstance(browser, str) else "")

        text_edit.textChanged.connect(_sync_form_from_json)
        _sync_form_from_json()

        cookie_browser.setEnabled(cookie_enabled.isChecked())
        cookie_enabled.toggled.connect(cookie_browser.setEnabled)

        # Buttons
        button_layout = QHBoxLayout()

        save_button = QPushButton("Save")
        save_button.clicked.connect(
            lambda: self._save_preferences(
                dialog, text_edit, cookie_enabled, cookie_browser
            )
        )
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

    def _save_preferences(self, dialog, text_edit, cookie_enabled=None, cookie_browser=None):
        """Validate and save the edited preferences JSON.

        The cookie form fields, when given, are written back into the JSON
        before it is validated - the form is the primary way to set them.
        """
        content = text_edit.toPlainText().strip()
        try:
            parsed = json.loads(content)
            # Validate structure
            if not isinstance(parsed, dict):
                raise ValueError("Root must be a JSON object")

            # The cookie form is authoritative for its two keys.
            if cookie_enabled is not None:
                parsed["cookies_from_browser_enabled"] = bool(cookie_enabled.isChecked())
            if cookie_browser is not None:
                parsed["cookies_from_browser"] = cookie_browser.text().strip()

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

            # Validate the cookie on/off switch
            if "cookies_from_browser_enabled" in parsed:
                if not isinstance(parsed["cookies_from_browser_enabled"], bool):
                    raise ValueError(
                        "cookies_from_browser_enabled must be true or false")

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
