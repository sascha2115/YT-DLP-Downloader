# ==================================================================================================================================
# YT-DLP Downloader
# a GUI (PyQt6) application for downloading YouTube videos
# with advanced options for format, quality, codec, subtitles, and SponsorBlock removal
# also optional subtitles resyncing to removed chapters
# Requirements: yt-dlp, ffmpeg
# Info: originaly we used "--sponsorblock-remove" to cut out all sponsor chapters,
# but now we create ".edl" files for Kodi to skip those chapters. This is smoother.
# And so the subtitles resyncing is not really needed anymore.
# ----------------------------------------------------------------------------------------------------------------------------------
# pyinstaller --name "YT-DLP Downloader" --windowed --icon AppIcon.icns --add-data "styles.qss:." app.py --clean --noconfirm
# ----------------------------------------------------------------------------------------------------------------------------------
# python3 app.py --simulate-download-error
# ==================================================================================================================================
#
APP_VERSION = "1.1.20"
import gc
import glob
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import threading
import time
import unicodedata
from datetime import datetime
from dataclasses import dataclass, field


import requests

# macOS-only framework bindings (dock tile badge + progress overlay).
# Optional dependency: on Linux (and other platforms) AppKit does not exist,
# so we import conditionally and degrade gracefully — every AppKit use below
# is either gated on IS_MACOS or no-ops when self.dockTile is None.
IS_MACOS = sys.platform == "darwin"
if IS_MACOS:
    from AppKit import (
        NSApplication,
        NSImage,
        NSImageView,
        NSColor,
        NSBezierPath,
    )  # type: ignore
    from Foundation import NSMakeRect  # type: ignore
else:
    NSApplication = NSImage = NSImageView = NSColor = NSBezierPath = None
    NSMakeRect = None
from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal, QRectF
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QContextMenuEvent,
    QKeyEvent,
    QKeySequence,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# ----------------------------------------------------------------------------------------------------
# Logger Setup
# ----------------------------------------------------------------------------------------------------
if IS_MACOS:
    log_dir = os.path.expanduser("~/Library/Logs/YT-DLP Downloader")
else:
    # Linux & other Unix: XDG state directory
    log_dir = os.path.join(
        os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
        "YT-DLP Downloader",
    )
os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(log_dir, "app.log")),
        logging.StreamHandler(),
    ],
    force=True,
)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------------------------------
# Load Preferences
# ----------------------------------------------------------------------------------------------------
if IS_MACOS:
    PREFERENCES_DIR = os.path.expanduser(
        "~/Library/Application Support/YT-DLP Downloader"
    )
else:
    # Linux & other Unix: XDG config directory
    PREFERENCES_DIR = os.path.join(
        os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
        "YT-DLP Downloader",
    )
PREFERENCES_FILE = os.path.join(PREFERENCES_DIR, "preferences.json")

def load_preferences():
    """Load preferences from preferences.json (macOS: ~/Library/Application Support/,
    Linux: ~/.config/)."""
    default_preferences = {
        "channel_name_map": {}
    }
    
    try:
        os.makedirs(PREFERENCES_DIR, exist_ok=True)
        if os.path.exists(PREFERENCES_FILE):
            with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
                preferences = json.load(f)
            return preferences
        else:
            return default_preferences
    except Exception as e:
        logger.error(f"Error loading preferences: {e}")
        return default_preferences

# Load preferences at module level
preferences = load_preferences()
CHANNEL_NAME_MAP = preferences.get("channel_name_map", {})

# ----------------------------------------------------------------------------------------------------
# Global Configuration
# ----------------------------------------------------------------------------------------------------
DEFAULT_OUTPUT_DIR = os.path.expanduser("~/Downloads")
TITLE_FETCH_DELAY_MS = 500

YOUTUBE_ID_REGEX = re.compile(
    r"(?:v=|\/|embed\/|shorts\/|live\/)([a-zA-Z0-9_-]{11})(?:[?&/ ]|$)"
)

RE_MERGE = re.compile(r'\[Merger\] Merging formats into "?(.*?)"?$')
RE_AUDIO = re.compile(r'\[ExtractAudio\] Destination: "?(.*?)"?$')
RE_DEST = re.compile(r'\[download\] Destination: "?(.*?)"?$')
RE_ALREADY = re.compile(r'\[download\] "?(.*?)"? has already been downloaded')
RE_CONVERT = re.compile(r'\[VideoConvertor\] (?:Converting|Recoding) video from .* to "?(.*?)"?$')
RE_SLEEP = re.compile(
    r"\[download\] Sleeping \d+\.\d+ seconds as required by the site\.\.\."
)

# Subtitle file extensions yt-dlp can output. The app requests srt
# (--sub-format srt --convert-subs srt), but yt-dlp may download an original
# subtitle format first (vtt, srv3, json3, ...) and convert it afterwards.
# Used to tell subtitle transfers apart from video/audio downloads, because
# yt-dlp downloads subtitles BEFORE the media streams.
SUBTITLE_EXTENSIONS = frozenset(
    {
        ".srt", ".vtt", ".ass", ".ssa", ".ttml", ".sbv", ".lrc", ".sami",
        ".scc", ".json3", ".srv1", ".srv2", ".srv3", ".srv4", ".mpl",
    }
)

# SponsorBlock category display names
SB_DISPLAY_NAMES = {
    "sponsor": "Sponsor",
    "selfpromo": "Selfpromo",
    "interaction": "Interaction",
    "intro": "Intro",
    "ending": "Ending",
    "preview": "Preview",
    "hook": "Hook",
    "tangents": "Tangents",
    "highlight": "Highlight",
    "music_offtopic": "Music/Offtopic",
}

# Mapping from SponsorBlock API internal names to our display/internal names
SB_API_MAP = {
    "outro": "ending",
    "filler": "tangents",
    "poi_highlight": "highlight",
}

SB_CATEGORY_COLORS = {
    "sponsor": "#00cc00",
    "selfpromo": "#d4ac0d",
    "interaction": "#8e44ad",
    "intro": "#16bbcc",
    "ending": "#0202ed",
    "preview": "#008fd6",
    "hook": "#395699",
    "tangents": "#7300ff",
    "music_offtopic": "#888888",
    "highlight": "#9b044c",
    "exclusive_access": "#888888",
}


# ====================================================================================================
# Signal Emitter Class for Thread-Safe GUI Updates
# ====================================================================================================
class SignalEmitter(QObject):
    update_title = pyqtSignal(str)
    append_output = pyqtSignal(str)
    update_last_line = pyqtSignal(str)
    enable_button = pyqtSignal()
    update_download_progress = pyqtSignal(int, int)
    title_fetch_complete = pyqtSignal(dict)
    set_indeterminate = pyqtSignal(bool)
    update_subtitle_checkboxes = pyqtSignal(str)
    update_dock_tile = pyqtSignal(str)
    update_dock_progress = pyqtSignal(float)
    update_sb_bar = pyqtSignal(list, float)
    set_download_button_label = pyqtSignal(str)
    set_download_button_status = pyqtSignal(str)


