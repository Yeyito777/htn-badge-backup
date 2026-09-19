"""Small interactive terminal UI. All destructive operations live in the backend."""
from pathlib import Path
import json

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

import badge_backup as backend


def choose_device(console):
    devices = [p for p in backend.ports() if (p.vid, p.pid) == backend.USB_ID]
    if not devices:
        console.print("[yellow]No badge found.[/] Connect a USB data cable, then try again.")
        connection_help(console)
        return None
    table = Table(title="Connected badges")
    for title in ("#", "Port", "USB serial"):
        table.add_column(title)
    for i, device in enumerate(devices, 1):
        table.add_row(str(i), Text(device.device), Text(device.serial_number or "(missing)"))
    console.print(table)
    value = Prompt.ask("Choose badge [0 = cancel]", choices=["0"] + [str(i) for i in range(1, len(devices) + 1)],
                       default="1" if len(devices) == 1 else "0", console=console)
    if value == "0":
        return None
    device = devices[int(value) - 1]
    if not device.serial_number:
        console.print("[red]This device has no USB identity; refusing to use it.[/]")
        return None
    return device


def choose_backup(console, root):
    entries = []
    for path in sorted(Path(root).glob("*/manifest.json"), reverse=True):
        try:
            info = json.loads(path.read_text())
            if info.get("schema_version") == 1 and info.get("state") == "verified":
                entries.append((path.parent, info))
        except (OSError, ValueError):
            continue
    table = Table(title="Saved backups (hash checked when selected)")
    for title in ("#", "Folder", "Created", "Badge"):
        table.add_column(title)
    for i, (folder, info) in enumerate(entries, 1):
        table.add_row(str(i), Text(folder.name), Text(str(info.get("created_utc", "?"))[:19]),
                      Text(str(info.get("usb_serial", "?"))))
    if entries:
        console.print(table)
    else:
        console.print("[yellow]No verified backups found in the backups folder.[/]")
    choices = ["0", "p"] + [str(i) for i in range(1, len(entries) + 1)]
    value = Prompt.ask("Choose backup [p = paste folder path, 0 = cancel]",
                       choices=choices, default="0", console=console)
    if value == "0":
        return None
    if value == "p":
        text = Prompt.ask("Backup folder (not the .bin file); blank cancels",
                          default="", console=console).strip().strip("\"'")
        return Path(text).expanduser() if text else None
    return entries[int(value) - 1][0]


def connection_mode(console):
    value = Prompt.ask("Connection [1 = automatic, 2 = already in START/USB recovery mode]",
                       choices=["1", "2"], default="1", console=console)
    return value == "2"


def connection_help(console):
    console.print(Panel(
        "1. Close browser IDEs and other serial monitors.\n"
        "2. Turn battery power OFF and unplug USB.\n"
        "3. Hold START, reconnect USB, then release START.\n"
        "4. Retry and choose recovery mode when asked.\n\n"
        "Use a USB data cable. Normal power-on should NOT hold START.\n"
        "A restore rewrites flash: keep USB connected until verification finishes.",
        title="Connection / recovery help", border_style="yellow", width=76))


def menu(console=None, backup_root=None):
    console = console or Console()
    backup_root = Path(backup_root or backend.BACKUPS)
    while True:
        console.print(Panel(
            "[bold cyan]Hack the North · Badge Backup[/]\n"
            "ESP32-C3 / 4 MiB • your backups stay on this computer\n\n"
            "[bold]1[/]  Back up my badge\n"
            "[bold]2[/]  Restore a saved backup\n"
            "[bold]3[/]  Check a saved backup\n"
            "[bold]4[/]  Connection / recovery help\n"
            "[bold]0[/]  Quit",
            border_style="cyan", width=76))
        try:
            choice = Prompt.ask("Choose", choices=["0", "1", "2", "3", "4"], default="0", console=console)
            if choice == "0":
                console.print("Bye! Keep your backups private.")
                return
            if choice == "4":
                connection_help(console)
                continue
            if choice == "3":
                source = choose_backup(console, backup_root)
                if source is not None:
                    metadata = backend.verify_local(source)
                    console.print("[green]✓ Backup integrity verified.[/]")
                    console.print(Text(f"SHA-256: {metadata['sha256']}"))
                continue
            source = None
            if choice == "2":
                source = choose_backup(console, backup_root)
                if source is None:
                    continue
                metadata = backend.verify_local(source)
            device = choose_device(console)
            if device is None:
                continue
            if choice == "2" and metadata["usb_serial"].lower() != device.serial_number.lower():
                console.print("[red]That backup belongs to a different badge. Nothing was changed.[/]")
                continue
            manual = connection_mode(console)
            if choice == "1":
                console.print("[yellow]This restarts the badge and interrupts its current app/game.[/]")
                consent = Prompt.ask("Start backup?", choices=["y", "n"], default="n", console=console)
                if consent != "y":
                    console.print("Cancelled. Nothing changed.")
                    continue
                folder = backup_root / backend.timestamp()
                with console.status("Backing up and checking flash… please keep USB connected"):
                    backend.backup(folder, serial=device.serial_number, manual_boot=manual)
                console.print("[bold green]✓ Backup saved and verified. Badge reboot requested.[/]")
                console.print(Text(str(folder)))
            else:
                console.print(Panel(
                    "RESTORE REPLACES ALL FIRMWARE AND SETTINGS\n"
                    "Contacts, app data, and progress revert to the selected backup.\n"
                    "A fresh verified safety backup is saved first.\n"
                    "Only this backup's original badge is allowed.\n"
                    "Keep USB connected. Do not interrupt the write.",
                    title="Confirm restore", border_style="red", width=76))
                console.print(Text(f"Backup: {source}\nBadge: {device.serial_number}"))
                consent = Prompt.ask("Type RESTORE to continue; anything else cancels",
                                     default="", console=console)
                if consent != "RESTORE":
                    console.print("Cancelled. Nothing changed.")
                    continue
                with console.status("Safety backup → restore → verify → reboot… keep USB connected"):
                    report = backend.restore(source, serial=device.serial_number, confirmed=True,
                                             manual_boot=manual, backup_root=backup_root)
                console.print("[bold green]✓ Restore verified. Badge reboot requested.[/]")
                console.print(Text(f"Safety backup: {report['safety_backup']}"))
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Interrupted. If a write had started, use recovery help before retrying.[/]")
            return
        except Exception as error:
            console.print(Text(f"Could not finish: {error}", style="bold red"))
            console.print("Details are in the operation log. No automatic recovery is attempted.")
            console.print("If writing had started, the app may not boot; use START/USB recovery and retry.")
