# Hack the North · Badge Backup

**Back up and restore your HTN 2026 badge using a little terminal menu.**
No serial-port arguments, flash addresses, or esptool commands to memorize.

```text
╭─────────────────────────────────────────────────────╮
│ Hack the North · Badge Backup                       │
│ ESP32-C3 / 4 MiB • backups stay on this computer     │
│                                                     │
│ 1  Back up my badge                                  │
│ 2  Restore a saved backup                            │
│ 3  Check a saved backup                              │
│ 4  Connection / recovery help                        │
│ 0  Quit                                             │
╰─────────────────────────────────────────────────────╯
Choose: _
```

## Start here

1. Install **Python 3.10 or newer** if you don't have it.
2. Download this repository using **Code → Download ZIP**, then extract it,
   or clone it with Git.
3. Start the launcher:
   - **macOS:** open `start.command`.
   - **Windows:** open `start.bat`.
   - **Linux / any terminal:** run `python3 start.py`.
4. On first launch, type `y` to install the pinned dependencies into this
   folder's `.venv`. Future launches go straight to the menu.
5. Turn the badge's battery switch **off** while using USB. Connect a
   **USB data cable**, then choose an option.

If macOS will not open the downloaded script, open a terminal in the
extracted folder and run `python3 start.py` instead. You don't need to
disable system security settings. On Linux, use your distribution's serial
port permissions; do not run the whole tool as root.

Already installed the dependencies? `python badge_backup.py` also opens the
menu. ESP-IDF and a C compiler are **not** required.

## Back up

Choose **1**, select your badge, use the default automatic connection mode,
and confirm. The tool:

1. Restarts/stops the current app.
2. Reads all **4 MiB** of flash.
3. Compares the saved dump against the actual device while the app is stopped.
4. Records its SHA-256 and reboots the badge.

The menu shows progress and the saved folder. Backups live in **`backups/`**
beside the script. Copy the **entire backup folder** to another private,
reliable storage location.

**This captures what's installed now.** If you already installed a custom
game/app, the backup contains that—not the original factory firmware.

## Restore

Choose **2**, pick a saved backup (or paste a backup folder path), select the
**same badge it came from**, and type **`RESTORE`** at the warning.

> **A full restore replaces all firmware AND settings.** Contacts, app data,
> and progress revert to the selected backup. Keep USB connected and do not
> interrupt it.

Before writing anything, the tool:

- Checks the source backup's size, SHA-256, and original USB identity.
- Validates the known HTN partition table and the bootloader/application
  image checksums and SHA-256 digests.
- Saves and verifies a **fresh safety backup** of the badge's current flash.
- Refuses secure-boot, encrypted, unknown-security devices and source backups
  with an unsupported layout.
- Stages a private copy of the selected image, so changing the original
  backup file mid-operation cannot change what gets written.

It then restores the full image, verifies it against the device, and requests
a reboot. No eFuse changes, `--force`, or explicit erase-all commands are used.
Writing necessarily erases/replaces the affected flash sectors.

Your previous state remains in `backups/before-restore-.../`. Choose that
backup later if you want to go back. Detailed restore logs and the staged
image remain private in `.restore-history/`.

A full restore can repair an interrupted write even when the **current**
bootloader/partition table is damaged. The **selected source backup** must
still contain valid bootloader/application images and the known HTN layout.
A safety snapshot of an already broken device is not necessarily bootable.

**An interrupted/failed write can leave the app unable to boot.** The tool
does not automatically reboot after failed write verification or attempt
an automatic rollback. Use the recovery steps below and retry a verified
backup. If only the final reboot fails, verification remains recorded.

### What is supported?

The **HTN 2026 ESP32-C3 badge with 4 MiB flash**, native Espressif USB
`303a:1001`, and its original partition layout:

| Partition | Offset | Size |
|---|---:|---:|
| NVS | `0x9000` | `0x4000` |
| PHY | `0xd000` | `0x1000` |
| Factory application | `0x10000` | `0x2a0000` |
| Storage | `0x2b0000` | `0x140000` |

The USB ID is shared with other Espressif devices; it does not prove the
board is an HTN badge. Connect the intended board. Other badge years,
USB bridge chips, source backups with modified layouts, raw `.bin` files without this tool's
verified manifest, and restoring someone else's backup are not supported.
Backups made with the initial version of this repository are compatible.

## Connection / recovery help

Choose **4** in the menu, or:

1. Close browser IDEs, serial monitors, and other flashing tools.
2. Turn battery power **off** and unplug USB.
3. Hold **START**, reconnect USB, then release START.
4. Retry the operation and select **recovery mode** when asked.

START is GPIO9, the ESP32-C3 download strap. Don't hold it during ordinary
power-on if you want the installed application to start normally.

The tool locks the chosen badge against other copies of itself. Other tools
may not respect that lock: close them first. If an operation fails before
any flash write, a normal power cycle should restart the existing app.

## Privacy and limits

Backups can contain **credentials, contacts, Wi-Fi details, and personal
data**. Never upload `flash.bin`, manifests, or logs to GitHub. This repository
contains **no actual badge firmware or ROMs**.

Files are created privately on POSIX; check folder permissions on Windows
too. `.gitignore` excludes backups and restore history, but is not a security
boundary—do not force-add private files.

Backup folders contain `flash.bin`, `manifest.json`, `SHA256SUMS`, and
`operations.log`. Local hash checking detects accidental changes; it is
not authentication against someone who can edit both file and manifest.

Flash backups do **not** include eFuses or undo security provisioning or
hardware damage. Do not casually program eFuses, enable secure boot/flash
encryption, or disable download access. This tool does not bypass those
protections.

## Optional scripted commands

The menu is the recommended interface. For automation:

```sh
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

python badge_backup.py
python badge_backup.py list
python badge_backup.py backup --serial YOUR_USB_SERIAL
python badge_backup.py verify backups/YOUR_BACKUP
python badge_backup.py restore backups/YOUR_BACKUP --serial YOUR_USB_SERIAL --yes
```

`backup` also accepts `--output NEW_FOLDER`, `--port`, `--manual-boot`,
and `--stay-in-bootloader`. `restore` accepts `--port` and `--manual-boot`.
The non-interactive restore deliberately requires `--yes`; there is no
option to bypass identity, integrity, security, or safety-backup checks.

## Tests / verification

```sh
python -m unittest discover -s tests -v
```

Tests cover backup integrity, restore ordering, source validation, wrong
device rejection, security failures, safety-backup failures, write/verify
failures, locking, and menu cancellation/confirmation. Test firmware is
synthetic, not copied from a real badge. GitHub Actions runs on macOS,
Windows, and Linux.

The original read + on-device digest workflow was exercised on an HTN 2026
badge on macOS during native-firmware development. The menu/restore wrapper
is tested offline with synthetic images and mocked hardware; **this release's
full restore has not been exercised on a physical badge**. The validation
also accepts the author's original backup in a local read-only check.
Don't confuse automated tests with a hardware recovery guarantee.

Independent community utility, not an official Hack the North or Espressif
release. MIT-licensed; dependencies retain their own licenses.