# ----------------------------------------------------------------------------------------------------
# Custom SponsorBlock Visualization Bar
# ----------------------------------------------------------------------------------------------------
class SponsorBlockBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments = []
        self.duration = 0
        self.setFixedHeight(7)
        self.setToolTip("SponsorBlock segments visualization")
        # Colors based on categories
        self.category_colors = {
            cat: QColor(hex_val) for cat, hex_val in SB_CATEGORY_COLORS.items()
        }
        self.default_color = QColor("#888888")   # Grey

    def set_segments(self, segments, duration):
        self.segments = segments if segments else []
        self.duration = duration

        if self.segments and self.duration > 0:
            lines = []
            # Sort segments by start time
            sorted_segments = sorted(
                self.segments, key=lambda x: x.get("segment", [0, 0])[0]
            )
            for seg in sorted_segments:
                cat_key = seg.get("category", "unknown")
                # Translate from API name if needed
                cat_key = SB_API_MAP.get(cat_key, cat_key)
                category = SB_DISPLAY_NAMES.get(cat_key, cat_key.capitalize())
                times = seg.get("segment", [0, 0])
                start_m, start_s = divmod(int(times[0]), 60)
                end_m, end_s = divmod(int(times[1]), 60)
                lines.append(
                    f"{category}: {start_m}:{start_s:02d} - {end_m}:{end_s:02d}"
                )
            self.setToolTip("\n".join(lines))
        else:
            self.setToolTip("SponsorBlock segments visualization")

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Create rounded rect path for clipping
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 3, 3)
        painter.setClipPath(path)

        # Draw background
        painter.setBrush(QColor("#444444"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(self.rect())

        if not self.segments or self.duration <= 0:
            return

        width = self.width()
        for seg in self.segments:
            cat_key = seg.get("category", "unknown")
            # Translate from API name if needed
            cat_key = SB_API_MAP.get(cat_key, cat_key)
            
            times = seg.get("segment", [0, 0])
            start = float(times[0])
            end = float(times[1])

            x_start = (start / self.duration) * width
            x_end = (end / self.duration) * width
            seg_width = x_end - x_start

            color = self.category_colors.get(cat_key, self.default_color)
            painter.setBrush(color)
            painter.drawRect(int(x_start), 0, max(1, int(seg_width)), self.height())


# ----------------------------------------------------------------------------------------------------
# Small busy spinner (QProgressIndicator is not available in all PyQt6 builds)
# ----------------------------------------------------------------------------------------------------
class BusySpinner(QWidget):
    SPINNER_COLOR = QColor("#259")
    INTERVAL_MS = 80

    def __init__(self, parent=None, size: int = 18):
        super().__init__(parent)
        self._angle = 0
        self.setFixedSize(size, size)
        self.setToolTip("Working…")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)

    def start(self):
        self._angle = 0
        self._timer.start(self.INTERVAL_MS)
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _rotate(self):
        self._angle = (self._angle + 36) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        margin = 3
        rect = QRectF(margin, margin, side - 2 * margin, side - 2 * margin)
        pen = QPen(self.SPINNER_COLOR)
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(rect, -self._angle * 16, -270 * 16)


# ====================================================================================================
# Download Progress Manager Class
# ====================================================================================================
class DownloadProgressManager:
    PROGRESS_MAX = 1000
    PROGRESS_SCALE = 10

    # Typical 1080p VP9/WebM + best Opus: video is ~90–95% of bytes (audio ~128–165 kbps
    # vs video ~2–5 Mbps). Bar widths use 80/20 so the audio strip stays visible; dock
    # overlay uses 85/15 for a closer byte-weighted overall estimate.
    VIDEO_BAR_STRETCH = 4
    AUDIO_BAR_STRETCH = 1
    VIDEO_BYTE_WEIGHT = 0.85
    AUDIO_BYTE_WEIGHT = 0.15

    STREAM_VIDEO = "video"
    STREAM_AUDIO = "audio"

    def __init__(self, media_type: str = "video"):
        self.media_type = media_type
        self.video_progress = 0
        self.audio_progress = 0
        self.active_stream = self.STREAM_VIDEO
        self.download_count = 0
        self.started = False

    def mark_started(self):
        self.started = True

    def is_started(self):
        return self.started

    def on_download_destination(self):
        """Called when yt-dlp starts downloading a new file (video then audio)."""
        self.download_count += 1
        if self.media_type == "audio":
            self.active_stream = self.STREAM_AUDIO
        elif self.media_type == "video_only":
            self.active_stream = self.STREAM_VIDEO
        elif self.download_count == 1:
            self.active_stream = self.STREAM_VIDEO
        else:
            self.active_stream = self.STREAM_AUDIO

    def update_from_ytdlp_percent(self, yt_dlp_percent: float) -> tuple[int, int]:
        value = min(
            self.PROGRESS_MAX,
            int(yt_dlp_percent * self.PROGRESS_SCALE),
        )
        if self.active_stream == self.STREAM_AUDIO:
            self.audio_progress = value
        else:
            self.video_progress = value
        return self.video_progress, self.audio_progress

    def mark_complete(self) -> tuple[int, int]:
        if self.media_type == "audio":
            self.audio_progress = self.PROGRESS_MAX
        elif self.media_type == "video_only":
            self.video_progress = self.PROGRESS_MAX
        else:
            self.video_progress = self.PROGRESS_MAX
            self.audio_progress = self.PROGRESS_MAX
        return self.video_progress, self.audio_progress

    def get_combined_fraction(self) -> float:
        """Overall progress for dock icon overlay (0.0 – 1.0)."""
        v = self.video_progress / self.PROGRESS_MAX
        a = self.audio_progress / self.PROGRESS_MAX
        if self.media_type == "audio":
            return a
        if self.media_type == "video_only":
            return v
        if self.download_count <= 1 and self.audio_progress == 0:
            return v
        return (
            v * self.VIDEO_BYTE_WEIGHT + a * self.AUDIO_BYTE_WEIGHT
        )


# ====================================================================================================
# Custom QTextEdit Class
# ====================================================================================================
class CustomTextEdit(QTextEdit):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window

    # add context menu
    def contextMenuEvent(self, e: QContextMenuEvent | None):
        if e is None:
            return

        menu = self.createStandardContextMenu()
        if menu is None:
            menu = QMenu(self)

        menu.addSeparator()
        # Check the state of the main window
        is_busy = (
            self.main_window.video_state["is_fetching_info"]
            or self.main_window.video_state["is_download_running"]
        )
        clear_action = QAction("Clear Output", self)
        # Set the enabled state based on the check
        clear_action.setEnabled(not is_busy)
        # Keep the app header line when clearing output
        clear_action.triggered.connect(self.main_window.clear_output)
        menu.addAction(clear_action)
        menu.exec(e.globalPos())


# ====================================================================================================
# Main Class
# ====================================================================================================
class YTDLPDownloaderGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YT-DLP Downloader")
        self.setGeometry(100, 100, 900, 845)
        # self.setFixedSize(self.size())

        # Window/taskbar icon: Linux window managers take it from QIcon (macOS
        # takes it from the .app bundle), so set it whenever a PNG icon ships.
        _icon_path = resource_path("AppIcon.png")
        if os.path.exists(_icon_path):
            self.setWindowIcon(QIcon(_icon_path))

        # Initialize variables
        self.video_state = {
            "url": "",
            "clean_url": "",
            "video_id": "",
            "title": "",
            "description": "",
            "thumbnail_url": "",
            "upload_date": "",
            "language": "",
            "output_dir": DEFAULT_OUTPUT_DIR,
            "base_filename": "",
            "full_path": "",
            "media_type": "video",
            "video_format": "best",
            "audio_format": "best",
            "quality": "1080",
            "video_codec": "best",
            "is_fetching_info": False,
            "is_download_running": False,
            "episode_code": "",  # Set when a special-case channel overrides the episode number
        }

        # Cache for video metadata
        self.cached_video_metadata = None

        # Cache binary paths
        self.yt_dlp_bin = find_binary("yt-dlp")
        self.ffprobe_bin = find_binary("ffprobe")
        self.ffmpeg_bin = find_binary("ffmpeg")
        self.deno_bin = find_binary("deno")

        # Test helpers (CLI flags set in __main__)
        self.simulate_download_error = False

        # Map for radio buttons
        self.option_group_map = []

        # Signal emitter for thread-safe updates
        self.signals = SignalEmitter()
        self.signals.update_title.connect(self.set_title_label)
        self.signals.append_output.connect(self.append_output_text)
        self.signals.update_last_line.connect(self.update_last_output_line)
        self.signals.enable_button.connect(self.enable_all_controls)
        self.signals.title_fetch_complete.connect(self.title_fetch_finished)
        self.signals.set_indeterminate.connect(self.handle_indeterminate_state)
        self.signals.update_download_progress.connect(self._set_download_progress)
        self.signals.update_subtitle_checkboxes.connect(
            self._update_subtitle_checkboxes
        )
        self.signals.update_dock_tile.connect(self.setDockTileCheck)
        self.signals.update_dock_progress.connect(self.setDockProgressOverlay)
        self.signals.update_sb_bar.connect(
            lambda segments, duration: self.sb_bar.set_segments(segments, duration)
        )
        self.signals.set_download_button_label.connect(self.set_download_button_label)
        self.signals.set_download_button_status.connect(self.set_download_button_status)

        # Shortcut for Preferences (Cmd+,)
        self.prefs_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        self.prefs_shortcut.activated.connect(self.open_preferences_dialog)

        # Timer for fetching UI title entryfield
        self.fetch_title_timer = QTimer()
        self.fetch_title_timer.setSingleShot(True)
        self.fetch_title_timer.timeout.connect(self.fetch_video_info)

        # Init
        self.init_ui()
        self.setup_menu_bar()
        self.apply_styles()
        self.check_dependencies()

        # Get MacOS Dock Tile for later
        try:
            self.dockTile = NSApplication.sharedApplication().dockTile()
        except Exception:
            self.dockTile = None

        # Check clipboard on startup
        self.check_clipboard_on_startup()

    # ----------------------------------------------------------------------------------------------------
    # Check if required binaries are available at startup
    # ----------------------------------------------------------------------------------------------------
    def check_dependencies(self):
        # print(f"yt-dlp: {self.yt_dlp_bin}")
        # print(f"ffmpeg: {self.ffmpeg_bin}")
        # print(f"ffprobe: {self.ffprobe_bin}")
        # print(f"deno: {self.deno_bin}")
        missing = []

        if not shutil.which(self.yt_dlp_bin):
            missing.append("yt-dlp")

        if not shutil.which(self.ffmpeg_bin):
            missing.append("ffmpeg")

        if not shutil.which(self.deno_bin):
            missing.append("deno")

        if missing:
            missing_str = ", ".join(missing)
            self.signals.append_output.emit(
                f"🚩 Error: Missing Dependencies: {missing_str}"
            )

    # ----------------------------------------------------------------------------------------------------
    # Get the current output directory
    # ----------------------------------------------------------------------------------------------------
    def get_output_dir(self):
        # Use cached value when running in a thread or during download to avoid UI access
        if (
            self.video_state.get("is_download_running")
            or threading.current_thread() is not threading.main_thread()
        ):
            return self.video_state.get("output_dir", DEFAULT_OUTPUT_DIR)
        return self.output_dir_entry.text().strip() or DEFAULT_OUTPUT_DIR

    # ----------------------------------------------------------------------------------------------------
    # Construct full path to file with optional extension and language
    # ----------------------------------------------------------------------------------------------------
    def get_full_path(self, extension="", lang=None):
        base = self.video_state["base_filename"]
        output_dir = self.get_output_dir()

        if not base:
            return ""

        # If a language is provided, inject it before the extension
        if lang:
            base = f"{base}.{lang}"

        if extension:
            if not extension.startswith("."):
                extension = "." + extension
            return os.path.join(output_dir, base + extension)

        return os.path.join(output_dir, base)

    # ----------------------------------------------------------------------------------------------------
    # Get the yt-dlp filename template
    # ----------------------------------------------------------------------------------------------------
    def get_filename_template(self):
        base = self.video_state["base_filename"]
        output_dir = self.get_output_dir()

        if not base:
            return ""

        return f"{output_dir}/{base}.%(ext)s"

    # ----------------------------------------------------------------------------------------------------
    # Update video state with validation
    # ----------------------------------------------------------------------------------------------------
    def update_video_state(self, **kwargs):
        self.video_state.update(kwargs)

        # Update base_filename if title changed
        if "title" in kwargs:
            self.video_state["base_filename"] = kwargs["title"]

    # ----------------------------------------------------------------------------------------------------
    # Method to get cleaned URL
    # ----------------------------------------------------------------------------------------------------
    def get_clean_url(self):
        raw_url = self.url_entry.text().strip()
        if not raw_url:
            self.video_state["url"] = ""
            self.video_state["clean_url"] = ""
            return ""

        # Use centralized normalization
        clean_url = normalize_url(raw_url)
        self.video_state["url"] = raw_url
        self.video_state["clean_url"] = clean_url
        return clean_url

    # ----------------------------------------------------------------------------------------------------
    # Extract and cache video ID
    # ----------------------------------------------------------------------------------------------------
    def extract_video_id(self, url):
        # Check if we already extracted this URL
        if url == self.video_state["url"] and self.video_state["video_id"]:
            return self.video_state["video_id"]

        match = YOUTUBE_ID_REGEX.search(url)
        if match:
            video_id = match.group(1)
            # Update state
            self.video_state["url"] = url
            self.video_state["video_id"] = video_id
            return video_id

        # Not found - clear cache
        self.video_state["url"] = url
        self.video_state["video_id"] = ""
        return None

    # ----------------------------------------------------------------------------------------------------
    # Returns a list of language codes for currently checked subtitle boxes
    # ----------------------------------------------------------------------------------------------------
    def get_selected_subtitle_codes(self):
        return [code for code, cb in self.subtitle_checkboxes.items() if cb.isChecked()]

    # ----------------------------------------------------------------------------------------------------
    # Check clipboard on app launch
    # ----------------------------------------------------------------------------------------------------
    def check_clipboard_on_startup(self):
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return

        clipboard_content = clipboard.text()

        # Use helper function in Silent_mode for clean startup experience
        self._process_and_set_url(clipboard_content, silent_mode=True)

    # ----------------------------------------------------------------------------------------------------
    # Create a radio button group and wire it up
    # ----------------------------------------------------------------------------------------------------
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

    # ----------------------------------------------------------------------------------------------------
    # Main GUI
    # ----------------------------------------------------------------------------------------------------
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
        self.url_entry.setPlaceholderText("Paste YouTube URL here...")
        self.url_entry.textChanged.connect(self.on_url_text_change)
        self.url_entry.returnPressed.connect(self.fetch_video_info)
        url_layout.addWidget(self.url_entry)
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
                    {"text": "M4A", "value": "m4a", "attr": "m4a_radio"},
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
        self.subtitles_checkbox = QCheckBox("Subtitles")
        self.subtitles_checkbox.setChecked(False)
        # self.subtitles_checkbox.stateChanged.connect(self.on_subtitles_toggle)

        # Language Checkboxes
        languages = [("English", "en"), ("German", "de"), ("Spanish", "es")]
        self.subtitle_checkboxes = {}
        # Languages whose last info fetch reported "(none)" availability;
        # their checkboxes stay disabled even when the UI is re-enabled
        self.subtitle_unavailable = set()
        for label, code in languages:
            cb = QCheckBox(f"{label} ({code})")
            subtitle_layout.addWidget(cb)
            self.subtitle_checkboxes[code] = cb
        # Set default state
        # self.subtitle_checkboxes["en"].setChecked(True)
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

        # Output Directory
        output_dir_layout = QHBoxLayout()
        output_dir_layout.setContentsMargins(0, 5, 0, 0)
        output_dir_label = QLabel("Save to:")
        self.output_dir_entry = QLineEdit(DEFAULT_OUTPUT_DIR)
        self.browse_button = QPushButton("📂")
        self.browse_button.clicked.connect(self.on_browse_directory)
        output_dir_layout.addWidget(output_dir_label)
        output_dir_layout.addWidget(self.output_dir_entry)
        output_dir_layout.addWidget(self.browse_button)
        # main_layout.addLayout(output_dir_layout)

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
        main_layout.addWidget(self.output_text)
        # End of UI Init

    # ----------------------------------------------------------------------------------------------------
    # Add CSS Styling
    # ----------------------------------------------------------------------------------------------------
    def apply_styles(self):
        qss_path = resource_path("styles.qss")
        try:
            with open(qss_path, "r") as f:
                stylesheet = f.read()
            self.setStyleSheet(stylesheet)
        except FileNotFoundError:
            print(f"Error: Stylesheet file NOT found at: {qss_path}")

    # ----------------------------------------------------------------------------------------------------
    # Menu bar
    # ----------------------------------------------------------------------------------------------------
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

    # ----------------------------------------------------------------------------------------------------
    # Enable/disable all primary controls and option groups
    # ----------------------------------------------------------------------------------------------------
    def _set_ui_enabled_state(self, enabled: bool):
        # Primary Controls
        controls = [
            self.url_entry,
            self.paste_button,
            self.title_entry,
            self.clean_button,
            self.output_dir_entry,
            self.browse_button,
            self.download_button,
            self.thumbnail_button,
            self.subtitles_checkbox,
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

    # ----------------------------------------------------------------------------------------------------
    # Clicked Paste Button
    # ----------------------------------------------------------------------------------------------------
    def on_paste_button_click(self):
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return

        clipboard_content = clipboard.text()
        self._process_and_set_url(clipboard_content, silent_mode=False)

    # ----------------------------------------------------------------------------------------------------
    # Changed URL text
    # ----------------------------------------------------------------------------------------------------
    def on_url_text_change(self):
        new_raw_url = self.url_entry.text().strip()
        # Get normalized/cleaned version
        new_clean = normalize_url(new_raw_url)

        # Update the UI field if it was a naked ID or needs cleaning (e.g. removing &list=)
        if new_clean and new_clean != new_raw_url:
            self.url_entry.blockSignals(True)
            self.url_entry.setText(new_clean)
            self.url_entry.blockSignals(False)

        # Get the previously stored clean URL from state
        old_clean = self.video_state.get("clean_url", "")

        # Only invalidate if the actual base URL has changed
        if new_clean != old_clean:
            self.video_state["clean_url"] = ""
            self.video_state["video_id"] = ""
            self.video_state["url"] = ""
            self.video_state["episode_code"] = ""  # Clear special-case episode override
            # Disable thumbnail button since info is now stale
            self.thumbnail_button.setEnabled(False)
            # Clear SponsorBlock bar
            self.sb_bar.set_segments([], 0)

        self.fetch_title_timer.stop()
        self.fetch_title_timer.start(TITLE_FETCH_DELAY_MS)

    # ----------------------------------------------------------------------------------------------------
    # Generic handler for all QButtonGroup options
    # ----------------------------------------------------------------------------------------------------
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

    # ----------------------------------------------------------------------------------------------------
    # Update Subtitle Checkboxes
    # ----------------------------------------------------------------------------------------------------
    def _update_subtitle_checkboxes(self, lang_input):
        # Normalize the input language (e.g., 'en-US' -> 'en')
        compare_code = lang_input.split("-")[0].lower() if lang_input else ""
        
        available = self.video_state.get("available_subtitles", {})
        for code, cb in self.subtitle_checkboxes.items():
            # Update label
            status = available.get(code)
            lang_names = {"en": "English", "de": "German", "es": "Spanish"}
            name = lang_names.get(code, code)
            
            if status:
                cb.setText(f"{name} ({status})")
                cb.setEnabled(True)
                self.subtitle_unavailable.discard(code)
            else:
                # No subtitles available: label it "(none)", uncheck it and
                # keep it disabled (also across UI re-enables, see
                # _set_ui_enabled_state)
                cb.setText(f"{name} (none)")
                cb.setEnabled(False)
                cb.setChecked(False)
                self.subtitle_unavailable.add(code)

        # Trigger auto-selection if in subtitles-only mode
        if self.video_state.get("media_type") == "subtitles":
            self._auto_select_subtitles()

    # ----------------------------------------------------------------------------------------------------
    # Auto-select subtitles based on type (real vs auto)
    # ----------------------------------------------------------------------------------------------------
    def _auto_select_subtitles(self):
        # Identify types
        real_codes = []
        available_codes = []
        
        for code, cb in self.subtitle_checkboxes.items():
            text = cb.text().lower()
            if "(real)" in text:
                real_codes.append(code)
            if "(none)" not in text and cb.isEnabled():
                available_codes.append(code)
        
        # Apply selection rules
        if real_codes:
            # Check all real ones, uncheck others
            for code, cb in self.subtitle_checkboxes.items():
                cb.setChecked(code in real_codes)
        elif available_codes:
            # Check all available (non-none) ones
            for code, cb in self.subtitle_checkboxes.items():
                cb.setChecked(code in available_codes)

    # ----------------------------------------------------------------------------------------------------
    # Changed SponsorBlock
    # ----------------------------------------------------------------------------------------------------
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

    # ----------------------------------------------------------------------------------------------------
    # Clicked Browse Button
    # ----------------------------------------------------------------------------------------------------
    def on_browse_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Choose Output Directory", self.output_dir_entry.text()
        )
        if directory:
            self.output_dir_entry.setText(directory)

    # ----------------------------------------------------------------------------------------------------
    # Check if the URL is a valid YouTube URL
    # ----------------------------------------------------------------------------------------------------
    def is_youtube_url(self, url):
        return self.extract_video_id(url) is not None

    # ----------------------------------------------------------------------------------------------------
    # Process clipboard content (used by paste and startup)
    # ----------------------------------------------------------------------------------------------------
    def _process_and_set_url(self, clipboard_content, silent_mode=False):
        # Clean up and normalize clipboard content (now handles naked IDs too)
        content = normalize_url(clipboard_content)

        if content and self.is_youtube_url(content):
            # If valid set text - this will trigger on_url_text_change
            self.url_entry.setText(content)
            return True

        if not silent_mode:
            # Display error/info only when not in silent mode (i.e., when user clicks Paste)
            if content:
                self.signals.append_output.emit(
                    "Clipboard content is not a valid YouTube URL"
                )
            else:
                self.signals.append_output.emit("Clipboard is empty")

        return False

    # ----------------------------------------------------------------------------------------------------
    # Fetch video info in sub-thread
    # ----------------------------------------------------------------------------------------------------
    def fetch_video_info(self):
        url = self.get_clean_url()
        if not url:
            self.title_entry.setText("Please enter a valid YouTube URL")
            return

        video_id = self.extract_video_id(url)
        if not video_id:
            self.title_entry.setText("Please enter a valid YouTube URL")
            return

        self.title_entry.setText("Fetching video info...")
        # Reset any prior error styling as soon as we start fetching info again
        self.set_download_button_status("")
        # Temporarily update the Download button label while we fetch metadata
        self.download_button.setText("Getting Info...")
        # Set state variable
        self.video_state["is_fetching_info"] = True
        self._set_ui_enabled_state(False)
        self.download_button.setEnabled(False)
        self._reset_download_progress_bars()
        self._set_download_busy(True)
        self.signals.update_dock_tile.emit("")
        self.clearDockProgress()
        self.clear_output()
        thread = threading.Thread(target=self.get_video_info, args=(url,))
        thread.daemon = True
        thread.start()

    # ----------------------------------------------------------------------------------------------------
    # Get video info with yt-dlp
    # ----------------------------------------------------------------------------------------------------
    def get_video_info(self, url):
        error_status = {"error": False}
        try:
            cmd = [self.yt_dlp_bin]

            # Use JS runtime if available for extraction
            if self.deno_bin and shutil.which(self.deno_bin):
                cmd.extend(["--js-runtimes", f"deno:{self.deno_bin}"])

            cmd.extend(
                [
                    "--print-json",
                    "--no-warnings",
                    "--skip-download",
                    url,
                ]
            )
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                json_data = json.loads(result.stdout.strip())

                # yt-dlp JSON fields can sometimes be present but null/None.
                # Guard against "NoneType has no attribute 'strip'" by normalizing values.
                def _s(key: str, default: str = "") -> str:
                    val = json_data.get(key, default)
                    return str(val).strip() if val is not None else ""

                title = _s("title")
                upload_date = _s("upload_date")
                duration = _s("duration_string")
                duration_sec = json_data.get("duration") or 0
                youtube_channel = _s("channel")
                channel = CHANNEL_NAME_MAP.get(youtube_channel, youtube_channel)
                thumbnail_url = _s("thumbnail")
                language = _s("language")
                height = str(json_data.get("height", ""))
                raw_description = _s("description")
                description = clean_youtube_description(raw_description)
                ext = _s("ext")
                vcodec = _s("vcodec")
                fps = str(json_data.get("fps", ""))
                
                subs_dict = json_data.get("subtitles", {})
                autos_dict = json_data.get("automatic_captions", {})
                all_formats = json_data.get("formats", [])

                # Update consolidated state
                self.update_video_state(
                    original_title=title,
                    channel=channel,
                    description=description,
                    thumbnail_url=thumbnail_url,
                    upload_date=upload_date,
                    language=language,
                    detected_ext=ext,
                    detected_vcodec=vcodec,
                    duration_sec=duration_sec,
                )

                self.signals.append_output.emit(f"Channel: {youtube_channel}")
                if channel != youtube_channel:
                    self.signals.append_output.emit(f"Podcast: {channel}")

                # Format and display info
                formatted_date = upload_date
                short_date = upload_date
                if upload_date:
                    try:
                        dt = datetime.strptime(upload_date, "%Y%m%d")
                        formatted_date = dt.strftime("%Y-%m-%d")  # For display: "2026-01-10"
                        short_date = dt.strftime("S%yE%m%d")      # For title: "S26E0110"
                    except ValueError:
                        pass

                    self.signals.append_output.emit(f"Upload date: {formatted_date}")

                self.signals.append_output.emit(f"Duration: {duration}")

                # Resolutions
                unique_heights = sorted(list(set(
                    f.get("height") for f in all_formats 
                    if f.get("height") and isinstance(f.get("height"), int) and f.get("vcodec") != "none"
                )), reverse=True)
                
                if unique_heights:
                    res_str = " | ".join([f"{h}p" for h in unique_heights])
                    self.signals.append_output.emit(f"Resolutions: {res_str}")
                elif height and height.isdigit():
                    self.signals.append_output.emit(f"Max Resolution: {height}p")

                if fps and fps not in ("NA", "None", "", "None.0"):
                    self.signals.append_output.emit(f"FPS: {fps}")

                # Show bitrates for the selected resolution only
                if all_formats and height and height.isdigit():
                    try:
                        selected_height = int(height)
                        # Keep only video streams at the selected height with a real bitrate
                        matching_fmts = [
                            f for f in all_formats
                            if f.get("vbr")
                            and f.get("vcodec") not in (None, "none")
                            and f.get("height") == selected_height
                        ]
                        # Sort by vbr descending (premium stream will appear first)
                        matching_fmts.sort(key=lambda f: f["vbr"], reverse=True)
                        if matching_fmts:
                            bitrate_strs = [f"{round(f['vbr'])} kbps" for f in matching_fmts]
                            label_key = "Video Bitrate" if len(bitrate_strs) == 1 else "Video Bitrates"
                            self.signals.append_output.emit(f"{label_key}: {' | '.join(bitrate_strs)}")
                    except Exception:
                        pass

                # Codecs availability
                available_codecs = set()
                for f in all_formats:
                    vc = (f.get("vcodec") or "").lower()
                    if vc and vc != "none":
                        if vc.startswith("avc1"):
                            available_codecs.add("H264")
                        elif vc.startswith("vp9") or vc.startswith("vp09"):
                            available_codecs.add("VP9")
                        elif vc.startswith("av01"):
                            available_codecs.add("AV1")
                
                if available_codecs:
                    codec_order = {"H264": 1, "VP9": 2, "AV1": 3}
                    sorted_codecs = sorted(list(available_codecs), key=lambda x: codec_order.get(x, 99))
                    self.signals.append_output.emit(f"Video Codecs: {' | '.join(sorted_codecs)}")

                # Audio Codecs availability
                available_audio = set()
                for f in all_formats:
                    ac = (f.get("acodec") or "").lower()
                    if ac and ac != "none":
                        if ac.startswith("mp4a"):
                            available_audio.add("AAC")
                        elif ac.startswith("opus"):
                            available_audio.add("Opus")
                        elif ac.startswith("vorbis"):
                            available_audio.add("Vorbis")
                        elif ac.startswith("mp3"):
                            available_audio.add("MP3")
                
                if available_audio:
                    audio_order = {"AAC": 1, "Opus": 2, "MP3": 3, "Vorbis": 4}
                    sorted_audio = sorted(list(available_audio), key=lambda x: audio_order.get(x, 99))
                    self.signals.append_output.emit(f"Audio Codecs: {' | '.join(sorted_audio)}")

                    # Subtitles availability analysis
                    available_subs = {}
                    try:
                        # subs_dict and autos_dict are already extracted from json_data above

                        # Filter automatic captions to only include ORIGINAL ones (not auto-translations)
                        original_autos = {}
                        for lang_code, formats in autos_dict.items():
                            if not formats:
                                continue
                            
                            # Original auto-captions don't have "tlang=" in their URL.
                            # We check the URL of the first format.
                            url = formats[0].get("url", "")
                            # Also check for lang matches if possible
                            if "tlang=" not in url:
                                # Standardize the key - sometimes YouTube provides 'en-orig' or 'en'
                                base = lang_code.split("-")[0].lower()
                                original_autos[base] = formats

                        # We check for our 3 target languages for the UI checkboxes
                        target_langs = [("English", "en"), ("German", "de"), ("Spanish", "es")]
                        for name, code in target_langs:
                            if any(k.split("-")[0].lower() == code for k in subs_dict.keys()):
                                available_subs[code] = "real"
                            elif code in original_autos:
                                available_subs[code] = "auto"

                        # Build the full report for the output log as requested
                        # Use sorted union of manual keys and original auto keys
                        all_langs = sorted(list(set([k.split("-")[0] for k in list(subs_dict.keys()) + list(original_autos.keys())])))
                        report_tokens = []
                        
                        for lang_code in all_langs:
                            # Check manual
                            if any(k.startswith(lang_code) for k in subs_dict.keys()):
                                report_tokens.append(f"{lang_code} (real)")
                            # Check original auto
                            if any(k.startswith(lang_code) for k in original_autos.keys()):
                                report_tokens.append(f"{lang_code} (auto)")

                        if report_tokens:
                            self.signals.append_output.emit(f"Subtitles: {', '.join(report_tokens)}")
                        else:
                            self.signals.append_output.emit("Subtitles: None available")

                        # Cache availability BEFORE emitting, so the
                        # main-thread slot always reads the fresh data
                        self.video_state["available_subtitles"] = available_subs
                        # Update checkbox labels in UI
                        self.signals.update_subtitle_checkboxes.emit(language or "")

                    except (json.JSONDecodeError, Exception) as e:
                        print(f"Error parsing subtitle info: {e}")

                    if language:
                        # Normalize language (e.g., 'en-US' -> 'en')
                        base_lang = language.split("-")[0]
                        # optional: Check the detected language
                        # self.signals.update_subtitle_checkboxes.emit(base_lang)
                    # self.signals.append_output.emit(f"Language: {language}")

                    # Set title (without channel name - channel is added to folder name only)
                    if title:
                        # Special Case: PowerfulJRE (Joe Rogan)
                        if youtube_channel == "PowerfulJRE":
                            # Look for episode number: e.g. "#2464" or " 2464"
                            ep_match = re.search(r"(?:#| )(\d+)(?: -| |$)", title)
                            if ep_match:
                                try:
                                    ep_num = int(ep_match.group(1))
                                    # We pad to 4 digits to match the general S##E#### format
                                    short_date = f"S01E{ep_num:04d}"
                                    self.video_state["episode_code"] = short_date
                                    # Remove episode number from title (e.g. "Joe Rogan Experience #2467 - Michael Pollan" -> "Joe Rogan Experience - Michael Pollan")
                                    match_str = ep_match.group(0)
                                    title = title.replace(match_str, " - ")
                                    # Clean up title if we introduced double dash or extra space
                                    title = re.sub(r"\s+", " ", title).replace(" - - ", " - ").strip().strip("-").strip()
                                except (ValueError, IndexError):
                                    pass

                        # Special Case: Shawn Ryan Show
                        elif youtube_channel == "Shawn Ryan Show":
                            # Look for SRS episode number: e.g. "| SRS #285" or "SRS #285"
                            ep_match = re.search(r"(?:[|]\s*)?SRS\s*#?\s*(\d+)", title)
                            if ep_match:
                                try:
                                    ep_num = int(ep_match.group(1))
                                    # User requested no offset for SRS: e.g. 285 becomes S01E0285
                                    short_date = f"S01E{ep_num:04d}"
                                    self.video_state["episode_code"] = short_date
                                    # Remove episode tag from title
                                    match_str = ep_match.group(0)
                                    title = title.replace(match_str, "")
                                    # Clean up title
                                    title = re.sub(r"\s+", " ", title).replace(" - - ", " - ").strip().strip("-").strip().strip("|").strip()
                                except (ValueError, IndexError):
                                    pass

                        # Special Case: Lex Fridman
                        elif youtube_channel == "Lex Fridman":
                            # Look for episode number: e.g. "| Lex Fridman Podcast #491"
                            ep_match = re.search(r"(?:[|]\s*)?Lex Fridman Podcast\s*#?\s*(\d+)", title)
                            if ep_match:
                                try:
                                    ep_num = int(ep_match.group(1))
                                    # Lex Fridman: e.g. 491 becomes S01E0491
                                    short_date = f"S01E{ep_num:04d}"
                                    self.video_state["episode_code"] = short_date
                                    # Remove episode tag from title
                                    match_str = ep_match.group(0)
                                    title = title.replace(match_str, "")
                                    # Clean up title
                                    title = re.sub(r"\s+", " ", title).replace(" - - ", " - ").strip().strip("-").strip().strip("|").strip()
                                except (ValueError, IndexError):
                                    pass

                        # Special Case: PBD Podcast
                        elif youtube_channel == "PBD Podcast":
                            # Look for episode number: e.g. "| PBD #754" or "| PBD Podcast #754"
                            ep_match = re.search(r"(?:[|]\s*)?PBD(?: Podcast)?\s*#?\s*(\d+)", title)
                            if ep_match:
                                try:
                                    ep_num = int(ep_match.group(1))
                                    # PBD Podcast: e.g. 754 becomes S01E0754
                                    short_date = f"S01E{ep_num:04d}"
                                    self.video_state["episode_code"] = short_date
                                    # Remove episode tag from title
                                    match_str = ep_match.group(0)
                                    title = title.replace(match_str, "")
                                    # Clean up title
                                    title = re.sub(r"\s+", " ", title).replace(" - - ", " - ").strip().strip("-").strip().strip("|").strip()
                                except (ValueError, IndexError):
                                    pass

                        sanitized_title = sanitize_title(title)
                        full_title = short_date + " - " + sanitized_title
                        self.signals.update_title.emit(full_title)

                        # Fetch SponsorBlock segments
                        self.check_sponsorblock(url)
                    else:
                        self.signals.append_output.emit("🚩 Could not find title")
                        error_status["error"] = True
                else:
                    self.signals.append_output.emit(
                        f"🚩 Could not parse the result from yt-dlp --print (parts: {len(parts)})"
                    )
                    error_status["error"] = True

            else:
                error_status["error"] = True

        except subprocess.TimeoutExpired:
            self.signals.append_output.emit("👉 Timeout fetching video info")
            error_status["error"] = True
        except Exception as e:
            self.signals.append_output.emit(f"🚩 Error fetching video info: {e}")
            error_status["error"] = True

        self.signals.title_fetch_complete.emit(error_status)

    # ----------------------------------------------------------------------------------------------------
    # Finished Title fetch thread
    # ----------------------------------------------------------------------------------------------------
    def title_fetch_finished(self, result):
        # Reset state variable
        self.video_state["is_fetching_info"] = False
        # Restore Download button label after metadata fetch completes (success or failure)
        self.download_button.setText("Download")
        self.set_download_button_status("")
        self._reset_download_progress_bars()
        self._set_download_busy(False)
        # Re-enable all primary controls
        self._set_ui_enabled_state(True)
        # Ensure download button state is correct based on result
        if result.get("error"):
            self.download_button.setEnabled(False)
        else:
            self.thumbnail_button.setEnabled(True)

    # ----------------------------------------------------------------------------------------------------
    # Get selected SponsorBlock categories from checkboxes
    # ----------------------------------------------------------------------------------------------------
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

    # ----------------------------------------------------------------------------------------------------
    # Check for SponsorBlock segments
    # ----------------------------------------------------------------------------------------------------
    def _get_description_summary(
        self, description: str, max_chars: int = 220, min_words: int = 10
    ) -> str:
        """
        Return a human-friendly short summary of the description.

        Previous behavior used `split(".", 1)` which breaks on abbreviations like "Dr.".
        This tries to find the first real sentence boundary, but requires at least
        `min_words` words before accepting a sentence end. Also ignores a short list of
        common abbreviations. Falls back to a soft character/word preview.
        """
        if not description:
            return ""

        text = " ".join(description.strip().split())  # collapse whitespace/newlines
        if not text:
            return ""

        # Common abbreviations that frequently appear at the start of a sentence.
        # This is intentionally small and can be extended if we see more false splits.
        abbreviations = {
            "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st",
            "vs", "etc", "e.g", "i.e",
        }

        # Scan for the first likely end-of-sentence punctuation, but only accept
        # it once we've accumulated at least `min_words` words.
        boundary_idx = None
        for i, ch in enumerate(text):
            if ch not in ".!?":
                continue

            # Require a following space (or end-of-string) to look like a sentence boundary.
            next_char = text[i + 1] if i + 1 < len(text) else ""
            if next_char not in ("", " "):
                continue

            # Get the token immediately before the punctuation (e.g. "Dr" from "Dr.")
            before = text[:i].rstrip()
            last_token = before.split(" ")[-1] if before else ""
            token_key = last_token.lower().strip("()[]{}\"'“”‘’.,:;")
            if token_key in abbreviations:
                continue

            # Minimum-words rule: don't allow extremely short "sentences" like "Dr."
            if len(before.split()) < min_words:
                continue

            boundary_idx = i + 1
            break

        if boundary_idx:
            summary = text[:boundary_idx].strip()
        else:
            # Fallback: take a preview that contains at least `min_words` words, while
            # still preferring a soft character limit.
            words = text.split()
            if len(words) <= min_words:
                summary = text
            else:
                preview = ""
                for w in words:
                    candidate = (preview + " " + w).strip()
                    # Always allow growth until we reach min_words
                    if len(candidate.split()) <= min_words:
                        preview = candidate
                        continue
                    # After min_words, keep growing only if we stay within max_chars
                    if len(candidate) <= max_chars:
                        preview = candidate
                    else:
                        break
                summary = preview.strip()

        return summary

    def _emit_description_summary(self):
        description = (self.video_state.get("description") or "").strip()
        if not description:
            return

        summary = self._get_description_summary(description)
        if summary:
            # Keep an empty line above the description and force a "..." ending.
            self.signals.append_output.emit(f"\nDescription: {summary}...")

    def check_sponsorblock(self, url):
        try:
            # Get the categories
            # Get all possible categories to show everything in the visual bar
            all_categories = [
                "sponsor", "selfpromo", "interaction", "intro", "outro", 
                "preview", "music_offtopic", "filler", "poi_highlight", 
                "exclusive_access", "chapter"
            ]
            # video_id already extracted in fetch_video_info
            video_id = self.video_state["video_id"]
            if not video_id:
                self.signals.append_output.emit(
                    "SponsorBlock: Could not extract video ID from URL"
                )
                self._emit_description_summary()
                return

            # Build the API URL with query parameters
            api_url = "https://sponsor.ajay.app/api/skipSegments"
            payload = {"videoID": video_id, "category": all_categories}
            max_attempts = 3
            try:
                response = None
                for attempt in range(1, max_attempts + 1):
                    response = requests.get(api_url, params=payload, timeout=15)
                    # Retry on 5xx server errors
                    if response.status_code >= 500 and attempt < max_attempts:
                        self.signals.append_output.emit(
                            f"👉 SponsorBlock: Server error {response.status_code}, retrying ({attempt}/{max_attempts - 1})..."
                        )
                        time.sleep(2)
                        continue
                    break

                if response is None:
                    return

                if response.status_code == 200:
                    segments = response.json()
                    if segments and len(segments) > 0:
                        # Count segments by category for the *returned* segments
                        category_counts = {}
                        for segment in segments:
                            category = segment.get("category", "unknown")
                            category_counts[category] = (
                                category_counts.get(category, 0) + 1
                            )
                        # Format the output to show the segments found
                        segments_info = ", ".join(
                            [
                                f"{count} {SB_DISPLAY_NAMES.get(cat, cat.capitalize())}"
                                for cat, count in sorted(category_counts.items())
                            ]
                        )
                        total = len(segments)
                        self.signals.append_output.emit(
                            f"📟 SponsorBlock: {total} segment(s) available ({segments_info})"
                        )
                        # Update visual bar
                        duration = self.video_state.get("duration_sec", 0)
                        self.signals.update_sb_bar.emit(segments, float(duration))
                    else:
                        self.signals.append_output.emit(
                            "📟 SponsorBlock: No segments available"
                        )
                        self.signals.update_sb_bar.emit([], 0)
                elif response.status_code == 404:
                    self.signals.append_output.emit(
                        "📟 SponsorBlock: No segments available"
                    )
                    self.signals.update_sb_bar.emit([], 0)
                else:
                    self.signals.append_output.emit(
                        f"🚩 SponsorBlock: API returned status {response.status_code}"
                    )
            except requests.exceptions.RequestException as e:
                self.signals.append_output.emit(
                    f"🚩 SponsorBlock: Request error: {str(e)}"
                )
            finally:
                self._emit_description_summary()
        except Exception as e:
            self.signals.append_output.emit(
                f"🚩 SponsorBlock: Failed to check segments: {str(e)}"
            )
            self._emit_description_summary()

    # ----------------------------------------------------------------------------------------------------
    # Set Title text
    # ----------------------------------------------------------------------------------------------------
    def set_title_label(self, text):
        self.title_entry.setText(text)
        # Update video state
        self.update_video_state(title=text)

    # ----------------------------------------------------------------------------------------------------
    # Cleans up the title (mostly to avoid ALL-CAP strings)
    # If the title starts with a season/episode code like "S01E02", that prefix is kept uppercase.
    # The rest of the title is converted to Title Case, preserving already-lowercase words.
    # ----------------------------------------------------------------------------------------------------
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

    # ----------------------------------------------------------------------------------------------------
    # Open thumbnail in a dialog
    # ----------------------------------------------------------------------------------------------------
    def open_thumbnail_dialog(self):
        thumbnail_url = self.video_state.get("thumbnail_url", "")
        if not thumbnail_url:
            self.signals.append_output.emit("👉 No thumbnail available")
            return

        try:
            # Fetch the image
            response = requests.get(thumbnail_url, timeout=10)
            response.raise_for_status()

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
            pixmap.loadFromData(response.content)

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
            self.signals.append_output.emit(f"🚩 Error loading thumbnail: {e}")

    # ----------------------------------------------------------------------------------------------------
    # Open log file in a dialog with newest entries on top
    # ----------------------------------------------------------------------------------------------------
    def open_log_dialog(self):
        log_path = os.path.join(log_dir, "app.log")
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

        try:
            with open(log_path, "r", encoding="utf-8") as f:
                content = f.read()

            if content.strip():
                # Color result lines: red for failed, green for succeeded
                lines = content.strip().splitlines()
                colored_lines = []
                for line in lines:
                    if "Download failed" in line:
                        colored_lines.append(f'<span style="color: #ff6b6b;">{html.escape(line)}</span>')
                    elif "Download succeeded" in line:
                        colored_lines.append(f'<span style="color: #4ade80;">{html.escape(line)}</span>')
                    else:
                        colored_lines.append(html.escape(line))
                html_content = "<br>".join(colored_lines)
                text_browser.setHtml(html_content)
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

        layout.addWidget(text_browser)

        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)

        dialog.exec()

    # ----------------------------------------------------------------------------------------------------
    # Open Preferences Dialog (Cmd+,)
    # ----------------------------------------------------------------------------------------------------
    def open_preferences_dialog(self):
        """Open a dialog to edit the preferences.json file."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Preferences")
        dialog.resize(500, 400)

        layout = QVBoxLayout(dialog)

        # Label with file path
        path_label = QLabel(f"<small>Editing: {PREFERENCES_FILE}</small>")
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
            os.makedirs(PREFERENCES_DIR, exist_ok=True)
            if os.path.exists(PREFERENCES_FILE):
                with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
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

    # ----------------------------------------------------------------------------------------------------
    # Save preferences from the dialog
    # ----------------------------------------------------------------------------------------------------
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
            os.makedirs(PREFERENCES_DIR, exist_ok=True)
            with open(PREFERENCES_FILE, "w", encoding="utf-8") as f:
                json.dump(parsed, f, indent=2, ensure_ascii=False)
                f.write("\n")

            # Reload preferences globally
            global preferences, CHANNEL_NAME_MAP
            preferences = parsed
            CHANNEL_NAME_MAP = parsed.get("channel_name_map", {})

            self.signals.append_output.emit("✅ Preferences saved successfully")
            dialog.accept()
        except json.JSONDecodeError as e:
            self.signals.append_output.emit(f"🚩 Invalid JSON: {e}")
        except (ValueError, OSError) as e:
            self.signals.append_output.emit(f"🚩 Error saving preferences: {e}")

    # ----------------------------------------------------------------------------------------------------
    # Clear Output area
    # ----------------------------------------------------------------------------------------------------
    def clear_output(self):
        self.output_text.clear()
        # Keep the header line visible even while we are fetching video info
        self.output_text.append("YT-DLP Downloader " + APP_VERSION)
        self._reset_download_progress_bars()

    # ----------------------------------------------------------------------------------------------------
    # Add text to Output area
    # ----------------------------------------------------------------------------------------------------
    def append_output_text(self, text):
        # Block signals temporarily to prevent cascading updates
        self.output_text.blockSignals(True)
        self.output_text.append(text)
        cursor = self.output_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.output_text.setTextCursor(cursor)
        self.output_text.blockSignals(False)

    # ----------------------------------------------------------------------------------------------------
    # Update of last line
    # ----------------------------------------------------------------------------------------------------
    def update_last_output_line(self, text):
        # Block signals to prevent recursive repaints
        self.output_text.blockSignals(True)
        cursor = self.output_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.select(cursor.SelectionType.LineUnderCursor)
        if cursor.selectedText().startswith("[download]"):
            cursor.insertText(text)
            # Scroll is already at end, no need to scroll again
        else:
            # Don't call append_output_text to avoid double scrolling
            self.output_text.append(text)
            cursor = self.output_text.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.output_text.setTextCursor(cursor)
        self.output_text.blockSignals(False)

    # ----------------------------------------------------------------------------------------------------
    # Use ffprobe to get media metadata
    # ----------------------------------------------------------------------------------------------------
    def get_file_metadata(self, file_path):
        metadata = {
            "duration_seconds": None,
            "duration_formatted": "N/A",
            "video_codec": None,
            "video_long_codec": None,
            "resolution": None,
            "video_bitrate": None,
            "audio_codec": None,
            "audio_long_codec": None,
            "subtitle_streams": [],
            "ffmpeg_bitrate_line": None,
            "file_size": 0,
            "file_size_formatted": "N/A",
        }
        if not self.ffprobe_bin:
            self.signals.append_output.emit(
                "🚩 Cannot analyze file: ffprobe is not installed or accessible"
            )
            return metadata

        try:
            cmd = [
                self.ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "stream=codec_name,codec_long_name,codec_type,width,height,bit_rate,tags:format=duration",
                "-of",
                "json",
                file_path,
            ]
            print(f"Running ffprobe on: {file_path}")
            # Execute ffprobe
            with subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            ) as proc:
                stdout, stderr = proc.communicate(timeout=20)
                if proc.returncode != 0:
                    raise subprocess.CalledProcessError(proc.returncode, cmd)

            data = json.loads(stdout)

            # Get file size
            if os.path.exists(file_path):
                f_size = os.path.getsize(file_path)
                metadata["file_size"] = f_size
                metadata["file_size_formatted"] = format_filesize(f_size)

            # format duration
            fmt = data.get("format", {})
            duration_seconds = fmt.get("duration")
            if duration_seconds:
                metadata["duration_seconds"] = float(duration_seconds)
                metadata["duration_formatted"] = format_duration(duration_seconds)

            # Process Stream Metadata
            for stream in data.get("streams", []):
                codec_name = stream.get("codec_name")
                codec_long_name = stream.get("codec_long_name")
                codec_type = stream.get("codec_type")
                tags = stream.get("tags", {})

                if codec_type == "video" and not metadata["video_codec"]:
                    metadata["video_codec"] = codec_name
                    metadata["video_long_codec"] = codec_long_name

                    # Extract resolution as width x height
                    width = stream.get("width")
                    height = stream.get("height")
                    if width and height:
                        metadata["resolution"] = f"{width}x{height}"

                    # Extract video bitrate (in bps -> convert to kbps)
                    bit_rate = stream.get("bit_rate")
                    
                    # Fallback for WebM/MKV: Check tags for 'BPS'
                    if not bit_rate:
                        v_tags = stream.get("tags", {})
                        # Check "BPS" or "BPS-eng" etc.
                        for tag_key, tag_val in v_tags.items():
                            if tag_key.upper().startswith("BPS"):
                                bit_rate = tag_val
                                break
                                
                    if bit_rate:
                        try:
                            metadata["video_bitrate"] = round(int(bit_rate) / 1000)
                        except (ValueError, TypeError):
                            pass

                elif codec_type == "audio" and not metadata["audio_codec"]:
                    metadata["audio_codec"] = codec_name
                    metadata["audio_long_codec"] = codec_long_name

                elif codec_type == "subtitle":
                    # Track subtitle streams
                    language = tags.get("language", "N/A")
                    title = tags.get("title", "N/A")
                    metadata["subtitle_streams"].append(
                        {"codec": codec_name, "lang": language, "title": title}
                    )

        except subprocess.CalledProcessError as e:
            self.signals.append_output.emit(f"🚩 ffprobe failed for: {os.path.basename(file_path)}")
            print(f"ffprobe error for {file_path}: {e}")
        except subprocess.TimeoutExpired:
            self.signals.append_output.emit("👉 ffprobe timed out")
        except json.JSONDecodeError:
            self.signals.append_output.emit("🚩 ffprobe returned unreadable output")
        except Exception as e:
            self.signals.append_output.emit(
                f"🚩 Unexpected error during file analysis: {e}"
            )
        finally:
            # Try to get the actual bitrate line from ffmpeg -i as a source of truth
            if self.ffmpeg_bin:
                try:
                    ffmpeg_cmd = [self.ffmpeg_bin, "-i", file_path]
                    with subprocess.Popen(
                        ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                    ) as f_proc:
                        _, f_stderr = f_proc.communicate(timeout=10)
                        for f_line in f_stderr.splitlines():
                            if "bitrate:" in f_line:
                                metadata["ffmpeg_bitrate_line"] = f_line.strip()
                                break
                except Exception:
                    pass
            # release file handles
            gc.collect()

        return metadata

    # ----------------------------------------------------------------------------------------------------
    # Create NFO file for the downloaded video
    # ----------------------------------------------------------------------------------------------------
    def create_nfo_file(self, video_path):
        if not video_path:
            return

        nfo_path = os.path.splitext(video_path)[0] + ".nfo"

        # Get metadata from state
        # Use base_filename (e.g. "S26E0110 - My Video Title") and strip the S##E#### prefix
        # so the <title> tag contains just the clean title without the episode code
        base_filename = self.video_state.get("base_filename", "")
        title = re.sub(r"^S\d+E\d+\s*-\s*", "", base_filename).strip()
        if not title:
            title = self.video_state.get("original_title", "")
        showtitle = self.video_state.get("channel", "")
        description = self.video_state.get("description", "")
        upload_date = self.video_state.get("upload_date", "")  # YYYYMMDD
        url = self.video_state.get("clean_url", "")

        season = ""
        episode = ""
        aired = ""

        # Use special-case episode code if set (e.g. S01E2463 for JRE), otherwise derive from upload_date
        episode_code = self.video_state.get("episode_code", "")
        if episode_code:
            # Parse S##E#### format
            ep_code_match = re.match(r"S(\d+)E(\d+)", episode_code)
            if ep_code_match:
                season = str(int(ep_code_match.group(1)))  # e.g. "1"
                episode = str(int(ep_code_match.group(2)))  # e.g. "2463"
            # Still set aired from upload_date if available
            if upload_date and len(upload_date) == 8:
                try:
                    dt = datetime.strptime(upload_date, "%Y%m%d")
                    aired = dt.strftime("%Y-%m-%d")
                except ValueError:
                    aired = upload_date
        elif upload_date and len(upload_date) == 8:
            season = upload_date[2:4]
            episode = upload_date[4:8]
            try:
                dt = datetime.strptime(upload_date, "%Y%m%d")
                aired = dt.strftime("%Y-%m-%d")
            except ValueError:
                aired = upload_date  # Fallback

        # XML Content
        nfo_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<episodedetails>
    <title>{html.escape(title)}</title>
    <showtitle>{html.escape(showtitle)}</showtitle>
    <season>{season}</season>
    <episode>{episode}</episode>
    <plot>{html.escape(description)}</plot>
    <aired>{aired}</aired>
    <url>{html.escape(url)}</url>
</episodedetails>
"""
        try:
            with open(nfo_path, "w", encoding="utf-8") as f:
                f.write(nfo_content)
            self.signals.append_output.emit(f"🪪 Created NFO file: {os.path.basename(nfo_path)}")
        except Exception as e:
            self.signals.append_output.emit(f"🚩 Error creating NFO file: {e}")

    # ----------------------------------------------------------------------------------------------------
    # Download busy spinner + progress bars (no indeterminate bar animation)
    # ----------------------------------------------------------------------------------------------------
    def _set_progress_bars_visible(self, visible: bool):
        self.video_progress_bar.setVisible(visible)
        self.audio_progress_bar.setVisible(visible)

    def _set_download_busy(self, busy: bool):
        if busy:
            self.download_busy_indicator.start()
        else:
            self.download_busy_indicator.stop()

    def _reset_download_progress_bars(self):
        max_val = DownloadProgressManager.PROGRESS_MAX
        self._last_progress_values = (0, 0)
        self.video_progress_bar.setRange(0, max_val)
        self.audio_progress_bar.setRange(0, max_val)
        self.video_progress_bar.setValue(0)
        self.audio_progress_bar.setValue(0)
        self._set_progress_bars_visible(False)

    def _set_download_progress(self, video_value: int, audio_value: int):
        self._last_progress_values = (video_value, audio_value)
        if not self.video_progress_bar.isVisible():
            self._set_progress_bars_visible(True)
        self.video_progress_bar.setValue(video_value)
        self.audio_progress_bar.setValue(audio_value)

    def handle_indeterminate_state(self, is_busy: bool):
        self._set_download_busy(is_busy)
        # Do NOT force-hide progress bars here.
        # We use the spinner both:
        # 1) before the first download progress arrives, and
        # 2) during post-processing steps (after audio download), where keeping the
        #    progress bars visible is useful context.

    # ----------------------------------------------------------------------------------------------------
    # Start download
    # ----------------------------------------------------------------------------------------------------
    def start_download(self):
        # Reset label when a new download is initiated
        self.download_button.setText("Download")
        self.set_download_button_status("")
        # Get base filename/folder from title
        base_name = self.title_entry.text().strip()
        if not base_name:
            self.signals.append_output.emit("🚩 Error: No video title available")
            return
            
        # Base directory from settings
        root_output_dir = self.output_dir_entry.text().strip() or DEFAULT_OUTPUT_DIR
        
        # Create folder structure: Root / Channel / SxxEyyyy - Video Title
        # Files inside: "SxxEyyyy - Video Title"
        channel = self.video_state.get("channel", "")
        sanitized_base_name = sanitize_title(base_name)
        
        if channel:
            sanitized_channel = sanitize_title(channel)
            # Create nested folder structure: {root}/{channel}/{base_name}
            video_dir = os.path.join(root_output_dir, sanitized_channel, sanitized_base_name)
        else:
            video_dir = os.path.join(root_output_dir, sanitized_base_name)

        if not os.path.exists(video_dir):
            try:
                os.makedirs(video_dir, exist_ok=True)
                rel_path = os.path.relpath(video_dir, root_output_dir)
                self.signals.append_output.emit(f"📂 Created directory: {rel_path}")
            except Exception as e:
                self.signals.append_output.emit(f"🚩 Error creating directory: {e}")
                return

        # Update video state — filename is just base_name (without channel prefix)
        self.update_video_state(title=base_name, output_dir=video_dir)
        self.video_state["is_download_running"] = True

        # Detailed Existence Check
        media_type = self.video_state.get("media_type")
        selected_langs = self.get_selected_subtitle_codes()
        base_path = self.get_full_path()
        
        # 1. Check Media (Video/Audio)
        media_exists = False
        media_file = ""
        if media_type in ["video", "video_only"]:
            for ext in [".mp4", ".mkv", ".webm"]:
                if os.path.exists(base_path + ext):
                    media_exists = True
                    media_file = os.path.basename(base_path + ext)
                    break
        elif media_type == "audio":
            for ext in [".mp3", ".m4a", ".wav", ".opus"]:
                if os.path.exists(base_path + ext):
                    media_exists = True
                    media_file = os.path.basename(base_path + ext)
                    break
        
        # 2. Check Subtitles
        missing_subs = []
        existing_subs = []
        for code in selected_langs:
            # Check both manual (.en.srt) and auto-generated (.a.en.srt) paths
            p1 = self.get_full_path(f".{code}.srt")
            p2 = self.get_full_path(f".a.{code}.srt")
            if os.path.exists(p1) or os.path.exists(p2):
                existing_subs.append(code)
            else:
                missing_subs.append(code)

        # 3. Output Detailed Status
        if media_file:
            self.signals.append_output.emit(f"✓ Media exists: {media_file}")
        
        for code in existing_subs:
            p1 = self.get_full_path(f".{code}.srt")
            p2 = self.get_full_path(f".a.{code}.srt")
            if os.path.exists(p1):
                self.signals.append_output.emit(f"✓ Subtitle exists: {os.path.basename(p1)}")
            elif os.path.exists(p2):
                self.signals.append_output.emit(f"✓ Subtitle exists: {os.path.basename(p2)}")

        # 4. Decide if we skip
        should_skip = False
        skip_reason = ""
        
        if media_type == "subtitles":
            if not selected_langs:
                self.signals.append_output.emit("👉 No subtitle languages selected. Please check at least one language.")
                self.video_state["is_download_running"] = False
                return
            
            if not missing_subs:
                should_skip = True
                skip_reason = "All requested subtitles already exist"
        else:
            # Video or Audio requested
            # Skip only if the media file exists AND all requested subtitles are already there
            if media_exists and not missing_subs:
                should_skip = True
                skip_reason = "All requested files (media and subtitles) already exist"

        if should_skip:
            self.signals.append_output.emit(f"👉 {skip_reason} - Download skipped")
            self.video_state["is_download_running"] = False
            return

        selected_langs = self.get_selected_subtitle_codes()
        cmd = self.build_command(selected_langs)
        if not cmd:
            self.signals.append_output.emit("👉 Please enter a valid YouTube URL")
            self.video_state["is_download_running"] = False
            self.url_entry.setFocus()
            return

        # Log the download start
        channel = self.video_state.get("channel", "")
        url = self.video_state.get("clean_url", "")
        logger.info(f"{channel}")
        logger.info(f"{base_name}")
        logger.info(f"{url}")

        # Clear cached metadata at start of new download
        self.cached_video_metadata = None

        self.signals.update_dock_tile.emit("")
        self.clearDockProgress()
        self._set_ui_enabled_state(False)
        self.download_button.setEnabled(False)
        self._reset_download_progress_bars()
        self._set_download_busy(True)

        self.signals.append_output.emit(
            f"\n🖥️ {' '.join(str(item) for item in cmd if item)}"
        )

        selected_langs = self.get_selected_subtitle_codes()
        thread = threading.Thread(target=self.run_download, args=(cmd, selected_langs))
        thread.daemon = True
        thread.start()

    # ----------------------------------------------------------------------------------------------------
    # Build Command for yt-dlp
    # ----------------------------------------------------------------------------------------------------
    def build_command(self, selected_langs=None):
        url = self.get_clean_url()
        if not url:
            return None

        # Get the user-edited title for filename
        custom_title = self.title_entry.text().strip()
        if not custom_title:
            custom_title = "downloaded_video"

        # Update state
        self.update_video_state(title=custom_title)

        cmd = [self.yt_dlp_bin]

        # Dependency Paths
        if self.ffmpeg_bin and shutil.which(self.ffmpeg_bin):
            ffmpeg_dir = os.path.dirname(self.ffmpeg_bin)
            cmd.extend(["--ffmpeg-location", ffmpeg_dir])

        if self.deno_bin and shutil.which(self.deno_bin):
            cmd.extend(["--js-runtimes", f"deno:{self.deno_bin}"])

        # Media Type and Format Logic
        media_type = self.video_state["media_type"]
        quality = self.video_state["quality"]
        video_fmt = self.video_state["video_format"]
        audio_fmt = self.video_state["audio_format"]
        video_codec = self.video_state["video_codec"]

        if media_type == "audio":
            if audio_fmt == "best":
                cmd.extend(["-f", "bestaudio/best"])
            else:
                postprocessor_format = "aac" if audio_fmt == "m4a" else audio_fmt
                cmd.extend(
                    [
                        "-x",
                        "--audio-format",
                        postprocessor_format,
                        "--audio-quality",
                        "0",
                    ]
                )
        elif media_type in ["video", "video_only"]:
            # Build common video filters
            format_parts = [
                f"[height<={int(quality)}]" if quality != "best" else "",
                f"[vcodec*={video_codec}]" if video_codec != "best" else "",
            ]
            video_format_filter = "".join(p for p in format_parts if p)

            if media_type == "video":
                format_string = f"bestvideo{video_format_filter}+bestaudio/best"
                cmd.extend(["-f", format_string])
                if video_fmt != "best":
                    cmd.extend(["--merge-output-format", video_fmt])

            elif media_type == "video_only":
                format_string = f"bestvideo{video_format_filter}/best"
                cmd.extend(["-f", format_string])
                if video_fmt != "best":
                    cmd.extend(["--recode-video", video_fmt])

        elif media_type == "subtitles":
            cmd.extend(["--skip-download"])

        # SponsorBlock Integration
        sb_categories = []
        if self.sb_all_checkbox.isChecked():
            sb_categories = ["all"]
        else:
            sb_categories = [
                category
                for checkbox, category in self.sb_checkbox_map.items()
                if checkbox.isChecked()
            ]

        if sb_categories:
            cmd.extend(["--sponsorblock-mark", "all"])

        # Final parameters - use helper method
        cmd.extend(["--add-metadata"])
        cmd.extend(["-o", self.get_filename_template()])
        cmd.extend(["--no-playlist", "-N", "8"])
        cmd.extend(["--write-info-json"])
        # Subtitles Integration
        if selected_langs:
            lang_arg = ",".join([f"{lg},a.{lg}" for lg in selected_langs])
            cmd.extend(
                [
                    "--write-subs",
                    "--write-auto-subs",
                    "--sub-langs",
                    lang_arg,
                    "--sub-format",
                    "srt",
                    "--convert-subs",
                    "srt",
                ]
            )

        # cmd.extend(["--extractor-args", "youtube:player_client=default,ios"])
        cmd.append(url)
        return cmd

    # ----------------------------------------------------------------------------------------------------
    # Run download process
    # ----------------------------------------------------------------------------------------------------
    def run_download(self, cmd, selected_langs):
        success = False
        had_download_progress = False
        try:
            if self.simulate_download_error:
                raise RuntimeError("Simulated download error (testing flag enabled)")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
            )

            progress_manager = DownloadProgressManager(
                media_type=self.video_state.get("media_type", "video")
            )
            state = {
                "last_line_was_progress": False,
                "merged_filename": None,
                "downloading_subtitles": False,
                "progress_manager": progress_manager,
            }

            if process.stdout is None:
                return

            self.signals.set_indeterminate.emit(True)

            # Main Output Loop
            for line in process.stdout:
                # yt-dlp separates progress updates with carriage returns;
                # universal newlines already splits them, but strip any
                # leading "\r" so the line reliably starts with "[download]".
                line = line.lstrip("\r").rstrip()
                state = self._parse_download_output(line, state)

            process.wait()
            exit_code = process.returncode
            had_download_progress = progress_manager.is_started()
            if had_download_progress:
                video_done, audio_done = progress_manager.mark_complete()
                self.signals.update_download_progress.emit(video_done, audio_done)

            if exit_code == 0:
                # Post-processing phase: keep the busy spinner visible while we
                # finalize files (subtitles, EDL/NFO, cleanup, analysis, etc.).
                self.signals.set_indeterminate.emit(True)

                media_type = self.video_state.get("media_type")
                if media_type == "subtitles":
                    self.signals.append_output.emit("Subtitle download complete.")
                else:
                    self.signals.append_output.emit("Video/Audio downloaded.")

                # Update video state with final filename
                if state["merged_filename"]:
                    self.video_state["full_path"] = state["merged_filename"]

                # Cache metadata immediately after download
                if state["merged_filename"]:
                    self.cached_video_metadata = self.get_file_metadata(
                        state["merged_filename"]
                    )

                # Process subtitles (now downloaded together with video)
                if selected_langs:
                    self.signals.append_output.emit("\nProcessing subtitles...")
                    # Identify which languages were actually downloaded (checking both manual and auto-subs)
                    downloaded_subs = []
                    available_info = self.video_state.get("available_subtitles", {})
                    
                    for lang in selected_langs:
                        p1 = self.get_full_path(extension=f".{lang}.srt")
                        p2 = self.get_full_path(extension=f".a.{lang}.srt")

                        if os.path.exists(p1):
                            # Use metadata to distinguish, but default to "real" if standard suffix exists
                            # unless we explicitly know it's an "auto" type video.
                            sub_type = available_info.get(lang, "real")
                            downloaded_subs.append((lang, p1, sub_type))
                        elif os.path.exists(p2):
                            # Files with .a. suffix are always auto
                            downloaded_subs.append((lang, p2, "auto"))

                    if downloaded_subs:
                        report_langs = [f"{item[0]} ({item[2]})" for item in downloaded_subs]
                        self.video_state["downloaded_subtitles"] = [item[0] for item in downloaded_subs]
                        
                        self.signals.append_output.emit(
                            f"💬 Subtitles identified: {', '.join(report_langs)}"
                        )

                        # Process subtitles: merge auto-generated ones for 2-line display,
                        # but leave manual (real) subtitles untouched as they are already optimized.
                        for lang, srt_path, sub_type in downloaded_subs:
                            if sub_type == "real":
                                self.signals.append_output.emit(f"  → {lang} (real): keeping original format")
                            else:
                                self.signals.append_output.emit(f"  → {lang} (auto): merging into 2-line format")
                                self._resync_subtitle_for_language(lang, srt_path, [])
                    else:
                        self.signals.append_output.emit("👉 No subtitles were downloaded.")

                # Create EDL and NFO files BEFORE cleanup deletes the .info.json
                # Skip if we only downloaded subtitles
                if state["merged_filename"] and self.video_state.get("media_type") != "subtitles":
                    removed_sb_segments = self.create_edl_file(state["merged_filename"])
                    self.create_nfo_file(state["merged_filename"])

                # Cleanup (deletes .info.json and original subtitles)
                self.cleanup_files(selected_langs)
                # Fallback: if filename wasn't captured by regex, try to find it
                if not state["merged_filename"]:
                    base_path = self.get_full_path()
                    # Check for common extensions
                    for ext in [".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".opus"]:
                        if os.path.exists(base_path + ext):
                            state["merged_filename"] = base_path + ext
                            print(f"Fallback found file: {state['merged_filename']}")
                            break

                # Post Download Analysis (uses cached metadata)
                # Skip if we only downloaded subtitles (as no new media was created)
                if state["merged_filename"] and self.video_state.get("media_type") != "subtitles":
                    self.signals.append_output.emit(f"🔍 Analyzing: {os.path.basename(state['merged_filename'])}")
                    self._analyze_downloaded_file(state["merged_filename"])
                elif not state["merged_filename"] and self.video_state.get("media_type") != "subtitles":
                    self.signals.append_output.emit("🚩 Could not find filename for analysis")
                # all done
                self.signals.append_output.emit("\n✅ Download finished.")
                success = True
            else:
                self.signals.append_output.emit(
                    f"🚩 yt-dlp process failed with exit code {exit_code}"
                )

        except Exception as e:
            self.signals.append_output.emit(f"🚩 An unexpected error occurred: {e}")
            logger.error(f"Download failed with exception: {e}")
        finally:
            # Log SponsorBlock segments separately if available
            if success and 'removed_sb_segments' in locals() and removed_sb_segments:
                segment_info = []
                for seg in removed_sb_segments:
                    category = seg.get('category', 'unknown')
                    start = seg.get('start', 0)
                    end = seg.get('end', 0)
                    segment_info.append(f"{category} ({start:.1f}s-{end:.1f}s)")
                logger.info(f"SponsorBlock removed: {', '.join(segment_info)}")
            
            # Log the result
            logger.info(f"Download {'succeeded' if success else 'failed'}")

            # Clean up state and UI in a single batch to avoid cascading updates
            self.video_state["is_download_running"] = False
            # Subtitle transfers never touch the bars, so for subtitles-only
            # runs the bars are filled once here at the end (on success).
            fill_progress_bars = had_download_progress or (
                success and self.video_state.get("media_type") == "subtitles"
            )
            if fill_progress_bars:
                self.signals.update_download_progress.emit(
                    DownloadProgressManager.PROGRESS_MAX,
                    DownloadProgressManager.PROGRESS_MAX,
                )
            self.signals.set_indeterminate.emit(False)
            self.signals.set_download_button_label.emit(
                "Download Successful ✅" if success else "Download Error 🚨"
            )
            self.signals.set_download_button_status.emit(
                "success" if success else "error"
            )
            self.signals.enable_button.emit()
            self.signals.update_dock_tile.emit("✓" if success else "!")
            self.clearDockProgress()



    # ----------------------------------------------------------------------------------------------------
    # Cleanup temporary files
    # ----------------------------------------------------------------------------------------------------
    def cleanup_files(self, selected_langs):
        # Clean up JSON
        json_file = self.get_full_path(".info.json")
        if os.path.exists(json_file):
            try:
                os.remove(json_file)
            except OSError:
                print("Error: Could not remove json file.")
                pass

        # Clean up original/auto-generated files
        for code in selected_langs:
            # We always output to Title.[lang].srt.
            # So we only need to clean up Title.[lang].a.srt if it exists,
            # as it was replaced by the processed Title.[lang].srt.
            auto_srt = self.get_full_path(f".a.{code}.srt")
            if os.path.exists(auto_srt):
                try:
                    os.remove(auto_srt)
                except OSError:
                    pass

            # Also clean up any lingering .resynced or .merged files from previous versions
            for suffix in [".resynced", ".merged"]:
                old_path = self.get_full_path(f".{code}{suffix}.srt")
                if os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except OSError:
                        pass

    # ----------------------------------------------------------------------------------------------------
    # Load sponsor segments from info JSON file and verify removal
    # ----------------------------------------------------------------------------------------------------
    def _load_sponsor_segments(self, info_json_path):
        try:
            with open(info_json_path, "r", encoding="utf-8") as f:
                info = json.load(f)

            # Get SponsorBlock chapters/segments
            segments = []
            removed_categories = self.get_selected_sb_categories()

            if "sponsorblock_chapters" in info:
                print(
                    f"Found {len(info['sponsorblock_chapters'])} SponsorBlock chapters"
                )

                for i, chapter in enumerate(info["sponsorblock_chapters"], 1):
                    start = chapter.get("start_time", 0)
                    end = chapter.get("end_time", 0)
                    duration = end - start
                    categories = chapter.get("_categories", [])

                    # Handle case where categories might be nested lists or contain non-strings
                    # Categories format: [["sponsor", start, end, "Description"]]
                    flat_categories = []
                    category_names = []  # Just the category names for matching

                    if categories:
                        for cat in categories:
                            if isinstance(cat, list) and len(cat) > 0:
                                # First element is the category name
                                category_name = str(cat[0])
                                category_names.append(category_name)
                                flat_categories.extend(str(c) for c in cat)
                            else:
                                cat_str = str(cat)
                                category_names.append(cat_str)
                                flat_categories.append(cat_str)
                        category_str = ", ".join(flat_categories)
                    else:
                        category_str = "unknown"

                    print(
                        f"\nChapter {i}:, Category: {category_str}, Time: {start:.2f}s - {end:.2f}s, Duration: {duration:.2f}s"
                    )

                    # Check if this segment should have been removed
                    should_remove = any(
                        cat in removed_categories for cat in category_names
                    )
                    if should_remove:
                        print(f"Chapter removed.")
                        segments.append(
                            {
                                "start": start,
                                "end": end,
                                "category": category_names[0]
                                if category_names
                                else "unknown",
                            }
                        )
                    else:
                        print(f"Chapter not removed (category not in filter).")
            else:
                print("No sponsorblock_chapters found in info JSON.")

                # Try alternative fields
                if "chapters" in info:
                    print(
                        f"\nFound {len(info['chapters'])} regular chapters (not SponsorBlock)"
                    )
                    for i, chapter in enumerate(
                        info["chapters"][:3], 1
                    ):  # Show first 3
                        print(
                            f"  {i}. {chapter.get('title', 'Untitled')}: {chapter.get('start_time', 0):.2f}s"
                        )

            # Check video duration
            if "duration" in info:
                original_duration = info["duration"]
                total_removed = sum(seg["end"] - seg["start"] for seg in segments)
                final_duration = original_duration - total_removed

                print(f"Duration Analysis")
                print(
                    f"Original video duration: {original_duration:.2f}s ({original_duration / 60:.2f} min)"
                )
                print(
                    f"Total time removed: {total_removed:.2f}s ({total_removed / 60:.2f} min)"
                )
                print(
                    f"Final video duration: {final_duration:.2f}s ({final_duration / 60:.2f} min)"
                )

            print(f"Summary")
            self.signals.append_output.emit(
                f"Read json file: Total segments to be removed: {len(segments)}"
            )

            # Sort segments by start time
            segments.sort(key=lambda x: x["start"])
            return segments

        except FileNotFoundError:
            self.signals.append_output.emit(f"🚩 Cannot find the json file.")
            return []
        except json.JSONDecodeError:
            print(f"Error parsing JSON file: {info_json_path}")
            return []

    # ----------------------------------------------------------------------------------------------------
    # Calculate how much time to subtract from a timestamp based on removed segments
    # ----------------------------------------------------------------------------------------------------
    def _calculate_time_adjustment(self, timestamp, removed_segments):
        # Calculates the cumulative duration of all removed segments that occur BEFORE the given timestamp
        adjustment = 0

        for segment in removed_segments:
            seg_start = segment["start"]
            seg_end = segment["end"]
            seg_duration = seg_end - seg_start

            if timestamp <= seg_start:
                # Timestamp is before this segment starts, no more adjustments needed
                break
            elif timestamp >= seg_end:
                # Timestamp is after this segment ends, subtract the full segment duration
                adjustment += seg_duration
            else:
                # Timestamp falls within a removed segment
                # This shouldn't happen if segments were properly removed, but handle it
                # Subtract only the portion before the timestamp
                adjustment += timestamp - seg_start
                break

        return adjustment

    # ----------------------------------------------------------------------------------------------------
    # Build a mapping of original timestamps to adjusted timestamps
    # ----------------------------------------------------------------------------------------------------
    def create_edl_file(self, video_path):
        if not video_path:
            self.signals.append_output.emit("📟 create_edl_file called without video_path")
            return []

        json_file = self.get_full_path(".info.json")
        if not os.path.exists(json_file):
            self.signals.append_output.emit(f"📟 .info.json not found at {json_file}")
            return

        try:
            self.signals.append_output.emit(f"📟 Reading {json_file} for EDL generation...")
            with open(json_file, "r", encoding="utf-8") as f:
                info = json.load(f)

            segments = []
            removed_categories = self.get_selected_sb_categories()
            self.signals.append_output.emit(f"📟 Selected SB categories: {removed_categories}")

            if "sponsorblock_chapters" in info:
                self.signals.append_output.emit(f"📟 Found {len(info['sponsorblock_chapters'])} SB chapters")
                for chapter in info["sponsorblock_chapters"]:
                    start = chapter.get("start_time", 0)
                    end = chapter.get("end_time", 0)
                    categories = chapter.get("_categories", [])

                    flat_categories = []
                    if categories:
                        for cat in categories:
                            if isinstance(cat, list) and len(cat) > 0:
                                flat_categories.append(str(cat[0]))
                            else:
                                flat_categories.append(str(cat))

                    # Check if this segment matches selected categories
                    if any(cat in removed_categories for cat in flat_categories):
                        self.signals.append_output.emit(f"📟 Matching segment: {start} - {end} ({flat_categories})")
                        # Store the first category (main category) with the segment
                        category = flat_categories[0] if flat_categories else "unknown"
                        segments.append({"start": start, "end": end, "category": category})
            else:
                self.signals.append_output.emit("📟 No sponsorblock_chapters in info JSON")

            if not segments:
                self.signals.append_output.emit("📟 No segments to skip found in selected categories")
                return

            edl_path = os.path.splitext(video_path)[0] + ".edl"
            # Format: [start] [stop] [action]
            # Action 0 = Skip/Cut
            edl_content = "\n".join([f"{seg['start']} {seg['end']} 0" for seg in segments])

            with open(edl_path, "w", encoding="utf-8") as f:
                f.write(edl_content + "\n")

            self.signals.append_output.emit(f"🎬 Created EDL file: {os.path.basename(edl_path)}")
            return segments

        except Exception as e:
            self.signals.append_output.emit(f"🚩 Error creating EDL file: {e}")
            return []

    # ----------------------------------------------------------------------------------------------------
    # Build a mapping of original timestamps to adjusted timestamps
    # ----------------------------------------------------------------------------------------------------
    def _build_time_map(self, removed_segments, max_time):
        # creates a lookup that can be used to ensure consistent time adjustmentsacross all subtitles, preventing drift
        time_map = {}

        # Check if we have a drift correction factor
        drift_factor = (
            removed_segments[0].get("drift_factor", 1.0) if removed_segments else 1.0
        )

        # Sample every 0.1 seconds for precise mapping
        for original_time in range(0, int(max_time * 10) + 1):
            original_sec = original_time / 10.0
            adjustment = self._calculate_time_adjustment(original_sec, removed_segments)

            # Apply drift correction to the adjustment
            adjusted_sec = original_sec - (adjustment * drift_factor)
            time_map[original_sec] = adjusted_sec

        return time_map

    # ----------------------------------------------------------------------------------------------------
    # Adjust a timestamp using the pre-built time map with interpolation
    # ----------------------------------------------------------------------------------------------------
    def _adjust_timestamp_with_map(self, timestamp, time_map, removed_segments):
        # Find the closest mapped time
        rounded = round(timestamp * 10) / 10

        if rounded in time_map:
            return time_map[rounded]

        # If exact match not found, interpolate
        lower = int(timestamp * 10) / 10
        upper = lower + 0.1

        if lower in time_map and upper in time_map:
            # Linear interpolation
            ratio = (timestamp - lower) / 0.1
            adjusted = time_map[lower] + ratio * (time_map[upper] - time_map[lower])
            return adjusted

        # Fallback to direct calculation
        return timestamp - self._calculate_time_adjustment(timestamp, removed_segments)

    # ----------------------------------------------------------------------------------------------------
    # Splits a single line of text into at most two lines at the best space near the midpoint
    # ----------------------------------------------------------------------------------------------------
    def _smart_wrap(self, text, max_chars=42):
        """
        Splits a single line of text into at most two lines at the best space near the midpoint
        if the text exceeds max_chars. Returns the original text if no split is possible.
        """
        if len(text) <= max_chars:
            return text

        words = text.split()
        if len(words) < 2:
            return text

        # Target midpoint for a balanced split
        midpoint = len(text) // 2
        best_split_idx = 1  # Index of word to start second line
        min_dist = float("inf")

        current_len = 0
        for i in range(len(words) - 1):
            current_len += len(words[i])
            # The space is after words[i]. Its position is current_len.
            dist = abs(current_len - midpoint)
            if dist < min_dist:
                min_dist = dist
                best_split_idx = i + 1
            current_len += 1  # for the space

        line1 = " ".join(words[:best_split_idx])
        line2 = " ".join(words[best_split_idx:])

        return f"{line1}\n{line2}"

    # ----------------------------------------------------------------------------------------------------
    # Remove non-spoken bracketed text (e.g. [laughter], [snorts])
    # ----------------------------------------------------------------------------------------------------
    def _strip_nonspoken_brackets(self, text: str) -> str:
        """
        Remove stage directions / non-spoken tokens that commonly appear in subtitles
        inside square brackets, e.g. "[laughter]", "[snorts]".
        """
        if not text:
            return ""

        # Remove any bracketed chunks (non-greedy, no nesting support needed here)
        cleaned = re.sub(r"\[[^\[\]]*?\]", " ", text)
        # Normalize whitespace
        cleaned = " ".join(cleaned.split())
        return cleaned.strip()

    # ----------------------------------------------------------------------------------------------------
    # Subtitle display pipeline: merge choppy cues → pause/sentence boundaries → 2-line wrap
    # ----------------------------------------------------------------------------------------------------
    _SUBTITLE_SENTENCE_ABBREVS = frozenset(
        {
            "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st",
            "vs", "etc", "e.g", "i.e",
        }
    )

    def _flatten_subtitle_text(self, text: str) -> str:
        return " ".join((text or "").replace("\n", " ").split())

    def _subtitle_gap(self, cur: dict, nxt: dict) -> float:
        return (nxt.get("start") or 0) - (cur.get("end") or 0)

    def _last_sentence_boundary_index(self, text: str, min_words_before: int = 1) -> int:
        """Index immediately after the last valid sentence-ending .!? in text, or -1."""
        text = self._flatten_subtitle_text(text)
        if not text:
            return -1

        last_good = -1
        for i, ch in enumerate(text):
            if ch not in ".!?":
                continue

            next_char = text[i + 1] if i + 1 < len(text) else ""
            if next_char not in ("", " "):
                continue

            before = text[:i].rstrip()
            if len(before.split()) < min_words_before:
                continue

            last_token = before.split()[-1] if before else ""
            token_key = last_token.lower().strip("()[]{}\"'“”‘’.,:;")
            if token_key in self._SUBTITLE_SENTENCE_ABBREVS:
                continue

            last_good = i + 1

        return last_good

    def _ends_with_sentence(self, text: str) -> bool:
        flat = self._flatten_subtitle_text(text)
        boundary = self._last_sentence_boundary_index(flat)
        return boundary > 0 and not flat[boundary:].strip()

    def _merge_choppy_subtitle_cues(
        self,
        subtitles,
        max_line_chars: int = 50,
        max_gap_s: float = 1.0,
        max_duration_s: float = 6.0,
    ):
        """Pair rapid consecutive cues into one two-line subtitle event."""
        if not subtitles:
            return []

        merged = []
        i = 0
        while i < len(subtitles):
            cur = subtitles[i]
            if i + 1 < len(subtitles):
                nxt = subtitles[i + 1]
                cur_line = self._flatten_subtitle_text(cur.get("text"))
                nxt_line = self._flatten_subtitle_text(nxt.get("text"))
                gap = self._subtitle_gap(cur, nxt)
                duration = (nxt.get("end") or 0) - (cur.get("start") or 0)

                if (
                    cur_line
                    and nxt_line
                    and gap < max_gap_s
                    and duration < max_duration_s
                    and len(cur_line) < max_line_chars
                    and len(nxt_line) < max_line_chars
                ):
                    merged.append(
                        {
                            "start": cur["start"],
                            "end": nxt["end"],
                            "text": f"{cur_line}\n{nxt_line}",
                        }
                    )
                    i += 2
                    continue

            merged.append(
                {
                    "start": cur["start"],
                    "end": cur["end"],
                    "text": self._flatten_subtitle_text(cur.get("text")),
                }
            )
            i += 1

        return [s for s in merged if s.get("text")]

    def _optimize_subtitle_pause_boundaries(
        self,
        subtitles,
        pause_threshold_s: float = 1.0,
        max_tail_words: int = 3,
        min_words_keep: int = 3,
    ):
        """
        Move loose words off the end of a subtitle so pauses and sentence breaks read cleanly.

        - After a sentence end: push any trailing words to the next cue (any gap).
        - After a long pause with no sentence end: push a short tail (or whole tiny cue) forward.
        """
        if len(subtitles) < 2:
            return subtitles

        subs = [dict(s) for s in subtitles]
        i = 0
        while i < len(subs) - 1:
            cur = subs[i]
            nxt = subs[i + 1]
            gap = self._subtitle_gap(cur, nxt)

            cur_text = self._flatten_subtitle_text(cur.get("text"))
            nxt_text = self._flatten_subtitle_text(nxt.get("text"))
            if not cur_text or not nxt_text:
                i += 1
                continue

            boundary = self._last_sentence_boundary_index(cur_text)
            long_pause = gap >= pause_threshold_s

            if boundary > 0:
                before = cur_text[:boundary].strip()
                tail = cur_text[boundary:].strip()
                tail_words = tail.split()
                if tail_words and (
                    long_pause or len(tail_words) <= max_tail_words
                ):
                    if before and len(before.split()) >= min_words_keep:
                        cur_text = before
                        nxt_text = f"{tail} {nxt_text}".strip()
                    elif not before:
                        cur_text = ""
                        nxt_text = f"{tail} {nxt_text}".strip()

            elif long_pause:
                words = cur_text.split()
                if len(words) <= max_tail_words:
                    nxt_text = f"{cur_text} {nxt_text}".strip()
                    cur_text = ""
                elif len(words) > max_tail_words + min_words_keep:
                    tail = " ".join(words[-max_tail_words:])
                    keep = " ".join(words[:-max_tail_words])
                    cur_text = keep
                    nxt_text = f"{tail} {nxt_text}".strip()

            if cur_text:
                cur["text"] = cur_text
                nxt["text"] = nxt_text
                i += 1
            else:
                nxt["text"] = nxt_text
                subs.pop(i)

        return [s for s in subs if self._flatten_subtitle_text(s.get("text"))]

    def _merge_subtitle_continuations(
        self,
        subtitles,
        max_line_chars: int = 50,
        max_gap_s: float = 1.0,
        max_duration_s: float = 6.0,
    ):
        """Re-merge short continuation cues that the boundary pass left as separate one-liners."""
        if not subtitles:
            return []

        merged = []
        i = 0
        while i < len(subtitles):
            cur = subtitles[i]
            if i + 1 < len(subtitles):
                nxt = subtitles[i + 1]
                cur_line = self._flatten_subtitle_text(cur.get("text"))
                nxt_line = self._flatten_subtitle_text(nxt.get("text"))
                gap = self._subtitle_gap(cur, nxt)
                duration = (nxt.get("end") or 0) - (cur.get("start") or 0)

                if (
                    cur_line
                    and nxt_line
                    and gap < max_gap_s
                    and duration < max_duration_s
                    and len(cur_line) < max_line_chars
                    and len(nxt_line) < max_line_chars
                    and not self._ends_with_sentence(cur_line)
                ):
                    merged.append(
                        {
                            "start": cur["start"],
                            "end": nxt["end"],
                            "text": f"{cur_line}\n{nxt_line}",
                        }
                    )
                    i += 2
                    continue

            merged.append(dict(cur))
            i += 1

        return merged

    def _wrap_subtitle_text(self, text: str, max_chars: int = 50) -> str:
        flat = self._flatten_subtitle_text(text)
        if not flat:
            return ""
        if "\n" in (text or ""):
            lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
            if len(lines) == 2 and all(len(ln) <= max_chars for ln in lines):
                return f"{lines[0]}\n{lines[1]}"
        return self._smart_wrap(flat, max_chars)

    def _fix_subtitle_time_overlaps(self, subtitles):
        for i in range(len(subtitles) - 1):
            current_end = subtitles[i]["end"]
            next_start = subtitles[i + 1]["start"]
            if current_end > next_start:
                subtitles[i]["end"] = max(
                    next_start - 0.05, subtitles[i]["start"] + 0.1
                )
        return subtitles

    def _format_subtitles_for_display(self, subtitles):
        """
        Full auto-caption layout pass:
        1. Merge choppy single-line cues into two-line events
        2. Shift loose words across pauses / sentence ends
        3. Merge same-sentence continuations again
        4. Wrap long single lines and fix timestamp overlaps
        """
        if not subtitles:
            return []

        subs = self._merge_choppy_subtitle_cues(subtitles)
        subs = self._optimize_subtitle_pause_boundaries(subs)
        subs = self._merge_subtitle_continuations(subs)

        formatted = []
        for sub in subs:
            wrapped = self._wrap_subtitle_text(sub.get("text") or "")
            if wrapped:
                formatted.append({**sub, "text": wrapped})

        return self._fix_subtitle_time_overlaps(formatted)

    # ----------------------------------------------------------------------------------------------------
    # Resync subtitle for a single language
    # ----------------------------------------------------------------------------------------------------
    def _resync_subtitle_for_language(self, lang, srt_path, removed_segments):
        if not os.path.exists(srt_path):
            self.signals.append_output.emit(f"No srt file: {srt_path}")
            return

        # output_srt is just "Title.en.srt" (no "merged" or "resynced" suffix)
        output_srt = self.get_full_path(f".{lang}.srt")
        
        # If output_srt is different from srt_path (e.g. srt_path was .a.en.srt),
        # we process it into the final .en.srt.
        # If they are the same, we overwrite it (safe because resync_subtitles reads into memory).
        self.resync_subtitles(srt_path, removed_segments, output_srt)

    # ----------------------------------------------------------------------------------------------------
    # Resync subtitle file based on removed segments and merge into 2-line format
    # ----------------------------------------------------------------------------------------------------
    def resync_subtitles(self, srt_path, removed_segments, output_path):
        if not os.path.exists(srt_path):
            print(f"Subtitle file not found: {srt_path}")
            return False

        has_segments = bool(removed_segments)
        if has_segments:
            self.signals.append_output.emit("Resyncing subtitles...")

        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Split into subtitle blocks
        blocks = content.strip().split("\n\n")
        subtitles = []
        max_time = 0

        # First pass: collect all subtitles and find max time
        for block in blocks:
            lines = block.split("\n")
            if len(lines) < 3:
                continue

            # Parse timestamps
            time_line = lines[1]
            match = re.match(r"([\d:,]+)\s+-->\s+([\d:,]+)", time_line)
            if not match:
                continue

            start_str, end_str = match.groups()
            start_sec = parse_srt_time(start_str)
            end_sec = parse_srt_time(end_str)

            max_time = max(max_time, end_sec)

            # Store subtitle data with original times
            # Convert any multi-line subtitle block into a single line
            # This is crucial for the 2-line merging logic later
            raw_text = "\n".join(lines[2:])
            # Filter out ">>" artifacts often found in auto-generated captions
            clean_text = re.sub(r'^>>\s*', '', raw_text, flags=re.MULTILINE).strip()
            # Remove non-spoken tokens in square brackets, e.g. [laughter], [snorts]
            clean_text = self._strip_nonspoken_brackets(clean_text)
            # Replace all newlines and extra whitespace with a single space
            text = " ".join(clean_text.split())

            if text:
                subtitles.append(
                    {"original_start": start_sec, "original_end": end_sec, "text": text}
                )

        # Build consistent time mapping
        print(f"Building time map for {max_time:.2f}s of content...")
        time_map = self._build_time_map(removed_segments, max_time)

        # Second pass: adjust all timestamps using the consistent time map
        adjusted_subtitles = []
        for sub in subtitles:
            new_start = self._adjust_timestamp_with_map(
                sub["original_start"], time_map, removed_segments
            )
            new_end = self._adjust_timestamp_with_map(
                sub["original_end"], time_map, removed_segments
            )

            # Skip subtitles that fall entirely within removed segments or are too short/empty
            if new_start < 0 or new_end <= new_start or new_end - new_start < 0.15:
                continue

            adjusted_subtitles.append(
                {"start": new_start, "end": new_end, "text": sub["text"]}
            )

        print(
            f"Adjusted {len(adjusted_subtitles)} subtitles using consistent time mapping"
        )

        # Merge choppy cues, optimize around pauses/sentences, build 2-line display
        merged_subtitles = self._format_subtitles_for_display(adjusted_subtitles)

        # Write merged and resynced subtitles
        resynced_blocks = []
        for i, sub in enumerate(merged_subtitles, 1):
            time_line = (
                f"{format_srt_time(sub['start'])} --> {format_srt_time(sub['end'])}"
            )
            block = f"{i}\n{time_line}\n{sub['text']}"
            resynced_blocks.append(block)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(resynced_blocks))

        final_msg = (
            "Subtitles resynced and merged." if has_segments else "Subtitles merged."
        )
        self.signals.append_output.emit(f"💬 {final_msg}")
        return True

    # ----------------------------------------------------------------------------------------------------
    # Get the video duration (from ffprobe)
    # ----------------------------------------------------------------------------------------------------
    def get_video_duration(self, video_path=None):
        # Use cached metadata if available
        if self.cached_video_metadata and self.cached_video_metadata.get(
            "duration_seconds"
        ):
            return self.cached_video_metadata["duration_seconds"]

        # Only fetch if explicitly requested with a path AND cache is empty
        # This shouldn't happen in normal flow since we cache after download
        if video_path:
            print(
                f"Warning: Fetching metadata for {video_path} - cache was not available"
            )
            metadata = self.get_file_metadata(video_path)
            return metadata.get("duration_seconds")

        # If no cache and no path provided, return None
        print("Warning: get_video_duration() called without cache or video path")
        return None

    # ----------------------------------------------------------------------------------------------------
    # Verify removed segments match actual video duration and adjust if needed
    # ----------------------------------------------------------------------------------------------------
    def _verify_and_adjust_segments(
        self, removed_segments, original_duration, actual_duration, video_path
    ):
        # Use cached duration from ffprobe
        if actual_duration is None:
            actual_duration = self.get_video_duration()

        if actual_duration is None:
            print("Warning: Could not verify video duration, using segments as-is")
            return removed_segments

        total_removed = sum(seg["end"] - seg["start"] for seg in removed_segments)
        expected_duration = original_duration - total_removed
        duration_diff = abs(expected_duration - actual_duration)

        print(f"Duration Verification")
        print(f"Original duration: {original_duration:.2f}s")
        print(f"Total removed (from JSON): {total_removed:.2f}s")
        print(f"Expected final duration: {expected_duration:.2f}s")
        print(f"Actual video duration: {actual_duration:.2f}s")
        print(f"Difference: {duration_diff:.2f}s")

        if duration_diff > 1.0:
            print(f"Warning: Duration mismatch of {duration_diff:.2f}s detected.")
            print("The actual cuts may not match the info JSON perfectly.")
            print("This could cause subtitle sync issues.")

            # Try to detect if segments are offset
            # Calculate what the offset might be
            discrepancy = actual_duration - expected_duration
            print(f"\nDiscrepancy: {discrepancy:+.2f}s")

            if abs(discrepancy) > 0.5:
                print("Consider checking the SponsorBlock data accuracy.")
        else:
            print("Duration verification passed - segments appear accurate")

        # Apply drift correction factor
        # If there's a small discrepancy, apply a scaling factor to prevent accumulating errors
        if duration_diff > 0.1 and duration_diff <= 1.0:
            drift_factor = actual_duration / expected_duration
            print(f"Applying drift correction factor: {drift_factor:.6f}")

            # Adjust segment durations proportionally
            adjusted_segments = []
            for seg in removed_segments:
                adjusted_segments.append(
                    {
                        "start": seg["start"],
                        "end": seg["end"],
                        "category": seg.get("category", "unknown"),
                        "drift_factor": drift_factor,
                    }
                )
            return adjusted_segments

        return removed_segments

    # ----------------------------------------------------------------------------------------------------
    # Detect subtitle file paths in yt-dlp output (yt-dlp downloads subtitles
    # before the media streams, so subtitle transfers must be told apart)
    # ----------------------------------------------------------------------------------------------------
    def _is_subtitle_path(self, path) -> bool:
        """Return True if the given yt-dlp destination path is a subtitle file."""
        name = (path or "").strip().strip('"').lower()
        # Strip transient download suffixes (".part", ".ytdl", ".temp")
        while name.endswith((".part", ".ytdl", ".temp")):
            name = name.rsplit(".", 1)[0]
        return name.endswith(tuple(SUBTITLE_EXTENSIONS))

    # ----------------------------------------------------------------------------------------------------
    # Parse each line of yt-dlp output
    # ----------------------------------------------------------------------------------------------------
    def _parse_download_output(self, line, state):
        # A subtitle transfer only ever produces consecutive "[download]"
        # lines; any other non-empty line ends it, so later media lines are
        # accounted normally. (yt-dlp's "\r"-separated progress chunks yield
        # EMPTY lines mid-transfer, which must NOT end it.)
        if line and not line.startswith("[download]"):
            state["downloading_subtitles"] = False

        # If yt-dlp is in a post-processing phase (after downloads), show the spinner.
        # Important: avoid enabling this during the normal video→audio transition,
        # otherwise the spinner could remain visible during active download.
        progress_manager = state.get("progress_manager")
        if progress_manager and progress_manager.is_started():
            if (
                line.startswith(("[Merger]", "[ExtractAudio]", "[Fixup]"))
                or "Merging formats" in line
                or "Deleting original file" in line
            ):
                self.signals.set_indeterminate.emit(True)

        # Capture the final filename
        new_filename = None
        if m := RE_MERGE.search(line):
            new_filename = m.group(1).strip()
        elif m := RE_AUDIO.search(line):
            if self.video_state["media_type"] == "audio":
                new_filename = m.group(1).strip()
        elif m := RE_CONVERT.search(line):
            new_filename = m.group(1).strip()
        elif m := RE_ALREADY.search(line):
            # An "already downloaded" subtitle file is not the media file
            if not self._is_subtitle_path(m.group(1)):
                new_filename = m.group(1).strip()
        elif state["merged_filename"] is None:
            if m := RE_DEST.search(line):
                dest = m.group(1).strip()
                # yt-dlp downloads subtitles first; the first "[download]
                # Destination:" can therefore be a subtitle file, which must
                # not be captured as the final media filename.
                if not self._is_subtitle_path(dest):
                    new_filename = dest

        if new_filename:
            # Clean up trailing quotes if they leaked through
            new_filename = new_filename.strip('"')
            state["merged_filename"] = new_filename
            print(f"Captured final filename: {new_filename}")

        # Handle known non-progress output types
        if line.startswith(("[youtube]", "[info]", "[debug]", "[Metadata]")):
            self.signals.append_output.emit(line)
            state["last_line_was_progress"] = False

        elif RE_SLEEP.search(line):
            self.signals.append_output.emit(line)
            state["last_line_was_progress"] = False

        # Handle downloading (calls the progress update helper)
        elif line.startswith("[download]"):
            state = self._update_download_progress(line, state)
            state["last_line_was_progress"] = True

        # Handle Merging
        elif RE_MERGE.search(line):
            self.signals.append_output.emit(line)
            state["last_line_was_progress"] = False

        # Handle all other output
        else:
            self.signals.append_output.emit(line)
            state["last_line_was_progress"] = False

        return state

    # ----------------------------------------------------------------------------------------------------
    # Update the GUI progress bar based on yt-dlp output
    # ----------------------------------------------------------------------------------------------------
    def _update_download_progress(self, line, state):
        progress_manager = state["progress_manager"]
        
        # yt-dlp downloads subtitles BEFORE the media streams. Those transfers
        # are tiny and would otherwise wreck the bars: the subtitle's 100% lands
        # in the video bar, and the following media destination shifts the real
        # video download into the audio bar. Subtitle transfers are therefore
        # excluded from all progress-bar accounting (they are still logged).
        in_subtitle_transfer = state.get("downloading_subtitles", False)

        if "Destination:" in line:
            m = RE_DEST.search(line)
            in_subtitle_transfer = bool(m and self._is_subtitle_path(m.group(1)))
            state["downloading_subtitles"] = in_subtitle_transfer
            # Only real media destinations start/switch a progress stream
            if not in_subtitle_transfer:
                progress_manager.on_download_destination()
        elif m := RE_ALREADY.search(line):
            # An "already downloaded" line is a completed item on its own, so
            # it must not inherit a subtitle-transfer flag. An "already
            # downloaded" SUBTITLE must additionally not start the progress
            # accounting (it would turn the spinner off before the actual
            # media download begins).
            in_subtitle_transfer = self._is_subtitle_path(m.group(1))

        if not in_subtitle_transfer:
            if not progress_manager.is_started():
                progress_manager.mark_started()
                self.signals.set_indeterminate.emit(False)

            percent_match = re.search(r"(\d{1,3}(?:\.\d+)?)%", line)
            if percent_match:
                try:
                    percent = float(percent_match.group(1))
                    video_val, audio_val = progress_manager.update_from_ytdlp_percent(
                        percent
                    )
                    self.signals.update_download_progress.emit(video_val, audio_val)
                    self.signals.update_dock_progress.emit(
                        progress_manager.get_combined_fraction()
                    )
                except ValueError:
                    pass

        # Output line
        if state["last_line_was_progress"]:
            self.signals.update_last_line.emit(line)
        else:
            self.signals.append_output.emit(line)

        return state

    # ----------------------------------------------------------------------------------------------------
    # Analyze the downloaded file and display metadata summary
    # ----------------------------------------------------------------------------------------------------
    def _analyze_downloaded_file(self, filename):
        # Use cached metadata
        metadata = self.cached_video_metadata
        if not metadata:
            self.signals.append_output.emit("🚩 No metadata available for analysis")
            return

        # Display Metadata Summary
        self.signals.append_output.emit("\nFile info by ffprobe:")
        container = filename.split('.')[-1].upper()
        size_info = f"Container: {container}"
        if metadata.get("file_size_formatted"):
            size_info += f"  (Size: {metadata['file_size_formatted']})"
        self.signals.append_output.emit(size_info)

        if metadata["video_codec"]:
            video_info = (
                f"Video: {metadata['video_codec']} ({metadata['video_long_codec']})"
            )
            if metadata["resolution"]:
                video_info += f", {metadata['resolution']}"
            
            # Show strictly video-specific bitrate as requested
            v_bitrate = metadata["video_bitrate"]
            video_info += f", {v_bitrate} kbps" if v_bitrate else ", none"
            
            self.signals.append_output.emit(video_info)

        if metadata["audio_codec"]:
            audio_info = (
                f"Audio: {metadata['audio_codec']} ({metadata['audio_long_codec']})"
            )
            self.signals.append_output.emit(audio_info)

        if metadata["subtitle_streams"]:
            sub_list = [
                f"{s['lang']} ({s['codec']})" for s in metadata["subtitle_streams"]
            ]
            self.signals.append_output.emit(f"Subtitles: {', '.join(sub_list)}")

        if metadata.get("ffmpeg_bitrate_line"):
            self.signals.append_output.emit(metadata["ffmpeg_bitrate_line"])
        else:
            self.signals.append_output.emit(f"Duration: {metadata['duration_formatted']}")

    # ----------------------------------------------------------------------------------------------------
    # Re-enable all controls after download
    # ----------------------------------------------------------------------------------------------------
    def enable_all_controls(self):
        self._set_ui_enabled_state(True)

    def set_download_button_label(self, text: str):
        self.download_button.setText(text)

    def set_download_button_status(self, status: str):
        """
        status:
          - "error" => red styling
          - "" (empty) => default (green) styling
        """
        self.download_button.setProperty("status", status or "")
        # Force Qt to re-evaluate stylesheets after changing dynamic properties
        self.download_button.style().unpolish(self.download_button)
        self.download_button.style().polish(self.download_button)
        self.download_button.update()

    # ----------------------------------------------------------------------------------------------------
    # Set or Unset the Dock tile with Checkbox
    # ----------------------------------------------------------------------------------------------------
    def setDockTileCheck(self, label):
        if self.dockTile:
            self.dockTile.setBadgeLabel_(label)
            self.dockTile.display()

    # ----------------------------------------------------------------------------------------------------
    # Draw a progress bar onto the dock icon tile
    # ----------------------------------------------------------------------------------------------------
    def setDockProgressOverlay(self, progress: float):
        """
        Draw a progress bar onto the dock icon tile.
        progress: 0.0 – 1.0
        """
        if not self.dockTile:
            return

        size = self.dockTile.size()  # typically 128x128

        img = NSImage.alloc().initWithSize_(size)
        img.lockFocus()

        # Draw app icon as background
        app_icon = NSApplication.sharedApplication().applicationIconImage()
        if app_icon:
            app_icon.drawInRect_(NSMakeRect(0, 0, size.width, size.height))

        # Draw progress bar background (dark, at bottom)
        bar_h = size.height * 0.12
        bar_y = size.height * 0.04
        bar_rect = NSMakeRect(4, bar_y, size.width - 8, bar_h)

        #NSColor.darkGrayColor().setFill()
        NSColor.colorWithSRGBRed_green_blue_alpha_(0.2, 0.2, 0.2, 1.0).setFill()
        bg_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bar_rect, 4, 4)
        bg_path.fill()

        # Draw progress fill
        fill_w = max(0.0, min(1.0, progress)) * (size.width - 8)
        fill_rect = NSMakeRect(4, bar_y, fill_w, bar_h)
        NSColor.systemBlueColor().setFill()
        fg_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(fill_rect, 4, 4)
        fg_path.fill()

        img.unlockFocus()

        # Apply to dock tile
        image_view = NSImageView.alloc().init()
        image_view.setImage_(img)
        self.dockTile.setContentView_(image_view)
        self.dockTile.display()

    def clearDockProgress(self):
        if self.dockTile:
            self.dockTile.setContentView_(None)
            self.dockTile.display()

    # ----------------------------------------------------------------------------------------------------
    # Handle Keypress Events
    # ----------------------------------------------------------------------------------------------------
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


