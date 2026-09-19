#!/usr/bin/env python3
"""One-time local environment setup, then open the friendly badge menu."""
import os
from pathlib import Path
import subprocess
import sys
import venv


def main():
    if sys.version_info < (3, 10):
        raise RuntimeError("Install Python 3.10 or newer, then start again.")
    root = Path(__file__).resolve().parent
    environment = root / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    required = dict(line.strip().split("==") for line in
                    (root / "requirements.txt").read_text().splitlines() if line.strip())
    probe = ("import importlib.metadata as m; "
             f"assert all(m.version(k)==v for k,v in {required!r}.items())")
    ready = python.exists() and subprocess.run([str(python), "-c", probe],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not ready:
        print("First-time setup: install the pinned Python tools into this folder's .venv.")
        print("This downloads packages from PyPI; no device will be connected or reset.")
        if input("Install / repair local dependencies? [y/N] ").strip().lower() != "y":
            print("Cancelled.")
            return 0
        if not python.exists():
            print("Creating local Python environment… this may take a minute.", flush=True)
            venv.EnvBuilder(with_pip=True).create(environment)
        print("Installing / checking required tools…", flush=True)
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt")],
                       check=True)
    os.execv(str(python), [str(python), str(root / "badge_backup.py"), *sys.argv[1:]])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        print(f"\nCould not start: {error}\nInstall Python 3.10+ and try again.", file=sys.stderr)
        raise SystemExit(1)
