#!/usr/bin/env python3
"""Menu-driven backup and guarded restore for the HTN 2026 ESP32-C3 badge."""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys

FLASH_SIZE = 4 * 1024 * 1024
USB_ID = (0x303A, 0x1001)
ROOT = Path(__file__).resolve().parent
BACKUPS = ROOT / "backups"
READ_ONLY_COMMANDS = frozenset({"flash-id", "read-flash", "verify-flash", "run", "get-security-info"})


def timestamp():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


@contextlib.contextmanager
def device_lock(identity):
    """Cross-platform advisory lock shared by copies of this tool."""
    folder = Path.home() / ".htn-badge-backup" / "locks"
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    name = hashlib.sha256(identity.lower().encode()).hexdigest() + ".lock"
    with (folder / name).open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another backup/restore is using this badge. Close it first.") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def ports():
    from serial.tools import list_ports
    return list(list_ports.comports())


def select_device(serial=None, port=None):
    candidates = [
        p for p in ports()
        if (p.vid, p.pid) == USB_ID
        and (serial is None or (p.serial_number or "").lower() == serial.lower())
        and (port is None or p.device == port)
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"Expected exactly one matching Espressif USB device; found {len(candidates)}. "
            "Use 'list' and choose --serial or --port. Close other monitors."
        )
    device = candidates[0]
    if not device.serial_number:
        raise ValueError("USB serial number is missing; cannot safely pin device identity.")
    return device


def write_manifest(directory, data):
    temp = directory / "manifest.tmp"
    temp.write_text(json.dumps(data, indent=2) + "\n")
    temp.chmod(0o600)
    temp.replace(directory / "manifest.json")


def invoke(identity, command, arguments, directory, before="no-reset", after="no-reset",
           _restore=False):
    if command not in READ_ONLY_COMMANDS and not (_restore and command == "write-flash"):
        raise ValueError("Flash writes are only permitted by the guarded restore workflow.")
    if command == "write-flash" and (len(arguments) != 2 or arguments[0] != 0):
        raise ValueError("Restore only accepts one verified full image at offset zero.")
    # Check identity again before every command; never follow a reused port
    # to another badge after a disconnect.
    device = select_device(serial=identity)
    argv = [
        sys.executable, "-m", "esptool",
        "--chip", "esp32c3", "--port", device.device, "--baud", "460800",
        "--before", before, "--after", after, command, *map(str, arguments),
    ]
    labels = {"flash-id": "Checking badge and flash size",
              "read-flash": "Reading the complete flash (this can take a minute)",
              "verify-flash": "Verifying saved bytes against the badge",
              "get-security-info": "Checking security settings",
              "write-flash": "Restoring the selected image — do not disconnect",
              "run": "Restarting the badge"}
    print(f"{labels[command]} · {device.device}", flush=True)
    with (directory / "operations.log").open("a") as log:
        log.write("\nCOMMAND " + json.dumps(argv) + "\n")
        log.flush()
        offset = log.tell()
        result = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT,
                                timeout=300, check=False)
    if result.returncode:
        raise RuntimeError(f"esptool {command} failed; see {directory / 'operations.log'}")
    with (directory / "operations.log").open() as log:
        log.seek(offset)
        return log.read()


def check_flash_size(log):
    # esptool is pinned because this human-readable label is version-specific.
    if not re.search(r"Detected flash size:\s*4MB\b", log):
        raise ValueError("Expected 4 MiB flash; refusing an unrecognized flash size.")


def verify_local(directory):
    directory = Path(directory).resolve()
    metadata = json.loads((directory / "manifest.json").read_text())
    if metadata.get("schema_version") != 1 or metadata.get("state") != "verified":
        raise ValueError("This folder does not contain a completed, verified backup.")
    if metadata.get("chip") != "esp32c3" or not isinstance(metadata.get("usb_serial"), str) or not metadata["usb_serial"]:
        raise ValueError("Backup chip/device identity is missing or unsupported.")
    if (directory / "flash.bin").stat().st_size != FLASH_SIZE:
        raise ValueError("Backup size is not exactly 4 MiB.")
    data = (directory / "flash.bin").read_bytes()
    if len(data) != FLASH_SIZE or metadata.get("size") != FLASH_SIZE:
        raise ValueError("Backup size is not exactly 4 MiB.")
    digest = hashlib.sha256(data).hexdigest()
    if digest != metadata.get("sha256"):
        raise ValueError("Backup SHA-256 mismatch: file or manifest has changed.")
    return metadata


def backup(directory, serial=None, port=None, manual_boot=False, stay_in_bootloader=False):
    device = select_device(serial=serial, port=port)
    with device_lock(device.serial_number):
        return _backup_unlocked(directory, device.serial_number, manual_boot, stay_in_bootloader)