# Main Class End


# ----------------------------------------------------------------------------------------------------
# Get absolute path to resource
# ----------------------------------------------------------------------------------------------------
# ----------------------------------------------------------------------------------------------------
# Description / Plot Cleaning Logic (EN/DE, no AI)
# ----------------------------------------------------------------------------------------------------
@dataclass
class DescriptionSignal:
    pattern: re.Pattern
    score: float
    label: str = ""

@dataclass
class PoisonSignal:
    pattern: re.Pattern
    weight: float
    label: str = ""

def _r(pattern: str, flags=re.IGNORECASE) -> re.Pattern:
    return re.compile(pattern, flags)

CLUTTER_SIGNALS = [
    DescriptionSignal(_r(r"^\s*\d{1,2}:\d{2}(:\d{2})?[\s\-\u2013\u2014]"), -10, "timestamp"),
    DescriptionSignal(_r(r"^\s*https?://\S+\s*$"), -10, "bare_url"),
    DescriptionSignal(_r(r"^\s*(#\w+[\s,]*)+$"), -10, "hashtag_line"),
    DescriptionSignal(_r(r"^\s*[-=_*~]+\s*$"), -10, "separator"),
    DescriptionSignal(_r(r"^\s*[\w\s\-]{1,30}\s*[:→\-\u2013\u2014\u2192|]\s*\S+\s*$"), -10, "label_value_only"),
    DescriptionSignal(_r(r"^\s*(?:name|inhaber|kontoinhaber|empf.{1,4}nger|kontakt|adresse|anschrift|stra.{1,2}e|plz|ort|stadt|land|telefon|fax|gesch.{1,4}ftsf.{1,4}hrer|impressum|firma|unternehmen|contact|address|phone|recipient|account\s*holder|account\s*name)\s*[:→\-\u2013\u2014\u2192|]\s*.+\s*$"), -10, "contact_label"),
    DescriptionSignal(_r(r"(?:bc1p?[q-z02-9a-km-z]{6,87}|[13][1-9A-HJ-NP-Za-km-z]{24,33}|0x[0-9a-fA-F]{40}|ltc1[q-z02-9a-km-z]{6,87}|[LM][a-km-zA-HJ-NP-Z1-9]{26,33}|r[1-9A-HJ-NP-Za-km-z]{24,34}|D[5-9A-HJ-NP-U][1-9A-HJ-NP-Za-km-z]{32}|4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}|addr1[a-z0-9]{50,99}|T[1-9A-HJ-NP-Za-km-z]{33})"), -10, "crypto_address"),
    DescriptionSignal(re.compile(r"^\s*[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F700-\U0001FAFF\U00002702-\U000027B0\U000024C2-\U0001F251\u2600-\u26FF\u2700-\u27BF\u25A0-\u25FF\u2022\u2023\u2043\u2013\u2014]", re.UNICODE), -10, "emoji_lead"),
    DescriptionSignal(_r(r"\b(instagram|twitter|tiktok|facebook|linkedin|reddit|threads|snapchat|telegram|whatsapp|twitch|rumble|odysee|bitchute)\b"), -6, "social_platform"),
    DescriptionSignal(_r(r"\b(folg(e|t|en)|abonnier|follow|subscribe|sub to|sub here|hit the bell|glocke|benachrichtigung|notification)\b"), -6, "follow_cta"),
    DescriptionSignal(_r(r"\b(patreon|ko-?fi|buy me a coffee|paypal\.me|paypal spenden|mitglied werden|membership|kanal.*mitglied|channel.*member)\b"), -7, "crowdfunding"),
    DescriptionSignal(_r(r"\b(merch|shop|store|t-?shirt|hoodie|fanshop|fanartikel)\b"), -7, "merch"),
    DescriptionSignal(_r(r"\b(sponsor(ed|ing)?|gesponsert|werbung\b|anzeige\b|brought to you|in kooperation|affiliate|partner.?link|rabatt.?code|promo.?code|use code|gutschein|coupon|discount code)\b"), -8, "sponsor"),
    DescriptionSignal(_r(r"\b(werbepartner|produktplatzierung|unbezahlte werbung|bezahlte werbung)\b"), -9, "de_ad_disclosure"),
    DescriptionSignal(_r(r"\b(like (and )?share|smash (the )?like|daumen hoch|like.*klick|klick.*like|teil(e|t|en)\b|don.?t forget|vergiss nicht|hinterlass(t|e)? (einen )?kommentar|leave a comment|comment below|kommentier|schreib.*unten|lass.*wissen|let me know|thanks for watching|danke f.{1,4}s? (zu)?schauen|bis zum n.{1,6}chsten|see you next|watch next|watch more|click here|tap here|link in (the )?bio|links? (in|below|above|unten|in der beschreibung)|in the description)\b"), -5, "cta"),
    DescriptionSignal(_r(r"\b(newsletter|listen on|spotify|apple podcast|google podcast|anchor\.fm|substack|buzzsprout)\b"), -4, "podcast_promo"),
    DescriptionSignal(_r(r"\b(business (mail|email|anfrage|inquiry|enquiry)|for collab(oration)?s?|kooperation anfragen|zusammenarbeit|p\.?o\.? box|postfach|mailing address|impressum)\b"), -5, "contact"),
    DescriptionSignal(_r(r"https?://\S+"), -3, "inline_url"),
    DescriptionSignal(_r(r"#\w+"), -2, "inline_hashtag"),
    DescriptionSignal(_r(r"^\s*.{1,12}\s*$"), -1, "very_short"),
]

