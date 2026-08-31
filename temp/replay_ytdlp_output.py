"""Replay a captured yt-dlp output file through the real parser and report
the progress-bar state after every line (validation helper, not a test)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from temp.test_subtitle_progress import make_gui, make_state  # noqa: E402


def main(path, media_type="video"):
    gui = make_gui(media_type)
    state = make_state(gui)
    with open(path, encoding="utf-8", errors="replace") as fh:
        # splitlines() mirrors the app's universal-newlines iteration, which
        # also splits yt-dlp's "\r"-separated progress chunks into lines
        content = fh.read()
    for line in content.splitlines():
        state = gui._parse_download_output(line, state)
        pm = state["progress_manager"]
        if "Destination:" in line or "%" in line or "[Merger]" in line:
            print(
                f"video={pm.video_progress:4d} audio={pm.audio_progress:4d} "
                f"count={pm.download_count} dock={pm.get_combined_fraction():.2f} "
                f"| {line[:80]}"
            )
    print("\nFINAL:", {
        "video": state["progress_manager"].video_progress,
        "audio": state["progress_manager"].audio_progress,
        "count": state["progress_manager"].download_count,
        "merged": state["merged_filename"],
    })


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "video")
