"""
Unit tests for the dock progress-overlay throttle — no Qt event loop.

setDockProgressOverlay() used to repaint the dock tile on every yt-dlp
progress line: each call allocates an NSImage, repaints the app icon and
calls -display(). The throttle (should_redraw_dock) keeps a repaint only when
the fraction has actually moved, while still guaranteeing that the 0.0 and 1.0
endpoints are painted so a download never looks stuck short of full.

The pure helper is tested directly; the real method is exercised through a
harness with a fake dock tile so the AppKit path is covered without a window.

Run from the repo root:
    python3 -m unittest temp.test_dock_throttle -v
"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app
import ytdl.app as ytdl_app
from ytdl.app import DOCK_PROGRESS_MIN_DELTA, should_redraw_dock


class TestShouldRedrawDock(unittest.TestCase):
    def test_first_draw_always_repaints(self):
        self.assertTrue(should_redraw_dock(-1.0, 0.0))
        self.assertTrue(should_redraw_dock(-1.0, 0.5))

    def test_repeated_identical_value_is_throttled(self):
        self.assertFalse(should_redraw_dock(0.5, 0.5))

    def test_sub_threshold_move_is_throttled(self):
        # Moves smaller than 0.005 are not visible, so skip the repaint.
        self.assertFalse(should_redraw_dock(0.500, 0.501))
        self.assertFalse(should_redraw_dock(0.500, 0.502))
        self.assertFalse(
            should_redraw_dock(0.500, 0.500 + DOCK_PROGRESS_MIN_DELTA / 4)
        )

    def test_at_or_above_threshold_repaints(self):
        self.assertTrue(should_redraw_dock(0.500, 0.505))
        self.assertTrue(should_redraw_dock(0.500, 0.600))
        self.assertTrue(should_redraw_dock(0.500, 0.400))

    def test_endpoints_always_repaint(self):
        # A final sub-threshold step must still land at exactly 1.0.
        self.assertTrue(should_redraw_dock(0.999, 1.0))
        self.assertTrue(should_redraw_dock(0.9999, 1.0))
        self.assertTrue(should_redraw_dock(0.001, 0.0))

    def test_out_of_range_is_clamped(self):
        # A value past 1.0 still ends at the 1.0 endpoint and repaints.
        self.assertTrue(should_redraw_dock(0.5, 1.4))
        # Negative values clamp to 0.0, an endpoint that always repaints.
        self.assertTrue(should_redraw_dock(0.5, -0.3))

    def test_delta_constant_is_half_a_percent(self):
        self.assertAlmostEqual(DOCK_PROGRESS_MIN_DELTA, 0.005)


class FakeDockTile:
    """Counts repaints instead of touching AppKit."""

    def __init__(self):
        self.redraws = 0
        self.cleared = 0

    def size(self):
        return SimpleNamespace(width=128, height=128)

    def setContentView_(self, _view):
        self.redraws += 1

    def display(self):
        pass


class TestDockOverlayThrottle(unittest.TestCase):
    """The real methods, wired to a fake tile with the AppKit calls stubbed."""

    def setUp(self):
        # The drawing calls need a live NSGraphicsContext and a real app
        # icon; stub them on the module that defines the methods so only the
        # throttle logic is exercised.
        names = (
            "NSApplication", "NSImage", "NSImageView",
            "NSColor", "NSBezierPath", "NSMakeRect",
        )
        for name in names:
            patcher = mock.patch.object(ytdl_app, name, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _harness(self):
        h = SimpleNamespace()
        h.setDockProgressOverlay = app.YTDLPDownloaderGUI.setDockProgressOverlay.__get__(h)
        h.clearDockProgress = app.YTDLPDownloaderGUI.clearDockProgress.__get__(h)
        h.dockTile = FakeDockTile()
        h._last_dock_fraction = -1.0
        return h

    def test_repeated_updates_do_not_repaint_every_time(self):
        h = self._harness()
        # 200 updates, 0.0001 apart: 0.02 of total movement, so the 0.005
        # threshold allows ~4 repaints instead of 200.
        for i in range(200):
            h.setDockProgressOverlay(0.5 + i * 0.0001)
        total_movement = 199 * 0.0001
        expected = 1 + int(total_movement / DOCK_PROGRESS_MIN_DELTA)
        self.assertEqual(h.dockTile.redraws, expected)
        self.assertLess(h.dockTile.redraws, 200)
        # The cache holds the last DRAWN fraction, not the newest value: a
        # throttled update is deliberately not recorded, so the next draw is
        # still measured against what is actually on screen.
        self.assertAlmostEqual(
            h._last_dock_fraction,
            0.5 + (expected - 1) * DOCK_PROGRESS_MIN_DELTA,
            places=6,
        )

    def test_realistic_repeated_percent_lines_are_collapsed(self):
        """yt-dlp re-emits the same percent many times per update."""
        h = self._harness()
        updates = 0
        for percent in range(0, 101):
            for _ in range(10):  # ten lines per percent step
                h.setDockProgressOverlay(percent / 100.0)
                updates += 1
        # 1010 updates, but the bar can only move in 0.005 steps.
        self.assertEqual(updates, 1010)
        self.assertLessEqual(h.dockTile.redraws, 201)
        self.assertEqual(h._last_dock_fraction, 1.0)

    def test_final_one_hundred_percent_always_paints(self):
        h = self._harness()
        h.setDockProgressOverlay(0.999)
        before = h.dockTile.redraws
        h.setDockProgressOverlay(1.0)  # sub-threshold final step
        self.assertEqual(h.dockTile.redraws, before + 1)
        self.assertEqual(h._last_dock_fraction, 1.0)

    def test_clear_resets_cache_so_next_download_paints(self):
        h = self._harness()
        h.setDockProgressOverlay(0.5)
        h.clearDockProgress()
        self.assertEqual(h._last_dock_fraction, -1.0)
        redraws = h.dockTile.redraws
        # A new download at the same fraction must paint its first frame.
        h.setDockProgressOverlay(0.5)
        self.assertEqual(h.dockTile.redraws, redraws + 1)

    def test_no_dock_tile_is_a_noop(self):
        h = self._harness()
        h.dockTile = None
        h.setDockProgressOverlay(0.5)  # must not raise
        h.clearDockProgress()  # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