CONTENT_SIGNALS = [
    DescriptionSignal(_r(r".{80,}"), +5, "long_line"),
    DescriptionSignal(_r(r".{120,}"), +3, "very_long_line"),
    DescriptionSignal(_r(r"[.!?\u2026]\s*$"), +3, "sentence_end"),
    DescriptionSignal(_r(r"[,;:]\s*\w"), +2, "mid_sentence_punct"),
    DescriptionSignal(_r(r"\b(und|oder|aber|denn|weil|dass|wenn|als|wie|jedoch|allerdings|au\u00dferdem|dennoch|trotzdem|and|or|but|because|that|when|as|however|although|therefore|thus|hence|while)\b"), +2, "conjunction"),
    DescriptionSignal(_r(r"\b\d{4}\b"), +1, "year"),
    DescriptionSignal(_r(r"\d+\s*(kg|km|m\b|cm|mm|gb|mb|tb|hz|mhz|ghz|fps|ms|kb|euro|eur|usd|\$|\u20ac|%|prozent)\b"), +2, "measurement"),
    DescriptionSignal(_r(r"\([^)]{5,}\)"), +2, "parenthetical"),
    DescriptionSignal(_r(r'[\u201e\u201c\u00bb\u00ab"\']{1}.{5,}[\u201d\u201c"\']{1}'), +2, "quote"),
    DescriptionSignal(_r(r"\b(teil\s*\d|part\s*\d|folge\s*\d|staffel\s*\d|season\s*\d|kapitel\s*\d|chapter\s*\d)\b"), +1, "series_ref"),
    DescriptionSignal(_r(r"\b(erkl.{1,3}r|verstehen|lernen|tutorial|anleitung|einf.{1,4}hrung|grundlagen|fortgeschritten|analyse|vergleich|experiment|studie|forschung|ergebnis|wissenschaft|technik|methode|explained?|understand|learning|beginner|advanced|analysis|comparison|experiment|study|research|result|science|technology|deep dive|breakdown|overview|guide|how to|was ist|warum|wieso|weshalb)\b"), +2, "educational_vocab"),
]

