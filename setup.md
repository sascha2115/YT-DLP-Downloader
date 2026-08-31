# Setup Guide — YT-DLP Downloader (from source)

Complete instructions for installing, running, and packaging **YT-DLP Downloader** from source on a new machine, for **macOS** and **Linux**.

The app is a PyQt6 GUI around `yt-dlp` with SponsorBlock support. It needs three things on whatever machine it runs on: **Python 3.10+**, the **pip dependencies** in `requirements.txt`, and the **external binaries** `yt-dlp`, `ffmpeg`/`ffprobe`, and (recommended) `deno`.

## Requirements at a glance

| Component | macOS | Linux |
|---|---|---|
| Python | 3.10+ (3.11+ recommended) | 3.10+ (3.11+ recommended) |
| GUI toolkit | PyQt6 (pip) | PyQt6 (pip) **+ system xcb/X11 libs** |
| yt-dlp | Homebrew or pip | pip or distro package |
| ffmpeg + ffprobe | Homebrew | distro package |
| deno (optional, recommended) | Homebrew | official installer script |
| Packaging | `./build-macos.sh` → `.app` | `./build-linux.sh` → onedir |

> **Why Python 3.10+?** The code uses PEP 604 unions (`X | None`) in type annotations, which are evaluated at import time. Older interpreters fail with a `TypeError` at startup. Check with `python3 --version`.

## 1. Get the source

```bash
git clone https://github.com/sascha2115/YT-DLP-Downloader.git ytdl
cd ytdl
```

The repo root holds `app.py`, the build scripts, and docs; icons and `styles.qss` live in `assets/`.

## 2. macOS

### 2.1 Homebrew and Python

```bash
# Xcode command line tools (skip if present)
xcode-select --install

# Homebrew (skip if installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Python 3.10+  — macOS's built-in /usr/bin/python3 (3.9) is NOT sufficient
brew install python
```

A python.org installer works too; the app's `find_binary()` also detects pip-installed tools under `/Library/Frameworks/Python.framework/Versions/*/bin`.

### 2.2 External binaries

```bash
brew install yt-dlp ffmpeg deno
```

- `ffmpeg` includes `ffprobe` — the app uses **both** (ffmpeg for muxing, ffprobe for media metadata).
- `deno` is optional but strongly recommended: the app passes it to yt-dlp as a JavaScript runtime (`--js-runtimes`) for YouTube's challenge/extractor scripts.
- Install locations differ by CPU: Apple Silicon → `/opt/homebrew/bin`, Intel → `/usr/local/bin`. Both are searched by `find_binary()`.

> If `yt-dlp` errors on specific videos, try the nightly pre-release (§6 Troubleshooting) — Homebrew builds only update with `brew upgrade`.

### 2.3 Python environment

```bash
cd /path/to/ytdl
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On macOS this installs `PyQt6`, `requests`, and `pyobjc-framework-Cocoa` (the latter powers the dock-icon badge and progress overlay).

### 2.4 Run

```bash
python3 app.py
```

The working directory does not matter; the stylesheet and icon under `assets/` are resolved relative to the script.

### 2.5 Package as a .app bundle

```bash
pip install pyinstaller
./build-macos.sh    # → dist/YT-DLP Downloader.app
```
> `build-macos.sh` finds `pyinstaller` on its own — project venv first, then `~/.local/bin` (pip `--user` installs), then `PATH`. No venv activation needed.

The bundle is **unsigned**, so the first launch may be blocked by Gatekeeper: right-click the app → *Open*, or allow it under *System Settings → Privacy & Security*.

## 3. Linux

### 3.1 System packages

**Debian / Ubuntu:**

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv \
    libxcb-cursor0 libxcb-xinerama0 libxkbcommon0 libxkbcommon-x11-0 \
    libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 \
    libxcb-shape0 libgl1 libglib2.0-0 libfontconfig1 libdbus-1-3
```

The `libxcb*`/`libxkbcommon*` packages are the runtime libs the PyQt6 wheel's xcb platform plugin needs.

**Fedora / Arch:** installing the distro's Qt6 base package pulls in all required xcb libraries:

```bash
sudo dnf install python3-pip qt6-qtbase-gui    # Fedora
sudo pacman -S --needed python python-pip qt6-base ffmpeg deno   # Arch / CachyOS
```

(Arch-based KDE editions such as CachyOS already ship the full Qt6 stack, so `--needed` skips most of it; on non-KDE spins `qt6-base` pulls in the required xcb libraries. Package names can differ slightly between releases; the Debian list above shows exactly what is needed.)