def _backup_unlocked(directory, identity, manual_boot=False, stay_in_bootloader=False):
    directory = Path(directory).resolve()
    # New directory only: never overwrite an earlier backup or follow an
    # existing backup directory's symlinks.
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    metadata = {
        "schema_version": 1, "state": "started",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "usb_serial": identity, "chip": "esp32c3", "size": FLASH_SIZE,
        "boot_requested": False,
    }
    write_manifest(directory, metadata)
    print(f"Backing up device {identity} to {directory}", flush=True)
    print("This resets/stops the running app, but does not write or erase flash.", flush=True)
    try:
        invoke(identity, "flash-id", [], directory,
               before="no-reset" if manual_boot else "usb-reset")
        check_flash_size((directory / "operations.log").read_text())
        invoke(identity, "read-flash", [0, "ALL", directory / "flash.bin"], directory)
        image = directory / "flash.bin"
        image.chmod(0o600)
        data = image.read_bytes()
        if len(data) != FLASH_SIZE:
            raise ValueError(f"Incomplete or unexpected flash dump: {len(data)} bytes.")
        metadata["sha256"] = hashlib.sha256(data).hexdigest()
        metadata["state"] = "read_unverified"
        write_manifest(directory, metadata)
        # esptool requests the device's flash digest and compares the complete
        # saved file while the original app remains stopped.
        invoke(identity, "verify-flash", [0, image], directory)
        metadata["state"] = "verified"
        write_manifest(directory, metadata)
        (directory / "SHA256SUMS").write_text(metadata["sha256"] + "  flash.bin\n")
        verify_local(directory)
        if not stay_in_bootloader:
            # 'run' alone is not sufficient for ESP32-C3; request hard reset.
            invoke(identity, "run", [], directory, after="hard-reset")
            metadata["boot_requested"] = True
            write_manifest(directory, metadata)
        print(f"VERIFIED: {image}\nSHA-256: {metadata['sha256']}", flush=True)
        return metadata
    except (Exception, KeyboardInterrupt) as error:
        # Do not mislabel a verified dump if only the final reboot failed.
        metadata["error"] = f"{type(error).__name__}: {error}"
        if metadata["state"] != "verified":
            metadata["state"] = "failed"
        write_manifest(directory, metadata)
        raise


def validate_layout(data):
    """Restrict restore to the known plaintext HTN 2026 partition layout."""
    if len(data) != FLASH_SIZE:
        raise ValueError("Restore requires exactly 4 MiB.")
    expected = [
        (1, 2, 0x9000, 0x4000, b"nvs"),
        (1, 1, 0xD000, 0x1000, b"phy_init"),
        (0, 0, 0x10000, 0x2A0000, b"factory"),
        (1, 0x83, 0x2B0000, 0x140000, b"storage"),
    ]
    table = data[0x8000:0x9000]
    for index, (kind, subtype, offset, size, label) in enumerate(expected):
        values = struct.unpack_from("<HBBII16sI", table, index * 32)
        if values != (0x50AA, kind, subtype, offset, size, label.ljust(16, b"\0"), 0):
            raise ValueError("Unsupported or encrypted partition layout; restore refused.")
    if table[128:144] != b"\xeb\xeb" + b"\xff" * 14:
        raise ValueError("Partition-table digest marker is missing.")
    # MD5 here is the ESP partition format's integrity field, not authentication.
    if hashlib.md5(table[:128]).digest() != table[144:160]:
        raise ValueError("Partition-table digest mismatch.")
    if table[160:] != b"\xff" * (len(table) - 160):
        raise ValueError("Unexpected additional partition-table data.")


def validate_images(data):
    from esptool.bin_image import LoadFirmwareImage
    for name, start, end in [("bootloader", 0, 0x8000), ("application", 0x10000, 0x2B0000)]:
        image_data = data[start:end]
        if image_data[0] != 0xE9 or struct.unpack_from("<H", image_data, 12)[0] != 5:
            raise ValueError(f"Backup {name} is not a plaintext ESP32-C3 image.")
        image = LoadFirmwareImage("esp32c3", image_data)
        if image.checksum != image.calculate_checksum():
            raise ValueError(f"Backup {name} checksum mismatch.")
        if not image.append_digest or image.stored_digest != image.calc_digest:
            raise ValueError(f"Backup {name} SHA-256 image digest mismatch.")
        if image.flash_size_freq >> 4 != 2:
            raise ValueError(f"Backup {name} is not configured for 4 MiB flash.")


def check_security(text):
    for field in ("Secure Boot", "Flash Encryption"):
        states = re.findall(rf"^{field}:\s*(\w+)\s*$", text, re.MULTILINE)
        if states != ["Disabled"]:
            raise ValueError(f"{field} must be explicitly Disabled; restore refused.")