PARAGRAPH_POISON_SIGNALS = [
    PoisonSignal(_r(r"\b(sign.?up|sign up for|jetzt anmelden|registrier|create (a |an |your )?account|konto erstellen)\b"), 7.0, "signup"),
    PoisonSignal(_r(r"\b(promo.?code|promocode|rabatt.?code|gutschein.?code|coupon code|discount code|use code|code:\s*\w+|mit dem code|mit code)\b"), 7.0, "promo_code"),
    PoisonSignal(_r(r"\b(free trial|gratis.?monat|kostenlos testen|30.day(s)? free|erste[rn]? monat gratis|try.{1,10}free|jetzt kostenlos)\b"), 7.0, "free_trial"),
    PoisonSignal(_r(r"\b(affiliate|referral link|ref=|partnerlink|gesponserte[rnm]?\b|paid promotion|bezahlte werbung|produktplatzierung)\b"), 7.0, "affiliate"),
    PoisonSignal(_r(r"\b(sponsor(ed|ing)?|gesponsert|in kooperation|brought to you by|powered by|presented by|in zusammenarbeit mit)\b"), 4.0, "sponsorship"),
    PoisonSignal(_r(r"\b(check out|schau(t)? (euch|dir|mal)|besuche?t?|visit|klick(e|t)? (hier|unten)|click (here|below|the link))\b"), 2.5, "check_out_cta"),
    PoisonSignal(_r(r"\b(exklusiv|exclusive|limited( offer)?|nur für kurze zeit|limited time|zeitlich begrenzt|nur heute|only today)\b"), 3.0, "scarcity"),
    PoisonSignal(_r(r"\b(rabatt|discount|angebot|deal|offer|sale|sparen|save)\b"), 2.5, "discount"),
    PoisonSignal(_r(r"\b(link in (der )?beschreibung|link below|link above|link in bio|in the description|unten (im|in der))\b"), 2.5, "link_cta"),
    PoisonSignal(_r(r"\b(patreon|ko-?fi|buy me a coffee|paypal|mitglied|membership|merch|shop)\b"), 3.0, "monetisation"),
    PoisonSignal(_r(r"\b(newsletter|abonniere?|subscribe|folg)\b"), 2.0, "subscribe"),
    PoisonSignal(_r(r"https?://\S+"), 1.5, "url_present"),
]

