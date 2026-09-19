import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import badge_backup as app


def device(serial="test-device", path="/dev/test", vid=0x303A, pid=0x1001):
    return SimpleNamespace(serial_number=serial, device=path, vid=vid, pid=pid)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "new"
        self.data = b"\xA5" * app.FLASH_SIZE
        self.calls = []

    def fake_invoke(self, identity, command, arguments, directory, **options):
        self.calls.append((identity, command, arguments, options))
        if command == "flash-id":
            (directory / "operations.log").write_text("Detected flash size: 4MB\n")
        if command == "read-flash":
            (directory / "flash.bin").write_bytes(self.data)

    def run_backup(self, **options):
        with patch.object(app, "ports", return_value=[device()]), \
             patch.object(app, "invoke", side_effect=self.fake_invoke):
            return app.backup(self.folder, **options)

    def test_complete_backup_is_verified_and_rebooted(self):
        result = self.run_backup()
        self.assertEqual(result["state"], "verified")
        self.assertTrue(result["boot_requested"])
        self.assertEqual(result["sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertEqual([c[1] for c in self.calls],
                         ["flash-id", "read-flash", "verify-flash", "run"])
        self.assertEqual(self.calls[0][3]["before"], "usb-reset")
        self.assertEqual(self.calls[-1][3]["after"], "hard-reset")
        self.assertEqual(app.verify_local(self.folder)["state"], "verified")

    def test_manual_boot_and_no_reboot(self):
        self.run_backup(manual_boot=True, stay_in_bootloader=True)
        self.assertEqual(self.calls[0][3]["before"], "no-reset")
        self.assertNotIn("run", [c[1] for c in self.calls])

    def test_existing_output_is_never_overwritten(self):
        self.folder.mkdir()
        with self.assertRaises(FileExistsError):
            self.run_backup()
        self.assertEqual(self.calls, [])

    def test_ambiguous_device_refused(self):
        with patch.object(app, "ports", return_value=[device("one"), device("two")]):
            with self.assertRaises(ValueError):
                app.select_device()
            self.assertEqual(app.select_device(serial="two").serial_number, "two")

    def test_wrong_usb_identity_refused(self):
        with patch.object(app, "ports", return_value=[device(vid=0x1234)]):
            with self.assertRaises(ValueError):
                app.select_device(port="/dev/test")

    def test_missing_serial_refused(self):
        with patch.object(app, "ports", return_value=[device(serial=None)]):
            with self.assertRaises(ValueError):
                app.select_device()

    def test_case_insensitive_serial(self):
        with patch.object(app, "ports", return_value=[device(serial="ABCD")]):
            self.assertEqual(app.select_device(serial="abcd").serial_number, "ABCD")

    def test_wrong_flash_size_refused(self):
        for text in ["Detected flash size: 8MB", "", "Detected flash size: 64MB"]:
            with self.assertRaises(ValueError):
                app.check_flash_size(text)
        app.check_flash_size("Detected flash size: 4MB\n")

    def test_truncated_read_never_verified_or_rebooted(self):
        self.data = b"truncated"
        with self.assertRaises(ValueError):
            self.run_backup()
        self.assertEqual([c[1] for c in self.calls], ["flash-id", "read-flash"])
        self.assertEqual(json.loads((self.folder / "manifest.json").read_text())["state"], "failed")

    def test_corrupt_backup_detected(self):
        self.run_backup()
        data = bytearray(self.data)
        data[1234] ^= 1
        (self.folder / "flash.bin").write_bytes(data)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            app.verify_local(self.folder)

    def test_failed_device_verify_never_reboots(self):
        def fail(identity, command, arguments, directory, **options):
            self.fake_invoke(identity, command, arguments, directory, **options)
            if command == "verify-flash":
                raise RuntimeError("digest mismatch")
        with patch.object(app, "ports", return_value=[device()]), \
             patch.object(app, "invoke", side_effect=fail):
            with self.assertRaises(RuntimeError):
                app.backup(self.folder)
        self.assertNotIn("run", [c[1] for c in self.calls])
        with self.assertRaises(ValueError):
            app.verify_local(self.folder)

    def test_reboot_failure_preserves_verified_backup(self):
        def fail(identity, command, arguments, directory, **options):
            self.fake_invoke(identity, command, arguments, directory, **options)
            if command == "run":
                raise RuntimeError("could not reboot")
        with patch.object(app, "ports", return_value=[device()]), \
             patch.object(app, "invoke", side_effect=fail):
            with self.assertRaises(RuntimeError):
                app.backup(self.folder)
        self.assertEqual(app.verify_local(self.folder)["state"], "verified")
        self.assertFalse(app.verify_local(self.folder)["boot_requested"])

    def test_flash_writes_and_efuses_are_rejected(self):
        for command in ["write-flash", "erase-flash", "erase-region", "burn-efuse"]:
            with self.assertRaises(ValueError):
                app.invoke("test-device", command, [], self.folder)

    def test_device_identity_rechecked_before_subprocess(self):
        self.folder.mkdir()
        with patch.object(app, "ports", return_value=[device(serial="another-device")]), \
             patch.object(app.subprocess, "run") as process:
            with self.assertRaises(ValueError):
                app.invoke("test-device", "flash-id", [], self.folder)
            process.assert_not_called()

    def test_subprocess_is_chip_pinned_and_has_no_shell(self):
        self.folder.mkdir()
        with patch.object(app, "ports", return_value=[device()]), \
             patch.object(app.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as process:
            app.invoke("test-device", "read-flash", [0, "ALL", self.folder / "flash.bin"], self.folder)
        args, kwargs = process.call_args
        argv = args[0]
        self.assertEqual(argv[argv.index("--chip") + 1], "esp32c3")
        self.assertEqual(argv[argv.index("--before") + 1], "no-reset")
        self.assertEqual(argv[argv.index("--after") + 1], "no-reset")
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(kwargs["timeout"], 300)


if __name__ == "__main__":
    unittest.main()
