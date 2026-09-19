#!/usr/bin/env python3
"""Read-only, verified full-flash backups for the HTN 2026 ESP32-C3 badge."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

FLASH_SIZE = 4 * 1024 * 1024
USB_ID = (0x303A, 0x1001)
READ_ONLY_COMMANDS = frozenset({"flash-id", "read-flash", "verify-flash", "run"})


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


def invoke(identity, command, arguments, directory, before="no-reset", after="no-reset"):
    if command not in READ_ONLY_COMMANDS:
        raise ValueError("This tool only permits read/verify/reset operations.")
    # Check identity again before every command; never follow a reused port
    # to another badge after a disconnect.
    device = select_device(serial=identity)
    argv = [
        sys.executable, "-m", "esptool",
        "--chip", "esp32c3", "--port", device.device, "--baud", "460800",
        "--before", before, "--after", after, command, *map(str, arguments),
    ]
    print(f"{command}: {device.device}", flush=True)
    with (directory / "operations.log").open("a") as log:
        log.write("\nCOMMAND " + json.dumps(argv) + "\n")
        log.flush()
        result = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT,
                                timeout=300, check=False)
    if result.returncode:
        raise RuntimeError(f"esptool {command} failed; see {directory / 'operations.log'}")


def check_flash_size(log):
    # esptool is pinned because this human-readable label is version-specific.
    if not re.search(r"Detected flash size:\s*4MB\b", log):
        raise ValueError("Expected 4 MiB flash; refusing an unrecognized flash size.")


def verify_local(directory):
    directory = Path(directory).resolve()
    metadata = json.loads((directory / "manifest.json").read_text())
    if metadata.get("schema_version") != 1 or metadata.get("state") != "verified":
        raise ValueError("This folder does not contain a completed, verified backup.")
    data = (directory / "flash.bin").read_bytes()
    if len(data) != FLASH_SIZE or metadata.get("size") != FLASH_SIZE:
        raise ValueError("Backup size is not exactly 4 MiB.")
    digest = hashlib.sha256(data).hexdigest()
    if digest != metadata.get("sha256"):
        raise ValueError("Backup SHA-256 mismatch: file or manifest has changed.")
    return metadata


def backup(directory, serial=None, port=None, manual_boot=False, stay_in_bootloader=False):
    device = select_device(serial=serial, port=port)
    identity = device.serial_number
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


def main():
    if os.name == "posix":
        os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("list", help="List candidate USB devices; no connection/reset")
    create = commands.add_parser("backup", help="Reset, read all flash, verify, reboot")
    create.add_argument("--serial", help="USB serial from 'list' (recommended)")
    create.add_argument("--port", help="Explicit port; must still match Espressif USB ID")
    create.add_argument("--output", type=Path, help="NEW directory; default backups/UTC-timestamp")
    create.add_argument("--manual-boot", action="store_true", help="Already held START while connecting USB")
    create.add_argument("--stay-in-bootloader", action="store_true", help="Do not reboot after verification")
    verify = commands.add_parser("verify", help="Verify local file size/hash; no device access")
    verify.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "list":
            for p in ports():
                if (p.vid, p.pid) == USB_ID:
                    print(f"{p.device}\tserial={p.serial_number or '(missing)'}\t{p.description}")
        elif args.action == "verify":
            metadata = verify_local(args.directory)
            print(f"Local integrity OK: {metadata['sha256']}")
        else:
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            backup(args.output or Path("backups") / stamp, args.serial, args.port,
                   args.manual_boot, args.stay_in_bootloader)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        print("No automatic erase/restore is attempted. A failed backup is not a recovery image.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