EMOJI_RE = re.compile(r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F700-\U0001FAFF\U00002702-\U000027B0\U000024C2-\U0001F251\U0000200D\U0000FE0F]+", flags=re.UNICODE)
DECO_RE = re.compile(r"[\u25b6\u25ba\u25b8\u25b7\u27a4\u2605\u2606\u2713\u2714\u2717\u2718\u2022\u00b7\u25aa\u25ab\u25e6\u2023\u2043\u25c6\u25c7\u25a0\u25a1\u25cf\u25cb\u2764]")

@dataclass
class ScoredDescriptionLine:
    original: str
    cleaned: str
    score: float
    signals: list[str] = field(default_factory=list)
    @property
    def is_blank(self) -> bool:
        return self.cleaned.strip() == ""

def _strip_description_inline(line: str) -> str:
    line = EMOJI_RE.sub("", line)
    line = DECO_RE.sub("", line)
    line = re.sub(r"https?://\S+", "", line)
    line = re.sub(r"#\w+", "", line)
    line = re.sub(r"[ \t]{2,}", " ", line)
    return line.strip()

def score_description_line(raw_line: str) -> ScoredDescriptionLine:
    cleaned = _strip_description_inline(raw_line)
    score = 0.0
    signals = []
    if not cleaned:
        return ScoredDescriptionLine(raw_line, cleaned, 0.0, ["blank"])
    emoji_count = len(EMOJI_RE.findall(raw_line))
    if emoji_count >= 3:
        score -= emoji_count * 0.5
        signals.append(f"emoji_density({emoji_count})")
    for sig in CLUTTER_SIGNALS:
        target = raw_line if sig.label == "emoji_lead" else cleaned
        if sig.pattern.search(target):
            score += sig.score
            signals.append(sig.label)
    for sig in CONTENT_SIGNALS:
        if sig.pattern.search(cleaned):
            score += sig.score
            signals.append(sig.label)
    clutter_labels = {s.label for s in CLUTTER_SIGNALS}
    if not any(s in clutter_labels for s in signals) and len(cleaned) > 60:
        score += 2
        signals.append("clean_long_bonus")
    non_space = cleaned.replace(" ", "")
    if len(non_space) > 8 and non_space.isupper():
        score -= 3
        signals.append("all_caps")
    if re.search(r"[a-z\u00e4\u00f6\u00fc\u00df]{4,}", cleaned):
        score += 1
        signals.append("lowercase_prose")
    return ScoredDescriptionLine(raw_line, cleaned, round(score, 2), signals)

