from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rich.console import Console
import badge_ui as ui
from test_backup import device


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.output = StringIO()
        self.console = Console(file=self.output, color_system=None, width=90)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_quit_without_device_access(self):
        with patch.object(ui.Prompt, "ask", return_value="0"), \
             patch.object(ui.backend, "ports") as ports:
            ui.menu(self.console, self.root)
        ports.assert_not_called()
        self.assertIn("Back up my badge", self.output.getvalue())

    def test_eof_exits_instead_of_looping(self):
        with patch.object(ui.Prompt, "ask", side_effect=EOFError), \
             patch.object(ui.backend, "ports") as ports:
            ui.menu(self.console, self.root)
        ports.assert_not_called()

    def test_backup_cancel_never_connects(self):
        with patch.object(ui.Prompt, "ask", side_effect=["1", "1", "1", "n", "0"]), \
             patch.object(ui.backend, "ports", return_value=[device()]), \
             patch.object(ui.backend, "backup") as backup:
            ui.menu(self.console, self.root)
        backup.assert_not_called()
        self.assertIn("Cancelled", self.output.getvalue())

    def test_backup_confirm_uses_chosen_badge(self):
        with patch.object(ui.Prompt, "ask", side_effect=["1", "1", "2", "y", "0"]), \
             patch.object(ui.backend, "ports", return_value=[device()]), \
             patch.object(ui.backend, "backup") as backup:
            ui.menu(self.console, self.root)
        self.assertEqual(backup.call_args.kwargs, {"serial": "test-device", "manual_boot": True})

    def test_restore_cancel_never_writes(self):
        with patch.object(ui.Prompt, "ask", side_effect=["2", "1", "1", "no", "0"]), \
             patch.object(ui, "choose_backup", return_value=self.root), \
             patch.object(ui.backend, "verify_local", return_value={"usb_serial": "test-device"}), \
             patch.object(ui.backend, "ports", return_value=[device()]), \
             patch.object(ui.backend, "restore") as restore:
            ui.menu(self.console, self.root)
        restore.assert_not_called()
        self.assertIn("Cancelled", self.output.getvalue())

    def test_restore_requires_exact_word(self):
        with patch.object(ui.Prompt, "ask", side_effect=["2", "1", "1", "RESTORE", "0"]), \
             patch.object(ui, "choose_backup", return_value=self.root), \
             patch.object(ui.backend, "verify_local", return_value={"usb_serial": "test-device"}), \
             patch.object(ui.backend, "ports", return_value=[device()]), \
             patch.object(ui.backend, "restore", return_value={"safety_backup": "safe"}) as restore:
            ui.menu(self.console, self.root)
        self.assertTrue(restore.call_args.kwargs["confirmed"])

    def test_mismatched_backup_blocked_in_ui(self):
        with patch.object(ui.Prompt, "ask", side_effect=["2", "1", "0"]), \
             patch.object(ui, "choose_backup", return_value=self.root), \
             patch.object(ui.backend, "verify_local", return_value={"usb_serial": "other"}), \
             patch.object(ui.backend, "ports", return_value=[device()]), \
             patch.object(ui.backend, "restore") as restore:
            ui.menu(self.console, self.root)
        restore.assert_not_called()
        self.assertIn("different badge", self.output.getvalue())

    def test_no_device_shows_help(self):
        with patch.object(ui.Prompt, "ask", side_effect=["1", "0"]), \
             patch.object(ui.backend, "ports", return_value=[]):
            ui.menu(self.console, self.root)
        self.assertIn("No badge found", self.output.getvalue())

    def test_bad_backup_error_returns_to_menu(self):
        with patch.object(ui.Prompt, "ask", side_effect=["3", "0"]), \
             patch.object(ui, "choose_backup", return_value=self.root), \
             patch.object(ui.backend, "verify_local", side_effect=ValueError("Checksum mismatch")):
            ui.menu(self.console, self.root)
        self.assertIn("Checksum mismatch", self.output.getvalue())