### 3.2 External binaries

```bash
# ffmpeg + ffprobe — distro package provides both
sudo apt install ffmpeg          # Debian/Ubuntu
# Fedora: enable RPM Fusion first, then: sudo dnf install ffmpeg
# Arch:   sudo pacman -S ffmpeg

# deno (optional but recommended) — official installer, lands in ~/.deno/bin
curl -fsSL https://deno.land/install.sh | sh
# Arch / CachyOS: deno is in the official repos instead — sudo pacman -S deno
```

`yt-dlp` is best installed **into the app's venv** (step 3.3) via `pip install yt-dlp` — `find_binary()` auto-detects it there, and it needs frequent updates. A distro-packaged `yt-dlp` also works if it is on `PATH`.

> If `yt-dlp` errors on specific videos, try the nightly pre-release — see §6 Troubleshooting.

> **ffprobe gotcha:** the app's startup warning only lists `yt-dlp`, `ffmpeg`, and `deno` — but it also calls `ffprobe`. Distro `ffmpeg` packages include it; a bare/static ffmpeg without ffprobe will quietly break media-duration features. Verify with `ffprobe -version`.

### 3.3 Python environment

```bash
cd /path/to/ytdl
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install yt-dlp               # recommended: keep the downloader inside the venv
```

> **fish shell (the CachyOS default):** `source .venv/bin/activate` is the POSIX/bash script and fails in fish with `case builtin not inside of switch block`. Use the fish variant — `source .venv/bin/activate.fish` — or skip activation entirely and call the venv binaries directly: `.venv/bin/pip install -r requirements.txt` and `.venv/bin/python app.py` (works from any shell).

On Linux the `pyobjc` requirement is skipped automatically (environment marker), so only `PyQt6` and `requests` are installed.

> The venv also sidesteps PEP 668 ("externally-managed-environment") on Debian 12+, Ubuntu 23.04+, and Fedora, which refuse bare `pip install` outside venvs.
>
> **Global install instead?** The venv is recommended, not required. On Arch/CachyOS (no PEP 668 lock) you can go fully pacman-managed with `sudo pacman -S python-pyqt6 python-requests yt-dlp` and skip pip entirely, or install user-scoped with `pip install --user -r requirements.txt yt-dlp` (no sudo, doesn't touch pacman's files, nightly switching stays easy). Avoid `sudo pip install`: it writes into the same site-packages pacman manages. On Debian/Ubuntu/Fedora and Homebrew-Python macOS, stick with a venv (or `--user`).

### 3.4 Run

```bash
python3 app.py
```

**Wayland:** if the window fails to open, run with `QT_QPA_PLATFORM=xcb python3 app.py` (via XWayland) or install your distro's `qt6-wayland` package.

### 3.5 Package for Linux

```bash
.venv/bin/pip install pyinstaller
./build-linux.sh       # → dist/YT-DLP Downloader/
# single-file alternative:
.venv/bin/pyinstaller --onefile app.py    # → dist/app
```
> `build-linux.sh` finds `pyinstaller` on its own — project venv first, then `~/.local/bin` (pip `--user` installs), then `PATH`. No activation needed, even in fish.

> **PyInstaller cannot cross-compile.** Linux binaries must be built *on* Linux and macOS `.app` bundles *on* macOS. To ship for both platforms, build on each (a Linux VM, container, or CI runner works).

## 4. Verify the installation

```bash
# Parser/progress unit tests — no GUI or network needed (expect: Ran 21 tests, OK)
python3 -m unittest temp.test_subtitle_progress -v

# Launch with the built-in error simulation (downloads raise by design)
python3 app.py --simulate-download-error
```

On startup the app checks for `yt-dlp`, `ffmpeg`, and `deno` and prints
`🚩 Error: Missing Dependencies: …` in its output pane if anything is absent.
Cross-check manually:

```bash
which yt-dlp ffmpeg ffprobe deno
```

## 5. Where the app stores its data

| Data | macOS | Linux |
|---|---|---|
| Preferences | `~/Library/Application Support/YT-DLP Downloader/preferences.json` | `~/.config/YT-DLP Downloader/preferences.json` (respects `XDG_CONFIG_HOME`) |
| Logs | `~/Library/Logs/YT-DLP Downloader/app.log` | `~/.local/state/YT-DLP Downloader/` (respects `XDG_STATE_HOME`) |
| Downloads | `~/Downloads` by default (changeable in the app) | same |

