# Debug helper: replay the captured Rumble info JSON through the real
# get_video_info() and print what the output panel would show.
# Run from the repo root:  python3 temp/replay_rumble_info.py

import json
import os
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402
from temp.test_rumble_info_parse import Harness, RUMBLE_URL, CAPTURE_PATH  # noqa: E402

h = Harness()
captured = []
h.signals.append_output.connect(captured.append)
h.signals.update_title.connect(lambda t: captured.append(f"[TITLE FIELD] {t}"))

with open(CAPTURE_PATH) as fh:
    payload = json.load(fh)

fake = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
with mock.patch.object(app.subprocess, "run", return_value=fake):
    h.get_video_info(RUMBLE_URL)

print("--- simulated output panel ---")
for line in captured:
    print(line)
