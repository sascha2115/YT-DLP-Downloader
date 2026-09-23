# Debug helper: run the REAL info fetch (no mocks) for an Odysee URL and print
# the simulated output panel, to verify site-specific behavior end to end.
# Run from the repo root:
#   python3 temp/replay_odysee_info.py [url]

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app

URL = sys.argv[1] if len(sys.argv) > 1 else "https://odysee.com/@Mantega:1/First-day-LBRY:1"


class Harness(app.InfoFetchMixin):
    def __init__(self):
        self.video_state = {
            "url": URL,
            "clean_url": URL,
            "video_id": "",
            "site": app.detect_site(URL),
        }
        self.signals = app.SignalEmitter()
        self.yt_dlp_bin = app.find_binary("yt-dlp")
        self.deno_bin = app.find_binary("deno")
        self.description_summaries = []

    update_video_state = app.YTDLPDownloaderGUI.update_video_state
    url_rejection_reason = app.YTDLPDownloaderGUI.url_rejection_reason

    def _emit_description_summary(self):
        self.description_summaries.append(True)


h = Harness()
captured = []
h.signals.append_output.connect(captured.append)
h.signals.update_title.connect(lambda t: captured.append(f"[TITLE FIELD] {t}"))

print(f"site: {h.video_state['site']} | info timeout: {app.site_info_timeout(h.video_state['site'])}s")
print("gate verdict:", h.url_rejection_reason(URL))
h.get_video_info(URL)

print("--- simulated output panel ---")
for line in captured:
    print(line)