@dataclass
class DescriptionParagraph:
    lines: list[ScoredDescriptionLine] = field(default_factory=list)
    poison_score: float = 0.0
    poison_hits: list[str] = field(default_factory=list)
    @property
    def is_poisoned(self) -> bool:
        return self.poison_score >= 6.0
    def analyse_poison(self) -> None:
        full_text = " ".join(sl.cleaned for sl in self.lines if not sl.is_blank)
        for sig in PARAGRAPH_POISON_SIGNALS:
            if sig.pattern.search(full_text):
                self.poison_score += sig.weight
                self.poison_hits.append(sig.label)

def clean_youtube_description(raw_description: str, threshold: float = 0.0) -> str:
    if not raw_description:
        return ""
    scored_lines = [score_description_line(line) for line in raw_description.splitlines()]
    paragraphs = [DescriptionParagraph()]
    for sl in scored_lines:
        if sl.is_blank:
            if paragraphs[-1].lines:
                paragraphs.append(DescriptionParagraph())
        else:
            paragraphs[-1].lines.append(sl)
    paragraphs = [p for p in paragraphs if p.lines]
    for p in paragraphs:
        p.analyse_poison()
    kept_parts = []
    for p in paragraphs:
        if p.is_poisoned:
            continue
        kept_lines = [sl.cleaned for sl in p.lines if sl.score > threshold and not sl.is_blank]
        if kept_lines:
            kept_parts.append("\n".join(kept_lines))
    result = "\n\n".join(kept_parts)
    return re.sub(r"\n{3,}", "\n\n", result).strip()

