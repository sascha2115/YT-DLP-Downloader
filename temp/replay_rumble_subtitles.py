# Debug helper: run the app's exact subtitle flags against the live Rumble
# test video, then replay the app's full post-processing chain
# (detection -> name normalization -> 2-line resync) and show the final
# files on disk. Run from the repo root:
#   python3 temp/replay_rumble_subtitles.py

import os
import shutil
import subprocess
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app  # noqa: E402
from temp.test_subtitle_multisite import SubtitleHarness  # noqa: E402
from temp.test_rumble_info_parse import RUMBLE_URL  # noqa: E402


def bind_all(h):
    """Bind every GUI method onto the harness (replay-only convenience).

    Skips anything the harness (or its class) already provides, so the
    harness's own get_output_dir / get_full_path overrides survive.
    """
    harness_names = set(dir(type(h))) | set(vars(h))
    for name in dir(app.YTDLPDownloaderGUI):
        if name in harness_names or name.startswith("__"):
            continue
        if not (name.startswith("_") and name.count("_") > 1):
            continue
        attr = getattr(app.YTDLPDownloaderGUI, name)
        if callable(attr):
            setattr(h, name, types.MethodType(attr, h))
        else:
            # Class-level data (e.g. _SUBTITLE_SENTENCE_ABBREVS)
            setattr(h, name, attr)
    # Public helpers the chain needs (get_full_path/get_output_dir stay
    # harness-provided so paths point at the replay directory)
    h.resync_subtitles = types.MethodType(app.YTDLPDownloaderGUI.resync_subtitles, h)


out_dir = tempfile.mkdtemp(prefix="rumble_subs_")
h = SubtitleHarness(out_dir)
h.signals = app.SignalEmitter()
# Match the -o template below so canonical paths line up; site=rumble is what
# the real GUI would have recorded for this URL (drives the resync decision)
h.video_state["base_filename"] = "S26E0922 - Test"
h.video_state["site"] = "rumble"
# Bind the real post-processing chain from the GUI class
bind_all(h)

lang_arg = h._subtitle_lang_patterns(["en"])
print("app --sub-langs argument:", lang_arg)

cmd = [
    app.find_binary("yt-dlp") or "yt-dlp",
    "--write-subs", "--write-auto-subs",
    "--sub-langs", lang_arg,
    "--sub-format", "srt",
    "--convert-subs", "srt",
    "--skip-download",
    "-o", os.path.join(out_dir, "S26E0922 - Test.%(ext)s"),
    RUMBLE_URL,
]
print("running:", " ".join(cmd[:10]), "...")
res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
print("exit:", res.returncode)

print("\nafter download:", sorted(os.listdir(out_dir)))

# --- app post-processing chain (as in run_download) ---
downloaded_subs = h._normalize_subtitle_names(h._find_downloaded_subtitles(["en"]))
for lang, srt_path, sub_type in downloaded_subs:
    print(f"detected: {lang} ({sub_type}) -> {os.path.basename(srt_path)}")
    if h._subtitle_needs_resync(sub_type):
        # Same call run_download makes: output == input path -> in-place
        h._resync_subtitle_for_language(lang, srt_path, [])
    else:
        print("  → keeping original format (site needs no resync)")

print("\nfinal files on disk:", sorted(os.listdir(out_dir)))
if len(os.listdir(out_dir)) == 1 and downloaded_subs:
    print("OK: single subtitle file (no duplicate)")
else:
    print("PROBLEM: duplicate files remain")

shutil.rmtree(out_dir, ignore_errors=True)
