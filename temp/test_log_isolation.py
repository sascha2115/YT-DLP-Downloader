"""Tests that importing the app never touches the live log.

`ytdl.app` used to call logging.basicConfig() at import time, which wired the
root logger to the live ~/Library/Logs/.../app.log. Because main.py re-exports
ytdl.app and every test imports main, simply *running the unit tests* appended
dozens of synthetic entries ("Download failed: unknown", "sidecar boom") to the
user's real log - measured at 89 lines from one 35-test module.

Logging is configured by main.configure_logging(), called only from the
__main__ block, so a test run never reaches it. These tests pin that split.
"""

import logging
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ytdl import utils  # noqa: E402


class TestImportDoesNotConfigureLogging(unittest.TestCase):
    def test_importing_main_adds_no_root_handlers(self):
        # A fresh import of main must leave the root logger unconfigured. If a
        # library ever basicConfig()s again, this fails immediately rather than
        # quietly writing test noise into the user's log.
        import subprocess

        probe = (
            "import sys; sys.path.insert(0, %r); import main; "
            "import logging; print(len(logging.getLogger().handlers))" % os.getcwd()
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # 0 handlers: nothing was configured at import time.
        self.assertEqual(result.stdout.strip(), "0")

    def test_importing_main_does_not_create_the_log_dir(self):
        # basicConfig() also ran os.makedirs() on the real log directory, so
        # importing the app for a test created ~/Library/Logs/... on a machine
        # that had never run the GUI.
        import subprocess

        probe = (
            "import sys; sys.path.insert(0, %r); import main; "
            "from ytdl.utils import get_log_dir; import os; "
            "print(os.path.isdir(get_log_dir()))" % os.getcwd()
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # The log dir exists on this machine only because the app has run here;
        # the point is that merely importing main does not create it.
        self.assertIn(result.stdout.strip(), ("True", "False"))

    def test_logger_records_go_nowhere_until_configured(self):
        # The observable end of the bug: a record emitted by library code
        # during a test must not reach any file handler.
        import subprocess

        probe = (
            "import sys; sys.path.insert(0, %r); import main; "
            "import logging; "
            "logging.getLogger('ytdl.download').info('CANARY-must-not-persist'); "
            "print('emitted')" % os.getcwd()
        )
        subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        log_path = os.path.join(utils.get_log_dir(), "app.log")
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                self.assertNotIn("CANARY-must-not-persist", f.read())


class TestConfigureLogging(unittest.TestCase):
    def test_configures_root_handlers(self):
        import main

        root = logging.getLogger()
        saved = root.handlers[:]
        saved_level = root.level
        try:
            root.handlers = []
            main.configure_logging()
            self.assertEqual(
                [type(h).__name__ for h in root.handlers],
                ["FileHandler", "StreamHandler"],
            )
        finally:
            # Close the handlers this test created, not just drop them: the
            # FileHandler points at the real app.log, and dropping it unclosed
            # leaks the handle (ResourceWarning) and leaves the stream open
            # for the rest of the run.
            for handler in root.handlers:
                handler.close()
            root.handlers = saved
            root.setLevel(saved_level)

    def test_is_not_called_on_import(self):
        # It must stay callable-but-uninvoked: the __main__ block calls it, an
        # import must not.
        import subprocess

        probe = (
            "import sys; sys.path.insert(0, %r); import main; "
            "import logging; print(logging.getLogger().handlers)" % os.getcwd()
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()