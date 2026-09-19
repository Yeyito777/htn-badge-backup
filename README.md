# Hack the North badge backup

A small **read-only flash-backup tool** for the **Hack the North 2026
ESP32-C3 / 4 MiB badge**. Save your own badge's firmware and storage before
experimenting with native firmware.

- Reads **all 4 MiB**, not just the application.
- Verifies the saved dump against the device's flash digest while its app
  remains stopped.
- Records a local SHA-256 and manifest, including your badge's USB identity.
- Refuses ambiguous device selection, missing identity, wrong chip/flash
  size, existing output folders, and incomplete files.
- Never calls write-flash, erase-flash, or an eFuse-writing command.
- Reboots the existing application after a successful backup by default.

**Backing up still resets the badge and interrupts the current game/app.**
Close serial monitors, browser IDE connections, and other esptool processes.
Do not run two backup processes against the same badge at once.

This repo contains **no badge firmware, ROMs, contacts, or recovery images**.
Back up your own device. A dump can contain credentials, Wi-Fi details,
contacts, and personal data: **never post it on GitHub**.

## Setup

Python **3.10+**, a USB data cable, and access to the serial port are required.
ESP-IDF and a C compiler are **not** required.

```sh
git clone https://github.com/Yeyito777/htn-badge-backup.git
cd htn-badge-backup
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python badge_backup.py list
```

Windows PowerShell activation: `.\.venv\Scripts\Activate.ps1`, or call
`.\.venv\Scripts\python.exe` directly. On Linux, configure your distribution's
serial-port permissions; do not run the whole tool as root just to bypass them.

## Back up

Switch battery power **off** while using USB and connect the badge.
Copy **your own** serial value from `list`:

```sh
python badge_backup.py backup --serial YOUR_USB_SERIAL
```

With exactly one matching device connected, automatic selection also works:

```sh
python badge_backup.py backup
```

Optional explicit port and a **new** output directory:

```sh
python badge_backup.py backup --port /dev/cu.usbmodemXXXX --output backups/original
# Windows example: --port COM5
# Linux example:   --port /dev/ttyACM0
```

The tool checks the native Espressif USB ID `303a:1001`, then pins the selected
USB serial for every operation. That ID is shared by other Espressif boards:
it does **not** prove the board is an HTN badge. You are responsible for
connecting the intended board. The chip must identify as ESP32-C3 and report
4 MiB flash. Different badge years, bridge chips, and other variants are
not supported.

Successful output contains:

```text
backups/<UTC timestamp>/
  flash.bin       # exactly 4,194,304 bytes — PRIVATE
  manifest.json   # hash, verification state, device identity — PRIVATE
  SHA256SUMS
  operations.log  # esptool diagnostics — PRIVATE
```

Copy the **entire folder** to another private, reliable storage location.
Keep the original untouched before flashing anything new.

```sh
python badge_backup.py verify backups/<UTC-timestamp>
```

`verify` checks the saved file against its recorded size/hash locally. It
does not access hardware or authenticate a manifest an attacker has modified.
The device comparison happens during `backup`, via esptool `verify-flash`.
Do not treat an incomplete/failed dump as a usable recovery image.

## If automatic connection fails

1. Close anything else using the badge's serial port.
2. Turn battery power off and unplug USB.
3. Hold **START**, reconnect USB, then release START.
4. Run `list` again, then:

```sh
python badge_backup.py backup --serial YOUR_USB_SERIAL --manual-boot
```

START is GPIO9, the ESP32-C3 ROM-download strap on this badge. Avoid holding
it during ordinary power-on if you want the application to start normally.
`--stay-in-bootloader` skips the final reboot. On an error, the board may
remain in download mode; a normal power cycle will restart its existing app.

## Recovery limits

This is a **backup tool, not a generic restore/flashing tool**. It captures
the current state, not a magically pristine factory image. If you already
replaced the application, your backup will contain that replacement.

Flash backups do not include eFuses or guarantee recovery after security
configuration changes, hardware damage, or disabled download access.
Do not program eFuses, enable secure boot/flash encryption, or change the
partition table casually. Security-enabled devices may reject these reads;
this script does not bypass those protections.

Never flash one person's full dump onto another badge. If you need recovery,
retain your original backup and use board-specific instructions for your
own device and its security settings.

## Verification / development

```sh
python -m unittest discover -s tests -v
```

The full-flash read + on-device digest verification workflow was exercised
on one HTN 2026 ESP32-C3 badge on macOS during native-firmware development.
This standalone packaging is covered by mocked tests; it has not been
separately exercised on every OS or badge revision. No fresh device reset
was performed just to publish it.

The script uses esptool's RAM flasher stub for read/digest operations; it does
not install that stub into flash. The original running app is stopped before
reading, so it cannot change filesystem/NVS contents during verification.
On POSIX, files are created privately; Windows users should also check
directory permissions. Git ignores dumps/manifests/logs, but **ignores are not
a security boundary**—do not force-add private backups.

Independent community utility, not an official Hack the North or Espressif
release. MIT-licensed script; esptool and pyserial retain their own licenses.
