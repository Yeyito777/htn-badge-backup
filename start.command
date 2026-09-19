#!/bin/sh
# macOS: double-click in Finder. Linux: ./start.command
cd "$(dirname "$0")" || exit 1
python3 start.py "$@"
result=$?
if [ "$result" -ne 0 ]; then
    printf '\nPress Enter to close...'
    read -r ignored
fi
exit "$result"
