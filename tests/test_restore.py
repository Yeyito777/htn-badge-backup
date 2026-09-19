import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import badge_backup as app
from test_backup import device


def full_image():
    """Entirely synthetic fixture; contains no badge firmware or ROM assets."""
    from esptool.bin_image import ESP32C3FirmwareImage, ImageSegment
    image = ESP32C3FirmwareImage()
    image.flash_size_freq = 0x20
    image.segments = [ImageSegment(0x3FC80000, b"TEST" * 8)]
    image.segments[0].name = ".test"
    binary = image.save(None)
    data = bytearray(b"\xff" * app.FLASH_SIZE)
    data[:len(binary)] = binary
    data[0x10000:0x10000 + len(binary)] = binary
    table = b"".join(
        struct.pack("<HBBII16sI", 0x50AA, kind, sub, off, size, label, 0)
        for kind, sub, off, size, label in [
            (1, 2, 0x9000, 0x4000, b"nvs"), (1, 1, 0xD000, 0x1000, b"phy_init"),
            (0, 0, 0x10000, 0x2A0000, b"factory"), (1, 0x83, 0x2B0000, 0x140000, b"storage")]
    )
    table += b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(table).digest()
    data[0x8000:0x8000 + len(table)] = table
    return bytes(data)


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "original"
        self.source.mkdir()
        self.data = full_image()
        (self.source / "flash.bin").write_bytes(self.data)
        self.metadata = {"schema_version": 1, "state": "verified", "size": app.FLASH_SIZE,
                         "usb_serial": "test-device", "chip": "esp32c3",
                         "sha256": hashlib.sha256(self.data).hexdigest()}
        app.write_manifest(self.source, self.metadata)
        self.calls = []
        self.security = "Secure Boot: Disabled\nFlash Encryption: Disabled\n"
        self.failure = None
        self.safety_failure = False

    def fake_safety(self, directory, identity, manual_boot=False, stay_in_bootloader=False):
        self.calls.append("safety")
        if self.safety_failure:
            raise RuntimeError("backup verification failed")
        self.assertTrue(stay_in_bootloader)
        directory.mkdir(parents=True)
        (directory / "flash.bin").write_bytes(self.data)
        app.write_manifest(directory, self.metadata)
        return self.metadata

    def fake_invoke(self, identity, command, arguments, directory, **options):
        self.calls.append(command)
        if command == self.failure:
            raise RuntimeError("simulated device failure")
        if command == "get-security-info":
            return self.security
        if command == "write-flash":
            self.assertTrue(options["_restore"])
            self.assertEqual(arguments[0], 0)
            self.assertNotEqual(arguments[1], self.source / "flash.bin")
            self.assertEqual(Path(arguments[1]).read_bytes(), self.data)
        if command == "run":
            self.assertEqual(options["after"], "hard-reset")
        return ""

    def run_restore(self, identity="test-device", confirmed=True):
        with patch.object(app, "ports", return_value=[device(serial=identity)]), \
             patch.object(app, "_backup_unlocked", side_effect=self.fake_safety), \
             patch.object(app, "invoke", side_effect=self.fake_invoke):
            return app.restore(self.source, confirmed=confirmed,
                               backup_root=self.root / "backups",
                               history_root=self.root / "history")

    def test_restore_order_and_frozen_image(self):
        report = self.run_restore()
        self.assertEqual(self.calls, ["safety", "get-security-info", "write-flash", "verify-flash", "run"])
        self.assertEqual(report["state"], "verified")
        self.assertTrue(report["boot_requested"])
        self.assertEqual(app.verify_local(report["safety_backup"])["state"], "verified")

    def test_confirmation_required_before_hardware(self):
        with self.assertRaises(ValueError):
            self.run_restore(confirmed=False)
        self.assertEqual(self.calls, [])

    def test_other_badge_refused_before_hardware(self):
        with self.assertRaisesRegex(ValueError, "different badge"):
            self.run_restore(identity="someone-else")
        self.assertEqual(self.calls, [])

    def test_corrupt_source_refused_before_hardware(self):
        (self.source / "flash.bin").write_bytes(b"\0" * app.FLASH_SIZE)
        with self.assertRaises(ValueError):
            self.run_restore()
        self.assertEqual(self.calls, [])

    def test_plaintext_images_verified(self):
        app.validate_layout(self.data)
        app.validate_images(self.data)
        corrupted = bytearray(self.data)
        corrupted[0x10000 + 40] ^= 1
        with self.assertRaises(Exception):
            app.validate_images(corrupted)

    def test_other_chip_image_refused(self):
        corrupt = bytearray(self.data)
        corrupt[12] = 9
        with self.assertRaises(ValueError):
            app.validate_images(corrupt)

    def test_partition_corruption_and_extra_entries_refused(self):
        for offset in (0x8004, 0x8080, 0x8090, 0x80A0):
            corrupt = bytearray(self.data)
            corrupt[offset] ^= 1
            with self.assertRaises(ValueError):
                app.validate_layout(corrupt)

    def test_failed_safety_backup_never_writes(self):
        self.safety_failure = True
        with self.assertRaises(RuntimeError):
            self.run_restore()
        self.assertEqual(self.calls, ["safety"])

    def test_changed_current_layout_never_writes(self):
        original = self.fake_safety
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            path = args[0] / "flash.bin"
            data = bytearray(path.read_bytes())
            data[0x8004] ^= 1
            path.write_bytes(data)
            info = dict(self.metadata, sha256=hashlib.sha256(data).hexdigest())
            app.write_manifest(args[0], info)
            return result
        with patch.object(self, "fake_safety", side_effect=changed):
            with self.assertRaises(ValueError):
                self.run_restore()
        self.assertNotIn("write-flash", self.calls)

    def test_corrupt_safety_copy_never_writes(self):
        original = self.fake_safety
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            (args[0] / "flash.bin").write_bytes(b"\0" * app.FLASH_SIZE)
            return result
        with patch.object(self, "fake_safety", side_effect=changed):
            with self.assertRaises(ValueError):
                self.run_restore()
        self.assertNotIn("write-flash", self.calls)

    def test_source_changed_during_device_work_uses_frozen_bytes(self):
        original = self.fake_safety
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.source / "flash.bin").write_bytes(b"\0" * app.FLASH_SIZE)
            return result
        with patch.object(self, "fake_safety", side_effect=changed):
            self.run_restore()  # fake_invoke asserts the staged bytes are unchanged.

    def test_unknown_or_enabled_security_refused(self):
        for text in ("", "Secure Boot: Enabled\nFlash Encryption: Disabled\n",
                     "Secure Boot: Disabled\nFlash Encryption: Enabled\n",
                     "Secure Boot: Disabled\nFlash Encryption: Unknown\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                app.check_security(text)
        self.security = "Secure Boot: Enabled\nFlash Encryption: Disabled\n"
        with self.assertRaises(ValueError):
            self.run_restore()
        self.assertNotIn("write-flash", self.calls)

    def test_failed_write_does_not_reboot_or_verify(self):
        self.failure = "write-flash"
        with self.assertRaises(RuntimeError):
            self.run_restore()
        self.assertNotIn("run", self.calls)
        self.assertNotIn("verify-flash", self.calls)

    def test_failed_verify_does_not_reboot(self):
        self.failure = "verify-flash"
        with self.assertRaises(RuntimeError):
            self.run_restore()
        self.assertNotIn("run", self.calls)
        report = json.loads(next((self.root / "history").glob("*/manifest.json")).read_text())
        self.assertEqual(report["state"], "failed")

    def test_reboot_failure_keeps_verified_state(self):
        self.failure = "run"
        with self.assertRaises(RuntimeError):
            self.run_restore()
        report = json.loads(next((self.root / "history").glob("*/manifest.json")).read_text())
        self.assertEqual(report["state"], "verified")
        self.assertFalse(report["boot_requested"])

    def test_two_process_locks_cannot_overlap(self):
        # No device access. Unique identity to avoid interacting with real users.
        identity = "test-lock-" + self.root.name
        with app.device_lock(identity):
            with self.assertRaises(RuntimeError):
                with app.device_lock(identity):
                    self.fail("Second lock unexpectedly acquired")
        with app.device_lock(identity):
            pass
