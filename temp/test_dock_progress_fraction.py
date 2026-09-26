"""
The dock overlay's overall fraction must never go backwards.

get_combined_fraction() used to return the plain video fraction during the
video transfer (0 -> 100%) and switch to the byte-weighted sum when the audio
transfer started - which, with the audio at 0%, is VIDEO_BYTE_WEIGHT. The dock
bar therefore jumped back to ~85% when the second stream appeared and then
climbed to 100% again. The video phase now only fills its share of the bar and
the audio transfer fills the rest, so the value is monotonic; the reservation
is released at mark_complete() for a download that turned out to be a single
muxed stream.

Run from the repo root:
    python3 -m unittest temp.test_dock_progress_fraction -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ytdl.progress import DownloadProgressManager  # noqa: E402
from temp.test_subtitle_progress import (  # noqa: E402
    VIDEO_WITH_RETRIES_LINES,
    feed,
    make_gui,
    make_state,
)


def manager(media_type="video"):
    return DownloadProgressManager(media_type=media_type)


def advance_video(pm, percent):
    """One video-stream progress line."""
    pm.on_download_destination("/tmp/Title.f399.mp4")
    return pm.update_from_ytdlp_percent(percent)


def advance_audio(pm, percent):
    """One audio-stream progress line (a second destination switches over)."""
    pm.on_download_destination("/tmp/Title.f251.webm")
    return pm.update_from_ytdlp_percent(percent)


class TestDockFractionIsMonotonic(unittest.TestCase):
    """The reported bug: 0->100% for the video, back to ~85% for the audio."""

    def test_video_phase_stops_at_its_share(self):
        pm = manager()
        advance_video(pm, 100.0)
        # Not 1.0: the audio share is still reserved.
        self.assertAlmostEqual(pm.get_combined_fraction(), 0.85)

    def test_audio_start_does_not_move_the_bar_back(self):
        pm = manager()
        advance_video(pm, 100.0)
        at_handover = pm.get_combined_fraction()
        # The audio destination arrives; the bar must not drop.
        pm.on_download_destination("/tmp/Title.f251.webm")
        self.assertAlmostEqual(pm.get_combined_fraction(), at_handover)

    def test_two_stream_download_rises_from_0_to_1(self):
        pm = manager()
        fractions = []
        for percent in range(0, 101, 5):
            advance_video(pm, percent)
            fractions.append(pm.get_combined_fraction())
        for percent in range(0, 101, 10):
            advance_audio(pm, percent)
            fractions.append(pm.get_combined_fraction())
        self.assertAlmostEqual(fractions[0], 0.0)
        self.assertAlmostEqual(fractions[-1], 1.0)
        for previous, current in zip(fractions, fractions[1:]):
            self.assertGreaterEqual(
                current, previous, f"fraction went backwards: {fractions}"
            )

    def test_incomplete_run_never_claims_full(self):
        # A failed or cancelled download must not paint a complete bar.
        pm = manager()
        advance_video(pm, 100.0)
        self.assertLess(pm.get_combined_fraction(), 1.0)


class TestDockFractionAtCompletion(unittest.TestCase):
    def test_single_muxed_stream_lands_on_one(self):
        pm = manager()
        advance_video(pm, 100.0)
        pm.mark_complete()
        self.assertAlmostEqual(pm.get_combined_fraction(), 1.0)

    def test_two_stream_download_lands_on_one(self):
        pm = manager()
        advance_video(pm, 100.0)
        advance_audio(pm, 100.0)
        pm.mark_complete()
        self.assertAlmostEqual(pm.get_combined_fraction(), 1.0)

    def test_single_stream_media_types_are_not_rescaled(self):
        # Audio-only and video-only downloads have a single stream that owns
        # the whole bar, so no share is reserved for them.
        audio = manager("audio")
        audio.on_download_destination("/tmp/Title.m4a")
        audio.update_from_ytdlp_percent(60.0)
        self.assertAlmostEqual(audio.get_combined_fraction(), 0.6)
        video_only = manager("video_only")
        advance_video(video_only, 60.0)
        self.assertAlmostEqual(video_only.get_combined_fraction(), 0.6)


class TestRealParsedOutputIsMonotonic(unittest.TestCase):
    """Same sequence through the REAL parser, checking the emitted values."""

    def test_emitted_dock_values_never_decrease(self):
        gui = make_gui("video")
        state = make_state(gui)
        # Captured video stream, then the real audio destination and its lines.
        lines = VIDEO_WITH_RETRIES_LINES + [
            "[download] 100% of  801.55MiB in 00:10:22 at  1.29MiB/s",
            "[download] Destination: Danny Jones/S26E0831 - Title.f251.webm",
            "[download]  40.0% of   12.34MiB at  250.00KiB/s ETA 00:02",
            "[download] 100% of   12.34MiB in 00:00:51 at  240.00KiB/s",
        ]
        state = feed(gui, state, lines)
        pm = state["progress_manager"]
        self.assertEqual(pm.download_count, 2)

        emitted = [args[0] for args in gui.signals.update_dock_progress.emitted]
        self.assertTrue(emitted)
        for previous, current in zip(emitted, emitted[1:]):
            self.assertGreaterEqual(current, previous, f"emitted: {emitted}")

        # The video's last line must not already have been 100%: that is the
        # jump-back the user saw.
        self.assertLess(max(emitted[:-1]), 1.0)
        pm.mark_complete()
        self.assertAlmostEqual(pm.get_combined_fraction(), 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
