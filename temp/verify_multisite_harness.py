# Manual verification harness for the Rumble PoC (site-aware extract_video_id).
# Run from the repo root:  python3 temp/verify_multisite_harness.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app


class Harness:
    def __init__(self):
        self.video_state = {"url": "", "video_id": "", "site": "youtube"}

    extract_video_id = app.YTDLPDownloaderGUI.extract_video_id


h = Harness()

# Rumble URL: supported, ID extracted, site recorded
rumble = "https://rumble.com/v6abcde-some-title.html"
vid = h.extract_video_id(rumble)
print(
    "rumble id:", vid,
    "| site:", h.video_state["site"],
    "| supported:", app.is_known_site(rumble),
    "| is_youtube:", app.detect_site(rumble) == "youtube",
)
assert vid == "v6abcde", vid
assert h.video_state["site"] == "rumble", h.video_state
assert app.is_known_site(rumble)
assert app.detect_site(rumble) != "youtube"

# Switch to a YouTube URL: ID + site must follow
youtube = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
vid = h.extract_video_id(youtube)
print(
    "youtube id:", vid,
    "| site:", h.video_state["site"],
    "| is_youtube:", app.detect_site(youtube) == "youtube",
)
assert vid == "dQw4w9WgXcQ", vid
assert h.video_state["site"] == "youtube", h.video_state

# Unknown domain: no ID, but still default site (yt-dlp may handle it)
vid = h.extract_video_id("https://example.com/video")
print("example.com id:", vid, "| site:", h.video_state["site"])
assert vid is None, vid
assert h.video_state["site"] == "youtube", h.video_state

# SponsorBlock profile flags (no GUI required)
assert app.SUPPORTED_SITES["youtube"]["sponsorblock"] is True
assert app.SUPPORTED_SITES["rumble"]["sponsorblock"] is False

print("ALL HARNESSED CHECKS PASSED")
