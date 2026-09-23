# Debug helper: simulate the exact user flow for garbage input ("nonsense")
# typed into the URL field, showing that the plausibility gate rejects it
# before any yt-dlp subprocess would be spawned.
# Run from the repo root:  python3 temp/replay_url_gate.py

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app  # noqa: E402

raw = "nonsense"
clean = app.normalize_url(raw)
print("normalize_url('nonsense')  ->", repr(clean))
print("is_plausible_url(clean)    ->", app.is_plausible_url(clean))
print(
    "=> fetch_video_info rejects BEFORE spawning yt-dlp:",
    not app.is_plausible_url(clean),
)

# Sanity: every legitimate input form still passes the gate
for good in [
    "https://rumble.com/v7fto6c-x.html?pri=abc",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "dQw4w9WgXcQ",
    "www.youtube.com/watch?v=dQw4w9WgXcQ",
    "rumble.com/v6abcde",
]:
    c = app.normalize_url(good)
    assert app.is_plausible_url(c), good
print("=> all legitimate input forms still pass the gate")
