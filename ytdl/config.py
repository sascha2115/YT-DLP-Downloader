"""App-wide configuration constants."""

import os

DEFAULT_OUTPUT_DIR = os.path.expanduser("~/Downloads")

# Delay before the (debounced) automatic info fetch after a URL edit
TITLE_FETCH_DELAY_MS = 500

# Default budget for the yt-dlp info fetch (`--print-json --skip-download`).
# Sites whose API is slow can override it via the site profile's
# "info_timeout" key (see ytdl/sites.py, e.g. Odysee/LBRY).
INFO_FETCH_TIMEOUT_SECONDS = 15

# While the info fetch is running, emit a progress hint into the output panel
# after this many seconds and then repeat every N seconds, so long upstream
# waits (Odysee's LBRY resolve routinely takes ~40-70s) do not look frozen.
INFO_FETCH_HINT_AFTER_SECONDS = 10
INFO_FETCH_HINT_EVERY_SECONDS = 20