def resource_path(relative_path):

    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


# ----------------------------------------------------------------------------------------------------
# Find binary in system PATH or common macOS locations
# ----------------------------------------------------------------------------------------------------
def find_binary(name):
    # First try to find in PATH
    binary_path = shutil.which(name)
    if binary_path:
        return binary_path

    # Pip console-scripts dir of the running Python (version-agnostic).
    # Catches e.g. /Library/Frameworks/Python.framework/Versions/3.14/bin/yt-dlp
    # without hardcoding the Python version.
    scripts_dir = sysconfig.get_path("scripts")
    if scripts_dir:
        pip_bin_path = os.path.join(scripts_dir, name)
        if os.path.isfile(pip_bin_path) and os.access(pip_bin_path, os.X_OK):
            return pip_bin_path

    # Fallback: common install locations (macOS + Linux)
    common_locations = [
        f"/usr/local/bin/{name}",
        f"/opt/homebrew/bin/{name}",
        f"/opt/local/bin/{name}",
        f"/home/linuxbrew/.linuxbrew/bin/{name}",  # Homebrew on Linux
        f"~/.deno/bin/{name}",  # Deno installer dir; often missing from GUI-launched PATH
        f"~/.local/bin/{name}",
    ]
    for location in common_locations:
        expanded_path = os.path.expanduser(location)
        if os.path.isfile(expanded_path) and os.access(expanded_path, os.X_OK):
            return expanded_path

    # Python.org framework installs (versioned subfolders). Broadly catches
    # pip-installed binaries when running as a PyInstaller bundle, where the
    # sysconfig lookup above resolves inside the app, not the system Python.
    for framework_bin in glob.glob(
        "/Library/Frameworks/Python.framework/Versions/*/bin"
    ):
        candidate = os.path.join(framework_bin, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    return name
    # Return name as-is, let it fail with helpful error


# ----------------------------------------------------------------------------------------------------
# Normalize (clean) a YouTube URL
# ----------------------------------------------------------------------------------------------------
def normalize_url(url):
    # Normalize (and if needed, extract) a YouTube URL from user input.
    #
    # The URL field may contain extra surrounding text (e.g. copied from chat):
    #   mytext `https://www.youtube.com/watch?v=xxxxxxxxxxx`
    # In that case we want to extract the actual URL/id instead of passing the
    # whole string to yt-dlp.
    if not url:
        return ""
    # Remove extra whitespace and normalize
    url = re.sub(r"\s+", " ", str(url)).strip()

    # 1) Extract the first URL-like token if there is any scheme URL inside the text.
    m = re.search(r"(https?://[^\s]+)", url, flags=re.IGNORECASE)
    if m:
        url = m.group(1)
    else:
        # 2) Extract a youtube-domain token without scheme, e.g. "www.youtube.com/..."
        m = re.search(
            r"((?:www\.)?(?:youtube\.com|youtu\.be)/[^\s]+)",
            url,
            flags=re.IGNORECASE,
        )
        if m:
            url = "https://" + m.group(1)

    # Strip common wrappers / trailing punctuation from copied text
    url = url.strip("`\"'<>[](){}.,;")

    # If it's a naked 11-char YouTube ID, make it a full URL
    if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
        return f"https://www.youtube.com/watch?v={url}"

    # If the input still isn't a clean URL, but contains a recognizable YouTube ID,
    # canonicalize it to a watch URL (handles cases like trailing backticks, etc.).
    if m := re.search(
        r"(?:v=|embed/|shorts/|live/|youtu\.be/)([a-zA-Z0-9_-]{11})",
        url,
        flags=re.IGNORECASE,
    ):
        return f"https://www.youtube.com/watch?v={m.group(1)}"

    # Remove query parameters after first & (keeps ?v=... but removes &list=... etc)
    url = re.sub(r"&.*$", "", url)
    return url


# ----------------------------------------------------------------------------------------------------
# Remove unwanted stuff from title
# ----------------------------------------------------------------------------------------------------
def sanitize_title(title):
    # Preprocessing
    title = title.replace(":", " - ")
    title = title.replace("! ", " - ")
    title = re.sub(r"[|–/]", "-", title)

    # German characters to preserve
    german_chars = "äöüÄÖÜß"
    # Character Normalization and Filtering via List Comprehension
    normalized_chars = [
        char
        if char in german_chars
        else "".join(
            c
            for c in unicodedata.normalize("NFD", char)
            if unicodedata.category(c) != "Mn"
        )
        for char in title
    ]
    # String Rebuilding
    title = "".join(normalized_chars)
    # Remove all characters except: word characters, spaces, hyphens, parentheses, and German umlauts
    sanitized = re.sub(r"[^\w\s\-\(\)äöüÄÖÜß]", "", title)
    # Clean up multiple spaces
    sanitized = re.sub(r"\s+", " ", sanitized)
    return sanitized.strip()


# ----------------------------------------------------------------------------------------------------
# Format video duration
# ----------------------------------------------------------------------------------------------------
def format_duration(total_seconds):
    try:
        total_seconds = int(float(total_seconds))
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        return f"{hours:02}:{minutes:02}:{seconds:02}"
    except (ValueError, TypeError):
        return "N/A"


# ----------------------------------------------------------------------------------------------------
# Format file size
# ----------------------------------------------------------------------------------------------------
def format_filesize(num_bytes):
    if num_bytes is None:
        return "N/A"
    try:
        num_bytes = float(num_bytes)
        # Use 1000 to match macOS Finder decimal calculation
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if num_bytes < 1000.0:
                return f"{num_bytes:.2f} {unit}"
            num_bytes /= 1000.0
        return f"{num_bytes:.2f} PB"
    except (ValueError, TypeError):
        return "N/A"


# ----------------------------------------------------------------------------------------------------
# Convert SRT timestamp to seconds
# ----------------------------------------------------------------------------------------------------
def parse_srt_time(time_str):
    # Format: HH:MM:SS,mmm
    match = re.match(r"(\d+):(\d+):(\d+),(\d+)", time_str)
    if not match:
        return 0

    h, m, s, ms = map(int, match.groups())
    return h * 3600 + m * 60 + s + ms / 1000


# ----------------------------------------------------------------------------------------------------
# Convert seconds to SRT timestamp format
# ----------------------------------------------------------------------------------------------------
def format_srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)

    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ----------------------------------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    simulate_download_error = "--simulate-download-error" in sys.argv

    app = QApplication(sys.argv)
    window = YTDLPDownloaderGUI()
    window.simulate_download_error = simulate_download_error
    window.show()
    sys.exit(app.exec())
