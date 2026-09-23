# Manual verification harness for the Odysee support (site profile + gate).
# Run from the repo root:  python3 temp/verify_odysee_harness.py

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app

ODYSEE_VIDEO = "https://odysee.com/@Mantega:1/First-day-LBRY:1"
ODYSEE_CHANNEL = "https://odysee.com/@Mantega:1"
ODYSEE_LBRY_TV = "https://lbry.tv/@LBRYFoundation:0/Episode-1:e"


class Harness:
    def __init__(self):
        self.video_state = {}
        self.title_texts = []
        self.title_entry = types.SimpleNamespace(setText=self.title_texts.append)

    url_rejection_reason = app.YTDLPDownloaderGUI.url_rejection_reason


h = Harness()

print("detect_site        :", app.detect_site(ODYSEE_VIDEO))
print("is_known_site      :", app.is_known_site(ODYSEE_VIDEO), app.is_known_site(ODYSEE_LBRY_TV))
print("is_channel_url     : video:", app.is_channel_url(ODYSEE_VIDEO),
      "| channel:", app.is_channel_url(ODYSEE_CHANNEL))
print("gate (video)       :", h.url_rejection_reason(ODYSEE_VIDEO))
print("gate (channel)     :", h.url_rejection_reason(ODYSEE_CHANNEL))
print("gate (google.com)  :", h.url_rejection_reason("https://www.google.com/"))
print("YT channel gate    :", h.url_rejection_reason("https://www.youtube.com/@SomeHandle"))
print("youtube video gate :", h.url_rejection_reason("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
print("inferred video id  :", app.ODYSEE_ID_REGEX.search(ODYSEE_VIDEO).group(1))
print("supported label    :", app.SUPPORTED_SITES_LABEL)
print("odysee profile     :", {k: v for k, v in app.SUPPORTED_SITES["odysee"].items() if k != "channel_url_regex"})
print("ALL ODYSEE CHECKS DONE")