def restore(source, serial=None, port=None, *, confirmed=False, manual_boot=False,
            backup_root=None, history_root=None):
    """Restore to the SAME badge only, after a fresh verified safety backup."""
    if not confirmed:
        raise ValueError("Restore replaces firmware AND settings. Explicit confirmation required.")
    source = Path(source).resolve()
    metadata = verify_local(source)
    device = select_device(serial=serial, port=port)
    identity = device.serial_number
    if identity.lower() != metadata["usb_serial"].lower():
        raise ValueError("This backup belongs to a different badge. Restore refused.")
    # Freeze bytes before connecting to hardware; do not flash the user's
    # mutable source path. Recheck the same digest to close the reread race.
    data = (source / "flash.bin").read_bytes()
    if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
        raise ValueError("Backup changed while being opened.")
    validate_layout(data)
    validate_images(data)
    stamp = timestamp()
    history = Path(history_root or ROOT / ".restore-history") / stamp
    safety = Path(backup_root or BACKUPS) / ("before-restore-" + stamp)
    with device_lock(identity):
        history.mkdir(parents=True, mode=0o700, exist_ok=False)
        report = {"state": "started", "usb_serial": identity, "source": str(source),
                  "sha256": metadata["sha256"], "safety_backup": str(safety),
                  "boot_requested": False}
        write_manifest(history, report)
        try:
            print("1/4 Saving and verifying a fresh safety backup before any write.", flush=True)
            _backup_unlocked(safety, identity, manual_boot, stay_in_bootloader=True)
            security = invoke(identity, "get-security-info", [], history)
            check_security(security)
            verify_local(safety)
            current = (safety / "flash.bin").read_bytes()
            validate_layout(current)
            if current[0x8000:0x9000] != data[0x8000:0x9000]:
                raise ValueError("Current partition table differs; automatic restore refused.")
            frozen = history / "restore-image.bin"
            frozen.write_bytes(data)
            frozen.chmod(0o600)
            if hashlib.sha256(frozen.read_bytes()).hexdigest() != metadata["sha256"]:
                raise ValueError("Staged restore image failed its integrity check.")
            report["state"] = "writing"
            write_manifest(history, report)
            print("2/4 Restoring all flash. Keep USB connected; do not interrupt.", flush=True)
            invoke(identity, "write-flash", [0, frozen], history, _restore=True)
            print("3/4 Verifying all restored bytes against the badge.", flush=True)
            invoke(identity, "verify-flash", [0, frozen], history)
            report["state"] = "verified"
            write_manifest(history, report)
            print("4/4 Rebooting the restored application.", flush=True)
            invoke(identity, "run", [], history, after="hard-reset")
            report["boot_requested"] = True
            write_manifest(history, report)
            print(f"Restore verified. Safety backup: {safety}", flush=True)
            return report
        except (Exception, KeyboardInterrupt) as error:
            report["error"] = f"{type(error).__name__}: {error}"
            if report["state"] != "verified":
                report["state"] = "failed"
            write_manifest(history, report)
            print(f"Restore stopped. Logs: {history}\nSafety backup: {safety}", file=sys.stderr)
            if report["state"] == "verified":
                print("Restored flash was verified; only reboot/finalization failed. "
                      "Try a normal power cycle.", file=sys.stderr)
            else:
                print("If writing had started, the app may not boot. "
                      "Use START + USB recovery and retry the verified backup.", file=sys.stderr)
            raise


def main():
    if os.name == "posix":
        os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action")
    commands.add_parser("menu", help="Friendly interactive backup/restore menu (default)")
    commands.add_parser("list", help="List candidate USB devices; no connection/reset")
    create = commands.add_parser("backup", help="Reset, read all flash, verify, reboot")
    create.add_argument("--serial", help="USB serial from 'list' (recommended)")
    create.add_argument("--port", help="Explicit port; must still match Espressif USB ID")
    create.add_argument("--output", type=Path, help="NEW directory; default backups/UTC-timestamp")
    create.add_argument("--manual-boot", action="store_true", help="Already held START while connecting USB")
    create.add_argument("--stay-in-bootloader", action="store_true", help="Do not reboot after verification")
    verify = commands.add_parser("verify", help="Verify local file size/hash; no device access")
    verify.add_argument("directory", type=Path)
    recover = commands.add_parser("restore", help="Restore verified backup to the same badge")
    recover.add_argument("directory", type=Path)
    recover.add_argument("--serial")
    recover.add_argument("--port")
    recover.add_argument("--manual-boot", action="store_true")
    recover.add_argument("--yes", action="store_true", help="Confirm replacing ALL firmware and settings")
    args = parser.parse_args()
    try:
        if args.action in (None, "menu"):
            if not sys.stdin.isatty():
                raise ValueError("The menu needs an interactive terminal. Use --help for scripted commands.")
            from badge_ui import menu
            menu()
        elif args.action == "list":
            for p in ports():
                if (p.vid, p.pid) == USB_ID:
                    print(f"{p.device}\tserial={p.serial_number or '(missing)'}\t{p.description}")
        elif args.action == "verify":
            metadata = verify_local(args.directory)
            print(f"Local integrity OK: {metadata['sha256']}")
        elif args.action == "restore":
            restore(args.directory, args.serial, args.port,
                    confirmed=args.yes, manual_boot=args.manual_boot)
        else:
            backup(args.output or BACKUPS / timestamp(), args.serial, args.port,
                   args.manual_boot, args.stay_in_bootloader)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        print("No automatic recovery is attempted. Check the saved logs before retrying.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