All directories are created automatically on first run. Preferences are edited via the in-app dialog; the `preferences.json` in the repo root is only a reference sample.

## 6. Troubleshooting

- **"🚩 Error: Missing Dependencies"** — install the listed binary(s) (§2.2 / §3.2), then reopen the app.
- **Binary not found despite being installed** — `find_binary()` searches in this order:
  1. `PATH`
  2. the running Python's scripts directory (catches pip/venv installs)
  3. `/usr/local/bin`, `/opt/homebrew/bin`, `/opt/local/bin`, `/home/linuxbrew/.linuxbrew/bin`, `~/.deno/bin`, `~/.local/bin`
  4. (macOS only) `/Library/Frameworks/Python.framework/Versions/*/bin`

  If your binary lives elsewhere, symlink it into `~/.local/bin` or extend the fallback list in `find_binary()` in `app.py`.
- **`yt-dlp` errors on a specific video** (extraction/site breakage) — try the **nightly pre-release**, which upstream publishes for exactly this:
  ```bash
  # inside the app's venv (recommended; no --break-system-packages needed there):
  python3 -m pip install -U --pre "yt-dlp[default]"

  # system-wide installs on PEP 668 distros (Debian 12+/Ubuntu 23.04+/Fedora,
  # and Homebrew's Python): pip needs permission to touch the managed env:
  python3 -m pip install --upgrade --break-system-packages --pre "yt-dlp[default]"
  ```
  `[default]` pulls in the recommended optional dependencies. Roll back to stable any time with `python3 -m pip install -U "yt-dlp[default]"` (drop `--pre`), or go bleeding-edge master with `python3 -m pip install --force-reinstall "yt-dlp[default] @ https://github.com/yt-dlp/yt-dlp/archive/master.tar.gz"`.
  Make sure you upgrade the copy the app actually uses (`which -a yt-dlp`; resolution order see the "Binary not found" bullet above) and restart the app afterwards.
- **`qt.qpa.plugin: could not load xcb`** — install the system libs from §3.1; on Wayland see §3.4.
- **`error: externally-managed-environment`** from pip — you are outside a venv; redo §2.3 / §3.3.
- **No dock badge/progress on Linux** — expected: dock-tile integration is macOS-only; progress is shown in the main window on all platforms.

## 7. Optional: Linux application-menu entry

**One-shot setup** — if the clone lives at `~/ytdl`, this creates the launcher file with real
paths filled in (works in fish, bash and zsh — `$HOME` expands automatically):

```bash
mkdir -p ~/.local/share/applications
printf '%s\n' \
"[Desktop Entry]" \
"Type=Application" \
"Name=YT-DLP Downloader" \
"Exec=$HOME/ytdl/.venv/bin/python $HOME/ytdl/app.py" \
"Icon=$HOME/ytdl/assets/AppIcon.png" \
"StartupWMClass=ytdl-downloader" \
"Terminal=false" \
"Categories=Network;AudioVideo;" \
> ~/.local/share/applications/ytdl-downloader.desktop
update-desktop-database ~/.local/share/applications
```

(For a clone in a different location, adjust the two paths after `Exec=` and `Icon=`.)

**What the file contains** (`~/.local/share/applications/ytdl-downloader.desktop`), for manual creation:

```ini
[Desktop Entry]
Type=Application
Name=YT-DLP Downloader
Exec=/absolute/path/to/ytdl/.venv/bin/python /absolute/path/to/ytdl/app.py
Icon=/absolute/path/to/ytdl/assets/AppIcon.png
StartupWMClass=ytdl-downloader
Terminal=false
Categories=Network;AudioVideo;
```

> `StartupWMClass` matches the app identity set in `app.py` (`setApplicationName`/`setDesktopFileName`); it makes the window manager associate the running window with this launcher so the taskbar shows **one** entry with the correct icon instead of a generic one. Keep the desktop file named `ytdl-downloader.desktop` to match.

Then refresh the menu database: `update-desktop-database ~/.local/share/applications`.

The app then appears in the application launcher (search for *YT-DLP*) with its icon — right-click it there to **Add to Favorites**, or launch it once and right-click the taskbar entry → **Pin to Taskmanager** (Plasma). To place a copy on the desktop: `cp ~/.local/share/applications/ytdl-downloader.desktop ~/Desktop/ && chmod +x ~/Desktop/ytdl-downloader.desktop`. If the launcher entry doesn't show up immediately, rebuild Plasma's cache with `kbuildsycoca6`.

