#!/bin/sh
# Lizard Scanner: double-click launcher for macOS
# https://github.com/The-Dorkknight/lizard-scanner
#
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. See LICENSE.md.
#
# Double-click this file. The first time, macOS will refuse because the file is
# not signed: right-click it, choose Open, then Open again (or System Settings,
# Privacy & Security, Open Anyway). It finds Python 3 and starts
# lizard_scanner.py from this folder. On its first run the app installs numpy,
# Pillow and OpenCV into lz_env/ in this folder. macOS asks once whether the
# Terminal may use the camera: allow it. The Mac camera path is UNTESTED.

cd "$(dirname "$0")" || exit 1
APP="lizard_scanner.py"

finish() {
    if [ "$1" -ne 0 ]; then
        printf '\nPress Enter to close this window. '
        read -r _answer
    fi
    exit "$1"
}

if [ ! -f "$APP" ]; then
    printf '\n%s is not in this folder (%s).\n' "$APP" "$(pwd)" >&2
    printf 'Keep this launcher in the same folder as the rest of the app'"'"'s files.\n' >&2
    finish 1
fi

# python.org and Homebrew Pythons first: Apple's own python3 has an old Tk.
# Apple's /usr/bin/python3 is only tried when the developer tools are installed;
# without them it would just pop up an install dialog.
PY=""
for candidate in \
        /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
        /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 \
        /opt/homebrew/bin/python3 /usr/local/bin/python3 python3 /usr/bin/python3; do
    case "$candidate" in
        /usr/bin/python3|python3)
            resolved="$(command -v "$candidate" 2>/dev/null)"
            if [ "$resolved" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then
                continue
            fi ;;
    esac
    if command -v "$candidate" >/dev/null 2>&1 \
            && "$candidate" -c 'import sys; sys.exit(sys.version_info[0] != 3)' >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done
if [ -z "$PY" ]; then
    printf '\nPython 3 is not installed, and Lizard Scanner needs it to start.\n\n' >&2
    printf '  1. Install Python 3.12 from https://www.python.org/downloads/macos/\n' >&2
    printf '     (the macOS installer; it includes the window toolkit the app needs).\n' >&2
    printf '  2. Double-click this launcher again.\n\n' >&2
    open "https://www.python.org/downloads/macos/" 2>/dev/null
    finish 1
fi

"$PY" "$APP" "$@"
code=$?
if [ "$code" -ne 0 ]; then
    printf '\nLizard Scanner stopped with an error (code %s).\n' "$code" >&2
    printf 'The messages above, and bootstrap.log in this folder, say why.\n' >&2
fi
finish "$code"
