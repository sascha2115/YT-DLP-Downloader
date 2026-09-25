"""Widget construction (init_ui), styling, dialogs, UI enable state.

Mixin for YTDLPDownloaderGUI (assembled in ytdl/app.py);
methods access shared state via self."""

import html
import logging
import os
import re
import shutil
import threading
import requests
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction, QKeyEvent, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import QApplication, QButtonGroup, QCheckBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QRadioButton, QSizePolicy, QStyle, QTextBrowser, QVBoxLayout, QWidget
from ytdl import APP_VERSION
from ytdl.config import DEFAULT_OUTPUT_DIR
from ytdl.progress import DownloadProgressManager
from ytdl.sites import SUPPORTED_SITES_LABEL
from ytdl.utils import get_log_dir, resource_path
from ytdl.widgets import BusySpinner, CustomTextEdit, SB_CATEGORY_COLORS, SB_DISPLAY_NAMES, SponsorBlockBar

logger = logging.getLogger(__name__)


class UiBuildMixin:
    def check_dependencies(self):
        missing = []

        if not os.path.isfile(self.yt_dlp_bin) and not shutil.which(self.yt_dlp_bin):
            missing.append("yt-dlp")

        if not os.path.isfile(self.ffmpeg_bin) and not shutil.which(self.ffmpeg_bin):
            missing.append("ffmpeg")

        if not os.path.isfile(self.ffprobe_bin) and not shutil.which(self.ffprobe_bin):
            missing.append("ffprobe")

        # deno is optional — yt-dlp works without it; only YouTube JS-challenge
        # extraction benefits from it. No startup warning needed.

        if missing:
            missing_str = ", ".join(missing)
            self.signals.append_output.emit(
                f"🚩 Error: Missing Dependencies: {missing_str}"
            )

    def _create_radio_group(self, title, attr, group_attr, buttons, enabled=True):
        # Create the top-level GroupBox and Layout
        group_box = QGroupBox(title)
        vertical_layout = QVBoxLayout(group_box)
        vertical_layout.setContentsMargins(15, 0, 20, 0)
        group_box.setLayout(vertical_layout)

        # Create the ButtonGroup instance
        button_group = QButtonGroup(self)
        setattr(self, group_attr, button_group)

        # Prepare data for the generic handler
        button_map = {}
        button_configs = []
        for i, btn_config in enumerate(buttons):
            radio_button = QRadioButton(btn_config["text"])
            setattr(self, btn_config["attr"], radio_button)
            button_group.addButton(radio_button, i)
            vertical_layout.addWidget(radio_button)

            # Set individual button enabled state
            btn_enabled = btn_config.get("enabled", True)
            radio_button.setEnabled(enabled and btn_enabled)
            button_configs.append({"button": radio_button, "enabled": btn_enabled})

            # Optional per-button tooltip (e.g. explaining the M4A container)
            if btn_config.get("tooltip"):
                radio_button.setToolTip(btn_config["tooltip"])

            # Populate map for the generic change handler
            button_map[radio_button.text().split(" ")[0].strip()] = btn_config["value"]

            if btn_config.get("is_default"):
                radio_button.setChecked(True)
                self.video_state[attr] = btn_config["value"]

        # Store the config for the generic change handler
        self.option_group_map.append(
            {
                "group": button_group,
                "attr": attr,
                "map": button_map,
                "enabled": enabled,
                "buttons": button_configs,
            }
        )
        # Connect to the generic handler
        button_group.buttonClicked.connect(self._on_option_group_change)

        # Set enabled state
        group_box.setEnabled(enabled)

        return group_box

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # URL Button
        url_layout = QHBoxLayout()
        self.paste_button = QPushButton("URL")
        self.paste_button.setToolTip("Click to paste clipboard content")
        self.paste_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.paste_button.clicked.connect(self.on_paste_button_click)
        url_layout.addWidget(self.paste_button)
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("Paste video URL here...")
        self.url_entry.textChanged.connect(self.on_url_text_change)
        self.url_entry.returnPressed.connect(self.fetch_video_info)
        url_layout.addWidget(self.url_entry)
        # Reload Button (right side): re-fetches video info for the current URL
        self.reload_button = QPushButton()
        self.reload_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.reload_button.setToolTip("Fetch video info again")
        self.reload_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reload_button.clicked.connect(self.on_reload_button_click)
        url_layout.addWidget(self.reload_button)
        main_layout.addLayout(url_layout)
        main_layout.addSpacing(10)

        # Video Title Input
        title_layout = QHBoxLayout()
        self.clean_button = QPushButton("Title")
        self.clean_button.setToolTip("Click to clean up video title")
        self.clean_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clean_button.clicked.connect(self.clean_title)
        title_layout.addWidget(self.clean_button)
        self.title_entry = QLineEdit()
        self.title_entry.setPlaceholderText("...")
        title_layout.addWidget(self.title_entry)
        main_layout.addLayout(title_layout)
        main_layout.addSpacing(15)

        # Options layout
        group_boxes_hlayout = QHBoxLayout()
        group_boxes_hlayout.setSpacing(15)
        # Info and Log buttons stacked vertically
        info_log_layout = QVBoxLayout()
        info_log_layout.setSpacing(4)

        self.thumbnail_button = QPushButton("Info")
        self.thumbnail_button.setToolTip("Click to see video description")
        self.thumbnail_button.setObjectName("thumbnailButton")
        self.thumbnail_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.thumbnail_button.clicked.connect(self.open_thumbnail_dialog)
        self.thumbnail_button.setEnabled(False)
        info_log_layout.addWidget(self.thumbnail_button)

        self.log_button = QPushButton("Log")
        self.log_button.setToolTip("Click to view the download log")
        self.log_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.log_button.clicked.connect(self.open_log_dialog)
        info_log_layout.addWidget(self.log_button)

        group_boxes_hlayout.addLayout(info_log_layout)

        # Configuration for all Radio Button Groups
        RADIO_GROUP_CONFIGS = [
            {
                "title": "Type",
                "attr": "media_type",
                "group_attr": "media_button_group",
                "buttons": [
                    {
                        "text": "Video + Audio",
                        "value": "video",
                        "is_default": True,
                        "attr": "video_audio_radio",
                    },
                    {
                        "text": "Video only",
                        "value": "video_only",
                        "attr": "video_only_radio",
                    },
                    {
                        "text": "Audio only",
                        "value": "audio",
                        "attr": "audio_only_radio",
                    },
                    {
                        "text": "Subtitles only",
                        "value": "subtitles",
                        "attr": "subtitles_only_radio",
                    },
                ],
            },
            {
                "title": "Quality",
                "attr": "quality",
                "group_attr": "quality_button_group",
                "buttons": [
                    {"text": "1440p", "value": "1440", "attr": "q1440_radio"},
                    {
                        "text": "1080p",
                        "value": "1080",
                        "is_default": True,
                        "attr": "q1080_radio",
                    },
                    {"text": "720p", "value": "720", "attr": "q720_radio"},
                    {"text": "Best", "value": "best", "attr": "qbest_radio"},
                ],
            },
            {
                "title": "Video",
                "attr": "video_codec",
                "group_attr": "codec_button_group",
                "buttons": [
                    {
                        "text": "Best",
                        "value": "best",
                        "is_default": True,
                        "attr": "codec_best_radio",
                    },
                    {"text": "H264", "value": "h264", "attr": "h264_radio"},
                    {"text": "VP9", "value": "vp9", "attr": "vp9_radio"},
                    {"text": "AV1", "value": "av1", "attr": "av1_radio"},
                ],
            },
            {
                "title": "Audio",
                "attr": "audio_format",
                "group_attr": "audio_format_button_group",
                "buttons": [
                    {
                        "text": "Best",
                        "value": "best",
                        "is_default": True,
                        "attr": "abest_radio",
                    },
                    {
                        "text": "M4A",
                        "value": "m4a",
                        "attr": "m4a_radio",
                        "tooltip": "AAC audio in an M4A (MP4) container. "
                        "Sources already in AAC (YouTube, Rumble) are remuxed "
                        "losslessly without re-encoding.",
                    },
                    {"text": "MP3", "value": "mp3", "attr": "mp3_radio"},
                    {"text": "Opus", "value": "opus", "attr": "opus_radio"},
                ],
            },
            {
                "title": "Container",
                "attr": "video_format",
                "group_attr": "video_format_button_group",
                "buttons": [
                    {
                        "text": "Best",
                        "value": "best",
                        "is_default": True,
                        "attr": "vbest_radio",
                    },
                    {"text": "MP4", "value": "mp4", "attr": "mp4_radio"},
                    {"text": "MKV", "value": "mkv", "attr": "mkv_radio"},
                    {"text": "WEBM", "value": "webm", "attr": "webm_radio"},
                ],
            },
        ]
        # group_boxes_hlayout.addStretch(1)

        # Loop and create the option groups dynamically
        for config in RADIO_GROUP_CONFIGS:
            group_box = self._create_radio_group(
                config["title"],
                config["attr"],
                config["group_attr"],
                config["buttons"],
                enabled=config.get("enabled", True),
            )
            group_boxes_hlayout.addWidget(group_box)

        # Subtitles Group
        subtitle_group_box = QGroupBox("Subtitles")
        subtitle_layout = QVBoxLayout()
        subtitle_layout.setContentsMargins(15, 0, 20, 0)
        # Language Checkboxes
        languages = [("English", "en"), ("German", "de"), ("Spanish", "es")]
        self.subtitle_checkboxes = {}
        # Languages whose last info fetch reported "(none)" availability;
        # their checkboxes stay disabled even when the UI is re-enabled
        self.subtitle_unavailable = set()
        for label, code in languages:
            cb = QCheckBox(f"{label} ({code})")
            # Start unchecked — subtitle selection is manual; an info fetch
            # only updates labels/availability (see _update_subtitle_checkboxes)
            cb.setChecked(False)
            subtitle_layout.addWidget(cb)
            self.subtitle_checkboxes[code] = cb
        subtitle_group_box.setLayout(subtitle_layout)
        group_boxes_hlayout.addWidget(subtitle_group_box)
        group_boxes_hlayout.addStretch(1)
        main_layout.addLayout(group_boxes_hlayout)
        main_layout.addSpacing(10)

        # SponsorBlock Checkboxes
        sponsorblock_group_box = QGroupBox("SponsorBlock")
        sponsorblock_group_box.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred
        )
        sponsorblock_layout = QHBoxLayout()
        sponsorblock_layout.setContentsMargins(10, 5, 25, 7)
        sponsorblock_layout.setSpacing(21)

        self.sb_sponsor_checkbox = QCheckBox(SB_DISPLAY_NAMES["sponsor"])
        self.sb_sponsor_checkbox.setToolTip("Paid promotion and direct advertisements.")
        self.sb_selfpromo_checkbox = QCheckBox(SB_DISPLAY_NAMES["selfpromo"])
        self.sb_selfpromo_checkbox.setToolTip(
            "Unpaid or self promotion, merchandise and donations."
        )
        self.sb_interaction_checkbox = QCheckBox(SB_DISPLAY_NAMES["interaction"])
        self.sb_interaction_checkbox.setToolTip("Reminder to like, subscribe or follow.")
        self.sb_intro_checkbox = QCheckBox(SB_DISPLAY_NAMES["intro"])
        self.sb_intro_checkbox.setToolTip(
            "Intro, intermission, animation or pause without content."
        )
        self.sb_ending_checkbox = QCheckBox(SB_DISPLAY_NAMES["ending"])
        self.sb_ending_checkbox.setToolTip("Outro, endcards or credits.")
        self.sb_preview_checkbox = QCheckBox(SB_DISPLAY_NAMES["preview"])
        self.sb_preview_checkbox.setToolTip("Preview (coming up) or recap.")
        self.sb_hook_checkbox = QCheckBox(SB_DISPLAY_NAMES["hook"])
        self.sb_hook_checkbox.setToolTip("Greetings or trailer for upcoming video.")
        self.sb_tangents_checkbox = QCheckBox(SB_DISPLAY_NAMES["tangents"])
        self.sb_tangents_checkbox.setToolTip("Tangential scene, filler or jokes.")
        
        # Hide "All" checkbox for now
        self.sb_all_checkbox = QCheckBox("All")
        self.sb_all_checkbox.hide()

        # Create mapping
        self.sb_checkbox_map = {
            self.sb_sponsor_checkbox: "sponsor",
            self.sb_selfpromo_checkbox: "selfpromo",
            self.sb_interaction_checkbox: "interaction",
            self.sb_intro_checkbox: "intro",
            self.sb_ending_checkbox: "ending",
            self.sb_preview_checkbox: "preview",
            self.sb_hook_checkbox: "hook",
            self.sb_tangents_checkbox: "tangents",
        }
        self.sb_all_checkbox.stateChanged.connect(self.toggle_sb_categories)
        
        # Default checked
        self.sb_sponsor_checkbox.setChecked(True)
        self.sb_selfpromo_checkbox.setChecked(True)

        # Style the checkboxes: labels in default color, indicator in category color
        for cb, cat in self.sb_checkbox_map.items():
            color = SB_CATEGORY_COLORS.get(cat, "#888888")
            cb.setStyleSheet(f"""
                QCheckBox {{
                    spacing: 4px;
                }}
                QCheckBox::indicator {{
                    width: 12px;
                    height: 12px;
                }}
                QCheckBox::indicator:unchecked {{
                    border: 2px solid {color};
                    background: transparent;
                    border-radius: 2px;
                }}
                QCheckBox::indicator:checked {{
                    border: 2px solid {color};
                    background: {color};
                    border-radius: 2px;
                }}
            """)

        # Layout into a single row
        for cb in [
            self.sb_sponsor_checkbox,
            self.sb_selfpromo_checkbox,
            self.sb_interaction_checkbox,
            self.sb_intro_checkbox,
            self.sb_ending_checkbox,
            self.sb_preview_checkbox,
            self.sb_hook_checkbox,
            self.sb_tangents_checkbox,
        ]:
            sponsorblock_layout.addWidget(cb)
        
        sponsorblock_layout.addStretch()
        sponsorblock_group_box.setLayout(sponsorblock_layout)

        sb_centering_layout = QHBoxLayout()
        sb_centering_layout.addSpacing(68)
        sb_centering_layout.addWidget(sponsorblock_group_box)
        sb_centering_layout.addStretch(1)
        main_layout.addLayout(sb_centering_layout)

        # SponsorBlock Visual Bar
        sb_bar_layout = QHBoxLayout()
        sb_bar_layout.setContentsMargins(70, 0, 78, 0)
        self.sb_bar = SponsorBlockBar()
        sb_bar_layout.addWidget(self.sb_bar)
        main_layout.addLayout(sb_bar_layout)
        main_layout.addSpacing(5)

        # Output Directory (layout intentionally not added to main_layout;
        # widgets kept because get_output_dir / start_download / _set_ui_enabled_state use them)
        self.output_dir_entry = QLineEdit(DEFAULT_OUTPUT_DIR)
        self.browse_button = QPushButton("📂")
        self.browse_button.clicked.connect(self.on_browse_directory)

        # Download Button
        self.download_button = QPushButton("Download")
        self.download_button.setToolTip("Start downloading (Cmd+D)")
        self.download_button.setObjectName("downloadButton")
        self.download_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.download_button.clicked.connect(self.start_download)
        download_hlayout = QHBoxLayout()
        download_hlayout.setSpacing(8)
        self.download_busy_indicator = BusySpinner(size=24)
        sp = self.download_busy_indicator.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.download_busy_indicator.setSizePolicy(sp)
        self.download_busy_indicator.hide()
        download_hlayout.addWidget(self.download_button, 1)
        download_hlayout.addWidget(
            self.download_busy_indicator, 0, Qt.AlignmentFlag.AlignVCenter
        )
        download_hlayout.setContentsMargins(65, 10, 35, 15)
        main_layout.addLayout(download_hlayout)

        # Area above Output Text
        output_header_layout = QHBoxLayout()

        # Clear Button
        # self.clear_link = QPushButton("Output:")
        # self.clear_link.setObjectName("clearLink")
        # self.clear_link.setToolTip("Click to clear output area")
        # self.clear_link.clicked.connect(self.clear_output)
        # output_header_layout.addWidget(self.clear_link)

        # Video + audio progress (one row, minimal gap)
        progress_row = QHBoxLayout()
        progress_row.setSpacing(1)
        progress_row.setContentsMargins(0, 0, 0, 0)

        bar_policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.video_progress_bar = QProgressBar()
        self.video_progress_bar.setObjectName("videoProgressBar")
        self.video_progress_bar.setRange(0, DownloadProgressManager.PROGRESS_MAX)
        self.video_progress_bar.setTextVisible(False)
        self.video_progress_bar.setSizePolicy(bar_policy)
        self.video_progress_bar.setValue(0)
        self.video_progress_bar.setToolTip("Video download")
        progress_row.addWidget(
            self.video_progress_bar, DownloadProgressManager.VIDEO_BAR_STRETCH
        )

        self.audio_progress_bar = QProgressBar()
        self.audio_progress_bar.setObjectName("audioProgressBar")
        self.audio_progress_bar.setRange(0, DownloadProgressManager.PROGRESS_MAX)
        self.audio_progress_bar.setTextVisible(False)
        self.audio_progress_bar.setSizePolicy(bar_policy)
        self.audio_progress_bar.setValue(0)
        self.audio_progress_bar.setToolTip("Audio download")
        progress_row.addWidget(
            self.audio_progress_bar, DownloadProgressManager.AUDIO_BAR_STRETCH
        )
        self._last_progress_values = (0, 0)
        self._set_progress_bars_visible(False)

        output_header_layout.addLayout(progress_row)
        main_layout.addLayout(output_header_layout)
        main_layout.addSpacing(5)

        # Output Text Area
        self.output_text = CustomTextEdit(self)
        self.output_text.setReadOnly(True)
        self.output_text.append("YT-DLP Downloader " + APP_VERSION)
        self.output_text.append("Supported: " + SUPPORTED_SITES_LABEL)
        main_layout.addWidget(self.output_text)

    def apply_styles(self):
        qss_path = resource_path("assets/styles.qss")
        try:
            with open(qss_path, "r") as f:
                stylesheet = f.read()
            self.setStyleSheet(stylesheet)
        except FileNotFoundError:
            logger.error(f"Stylesheet not found at: {qss_path}")
            self.signals.append_output.emit(f"🚩 Stylesheet not found: {qss_path}")

    def setup_menu_bar(self):
        menu_bar = self.menuBar()

        # App menu (YT-DLP Downloader)
        app_menu = menu_bar.addMenu("YT-DLP Downloader")

        prefs_action = QAction("Preferences...", self)
        prefs_action.setShortcut(QKeySequence("Ctrl+,"))
        prefs_action.triggered.connect(self.open_preferences_dialog)
        app_menu.addAction(prefs_action)

        app_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        app_menu.addAction(quit_action)

    def _set_ui_enabled_state(self, enabled: bool):
        # Primary Controls
        controls = [
            self.url_entry,
            self.paste_button,
            self.reload_button,
            self.title_entry,
            self.clean_button,
            self.output_dir_entry,
            self.browse_button,
            self.download_button,
            self.sb_all_checkbox,
        ]
        for control in controls:
            control.setEnabled(enabled)

        # Radio Button Option Groups
        for config in self.option_group_map:
            group_enabled = enabled and config.get("enabled", True)
            if "buttons" in config:
                for btn_info in config["buttons"]:
                    btn_info["button"].setEnabled(
                        group_enabled and btn_info.get("enabled", True)
                    )
            else:
                for button in config["group"].buttons():
                    button.setEnabled(group_enabled)

        # Subtitle Checkboxes (using the new dictionary); languages whose
        # last info fetch reported "(none)" availability stay disabled
        for code, cb in self.subtitle_checkboxes.items():
            cb.setEnabled(enabled and code not in self.subtitle_unavailable)

        # SponsorBlock Category Checkboxes
        sb_categories_enabled = enabled and not self.sb_all_checkbox.isChecked()

        for cb in self.sb_checkbox_map.keys():
            cb.setEnabled(sb_categories_enabled)

    def _on_option_group_change(self, button):
        group = button.group()
        # Find the matching configuration in the map
        config = next(
            (item for item in self.option_group_map if item["group"] == group), None
        )
        if not config:
            return

        # Get the clean button text (e.g., "1080" from "1080p", "MP4" from "MP4", or "H264")
        button_text = button.text().split(" ")[0].strip()
        # Determine the selected value using the map
        new_value = config["map"].get(button_text, button_text.lower())
        # save state
        self.video_state[config["attr"]] = new_value

        # If "Subtitles only" is selected, auto-check subtitles
        if config["attr"] == "media_type" and new_value == "subtitles":
            self._auto_select_subtitles()

    def toggle_sb_categories(self, state):
        # Disable/enable all other SponsorBlock checkboxes depending on "All"
        disable = state == Qt.CheckState.Checked.value
        for cb in [
            self.sb_sponsor_checkbox,
            self.sb_selfpromo_checkbox,
            self.sb_interaction_checkbox,
            self.sb_intro_checkbox,
            self.sb_ending_checkbox,
            self.sb_preview_checkbox,
            self.sb_hook_checkbox,
            self.sb_tangents_checkbox,
        ]:
            cb.setDisabled(disable)

    def on_browse_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Choose Output Directory", self.output_dir_entry.text()
        )
        if directory:
            self.output_dir_entry.setText(directory)

    def clean_title(self):
        title = self.title_entry.text()

        # Replacements
        title = title.replace(":", " - ")
        title = re.sub(r"[|–/]+", "-", title)

        # Remove all characters except: word characters (a-z, A-Z, 0-9, _), spaces, hyphens, and parentheses
        title = re.sub(r"[^\w\s\-\(\)]", "", title)

        # Clean up multiple spaces
        title = re.sub(r"\s+", " ", title).strip()

        # Convert to Title Case, while preserving already-lowercase words
        def to_title_case(text):
            parts = re.split(r"([ \-\(\)])", text)
            delimiters = [" ", "-", "(", ")"]
            capitalized_parts = []
            for p in parts:
                if p in delimiters or not p:
                    capitalized_parts.append(p)
                elif p.islower():
                    capitalized_parts.append(p)
                else:
                    capitalized_parts.append(p.capitalize())
            return "".join(capitalized_parts)

        # Check if the title starts with a season/episode code like S01E02 or S26E0110
        ep_match = re.match(r"^(S\d+E\d+)(.*)", title)
        if ep_match:
            episode_prefix = ep_match.group(1)  # e.g. "S01E02"
            rest = ep_match.group(2)            # e.g. " - my awesome video title"
            title = episode_prefix + to_title_case(rest)
        else:
            # No episode prefix, Title Case the entire title
            title = to_title_case(title)

        self.title_entry.setText(title)

    def open_thumbnail_dialog(self):
        thumbnail_url = self.video_state.get("thumbnail_url", "")
        if not thumbnail_url:
            self.signals.append_output.emit("👉 No thumbnail available")
            return

        # Single-flight guard: ignore extra clicks while a fetch is in progress
        if self._fetching_thumbnail:
            return
        self._fetching_thumbnail = True

        def _fetch_and_show():
            try:
                response = requests.get(thumbnail_url, timeout=10)
                response.raise_for_status()
                image_data = response.content
            except Exception as e:
                self.signals.append_output.emit(f"🚩 Error loading thumbnail: {e}")
                self._fetching_thumbnail = False
                return
            # Deliver bytes to the main thread via a queued signal — the only
            # safe way to trigger Qt widget construction from a worker thread.
            # (QTimer.singleShot from a plain threading.Thread has no event
            # loop and is silently dropped by Qt 6.)
            self.signals.thumbnail_ready.emit(image_data)

        threading.Thread(target=_fetch_and_show, daemon=True).start()

    def _show_thumbnail_dialog(self, image_data: bytes):
        self._fetching_thumbnail = False
        try:
            # Create dialog
            dialog = QDialog(self)
            dialog.setWindowTitle("Video Description")
            layout = QVBoxLayout(dialog)

            # Add Command+Q shortcut to the dialog
            quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), dialog)
            app_instance = QApplication.instance()
            if app_instance:
                quit_shortcut.activated.connect(app_instance.quit)

            # Create label for image
            image_label = QLabel()
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap()
            pixmap.loadFromData(image_data)

            # Scale image if too large
            max_width = 420
            max_height = 240
            if pixmap.width() > max_width or pixmap.height() > max_height:
                pixmap = pixmap.scaled(
                    max_width,
                    max_height,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

            image_label.setPixmap(pixmap)
            layout.addWidget(image_label)

            # Add description if available
            description = self.video_state.get("description", "")
            if description:
                description_text = QTextBrowser()
                description_text.setReadOnly(True)
                description_text.setOpenExternalLinks(True)

                # Escape HTML characters and convert URLs to clickable links
                escaped_desc = html.escape(description)
                url_pattern = r'(https?://[^\s<>"]+|www\.[^\s<>"]+)'

                def make_link(match):
                    url = match.group(1)
                    full_url = url if url.startswith("http") else "http://" + url
                    return f'<a href="{full_url}">{url}</a>'

                html_description = re.sub(url_pattern, make_link, escaped_desc)
                description_text.setHtml(html_description.replace("\n", "<br>"))
                layout.addWidget(description_text)

            dialog.resize(
                730,
                pixmap.height() + (400 if description else 0),
            )
            dialog.exec()

        except Exception as e:
            self.signals.append_output.emit(f"🚩 Error showing thumbnail dialog: {e}")

    def open_log_dialog(self):
        log_path = os.path.join(get_log_dir(), "app.log")
        dialog = QDialog(self)
        dialog.setWindowTitle("Download Log")
        dialog.resize(750, 500)

        layout = QVBoxLayout(dialog)

        text_browser = QTextBrowser()
        text_browser.setReadOnly(True)
        text_browser.setOpenExternalLinks(False)
        text_browser.setStyleSheet("""
            QTextBrowser {
                font-family: "Menlo", "Courier New";
                font-size: 12px;
            }
        """)

        MAX_LOG_LINES = 500
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Keep only the tail so the dialog stays fast on long-lived installs
            if len(lines) > MAX_LOG_LINES:
                omitted = len(lines) - MAX_LOG_LINES
                lines = [f"[ {omitted} earlier lines omitted ]\n"] + lines[-MAX_LOG_LINES:]

            if any(ln.strip() for ln in lines):
                # Color result lines: red for failed, green for succeeded
                colored_lines = []
                for line in lines:
                    line = line.rstrip("\n")
                    if "Download failed" in line:
                        colored_lines.append(f'<span style="color: #ff6b6b;">{html.escape(line)}</span>')
                    elif "Download succeeded" in line:
                        colored_lines.append(f'<span style="color: #4ade80;">{html.escape(line)}</span>')
                    else:
                        colored_lines.append(html.escape(line))
                text_browser.setHtml("<br>".join(colored_lines))
                # Auto-scroll to bottom so latest entries are visible (use timer to ensure content is rendered)
                def scroll_to_bottom():
                    scrollbar = text_browser.verticalScrollBar()
                    if scrollbar:
                        scrollbar.setValue(scrollbar.maximum())
                QTimer.singleShot(0, scroll_to_bottom)
            else:
                text_browser.setText("Log is empty.")
        except FileNotFoundError:
            text_browser.setText(f"Log file not found at:\n{log_path}")
        except OSError as e:
            text_browser.setText(f"Could not read log file:\n{e}")

        layout.addWidget(text_browser)

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)

        dialog.exec()

    def keyPressEvent(self, a0):
        if not isinstance(a0, QKeyEvent):
            return

        # Handle keyboard shortcuts
        modifiers = a0.modifiers()
        key = a0.key()

        # Handle Cmd/Ctrl + Q (Quit)
        if key == Qt.Key.Key_Q and (
            modifiers & Qt.KeyboardModifier.ControlModifier
            or modifiers & Qt.KeyboardModifier.MetaModifier
        ):
            self.close()
            a0.accept()
            return

        # Handle Cmd/Ctrl + L (Focus URL input and select all text)
        elif key == Qt.Key.Key_L and (
            modifiers & Qt.KeyboardModifier.ControlModifier
            or modifiers & Qt.KeyboardModifier.MetaModifier
        ):
            self.url_entry.setFocus()
            self.url_entry.selectAll()
            a0.accept()
            return

        # Handle Cmd/Ctrl + D (Start Download)
        elif key == Qt.Key.Key_D and (
            modifiers & Qt.KeyboardModifier.ControlModifier
            or modifiers & Qt.KeyboardModifier.MetaModifier
        ):
            if self.download_button.isEnabled():
                self.start_download()
            a0.accept()
            return

        super().keyPressEvent(a0)

    def get_selected_sb_categories(self):
        if self.sb_all_checkbox.isChecked():
            # Return all category values from the map
            return list(self.sb_checkbox_map.values())

        # Use the cached mapping
        return [
            category
            for checkbox, category in self.sb_checkbox_map.items()
            if checkbox.isChecked()
        ]
