"""Unit tests for the log-viewer Clear action's file truncation (no Qt).

`_truncate_log_file()` empties app.log through the logging FileHandler's own
stream while holding that handler's lock. That matters for two reasons:

* the stream offset stays consistent for any file mode. logging's FileHandler
  defaults to append mode (``mode='a'``), where a naive ``open(path, 'w')``
  behind its back still works because writes always land at EOF - but with a
  non-append mode the next record would be written at the stale offset, i.e. a
  sparse file padded with NUL bytes;
* an in-flight emit from a worker thread (a download logs continuously) can
  never interleave with the truncation.

Run from the repo root:
    python3 -m unittest temp.test_log_clear -v
"""
import logging
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app  # noqa: E402


class Harness:
    """Binds the real method - it only touches os + logging, no Qt/self state."""

    _truncate_log_file = app.YTDLPDownloaderGUI._truncate_log_file


class LogTruncateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_path = os.path.join(self.tmp.name, "app.log")

        # The app configures the root logger at import time (real log file +
        # stderr). Keep those handlers aside so these tests never touch the
        # real app.log, and restore them afterwards.
        self.root = logging.getLogger()
        self._saved_handlers = self.root.handlers[:]
        self._saved_level = self.root.level
        self.root.handlers = []
        self.root.setLevel(logging.INFO)

    def tearDown(self):
        for handler in self.root.handlers:
            handler.close()
        self.root.handlers = self._saved_handlers
        self.root.setLevel(self._saved_level)

    def _add_file_handler(self, path=None, mode="a"):
        handler = logging.FileHandler(
            path or self.log_path, mode=mode, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.root.addHandler(handler)
        return handler

    @staticmethod
    def _log(message):
        logging.getLogger("log-clear-test").info(message)

    def _read(self, path=None):
        with open(path or self.log_path, "rb") as f:
            return f.read()

    def test_file_is_empty_after_clear(self):
        self._add_file_handler()
        self._log("first entry")
        self.assertIn(b"first entry", self._read())

        Harness()._truncate_log_file(self.log_path)
        self.assertEqual(self._read(), b"")

    def test_only_the_next_record_is_kept(self):
        self._add_file_handler()
        self._log("first entry")
        Harness()._truncate_log_file(self.log_path)
        self._log("second entry")
        self.assertEqual(self._read(), b"second entry\n")

    def test_offset_stays_consistent_for_non_append_handlers(self):
        """A non-append handler would otherwise pad the file with NUL bytes."""
        self._add_file_handler(mode="w")
        self._log("first entry")
        Harness()._truncate_log_file(self.log_path)
        self._log("second entry")
        content = self._read()
        self.assertEqual(content, b"second entry\n")
        self.assertNotIn(bytes([0]), content)

    def test_waits_for_the_handler_lock(self):
        """No interleaving with an emit that is currently writing."""
        handler = self._add_file_handler()
        finished = threading.Event()

        def clear():
            Harness()._truncate_log_file(self.log_path)
            finished.set()

        handler.acquire()
        try:
            worker = threading.Thread(target=clear, daemon=True)
            worker.start()
            self.assertFalse(
                finished.wait(0.3), "truncate must wait for the handler lock"
            )
        finally:
            handler.release()

        self.assertTrue(finished.wait(2), "truncate must finish once released")

    def test_truncates_without_any_handler(self):
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("stale data\n")
        Harness()._truncate_log_file(self.log_path)
        self.assertEqual(self._read(), b"")

    def test_missing_file_is_created_empty(self):
        self.assertFalse(os.path.exists(self.log_path))
        Harness()._truncate_log_file(self.log_path)
        self.assertTrue(os.path.exists(self.log_path))
        self.assertEqual(self._read(), b"")

    def test_other_log_files_are_untouched(self):
        other = os.path.join(self.tmp.name, "other.log")
        self._add_file_handler(other)
        self._log("other keeps this")
        Harness()._truncate_log_file(self.log_path)
        self.assertIn(b"other keeps this", self._read(other))

    def test_closed_handler_reopens_at_offset_zero(self):
        handler = self._add_file_handler()
        self._log("before close")
        handler.close()  # stream is None; the file still holds the record
        Harness()._truncate_log_file(self.log_path)
        self.assertEqual(self._read(), b"")
        self._log("after reopen")  # FileHandler.emit() reopens the stream
        self.assertEqual(self._read(), b"after reopen\n")


if __name__ == "__main__":
    unittest.main()
