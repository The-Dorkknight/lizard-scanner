#!/bin/sh
# Lizard Scanner: double-click launcher for Linux
# https://github.com/The-Dorkknight/lizard-scanner
#
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty. See LICENSE.md.
#
# Double-click this file and choose "Run" (or "Run in Terminal"). It finds
# Python 3 and starts lizard_scanner.py from this folder. On its first run the
# app installs numpy and Pillow into lz_env/ in this folder and prints its
# progress, which is why a terminal window opens. The scanner itself needs
# v4l-utils (sudo apt install v4l-utils); the app says so if it is missing.
#
# Not a double-click on your system? Right-click the file, Properties,
# Permissions, tick "Allow executing file as program".

cd "$(dirname "$0")" || exit 1
APP="lizard_scanner.py"
TITLE="Lizard Scanner"
SELF="$(pwd)/$(basename "$0")"

problem() {     # say it in the terminal and, where there is one, in a dialog box
    printf '\n%s\n' "$1" >&2
    if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && [ -z "${LZ_LAUNCHER_NO_DIALOG:-}" ]; then
        if command -v zenity >/dev/null 2>&1; then
            zenity --error --no-wrap --title="$TITLE" --text="$1" 2>/dev/null
        elif command -v kdialog >/dev/null 2>&1; then
            kdialog --title "$TITLE" --error "$1" 2>/dev/null
        elif command -v xmessage >/dev/null 2>&1; then
            xmessage -center "$1" 2>/dev/null
        fi
    fi
}

finish() {      # leave a terminal we opened ourselves on screen long enough to be read
    if [ "$1" -ne 0 ] && [ -n "${LZ_LAUNCHER_OWN_TERMINAL:-}" ]; then
        printf '\nPress Enter to close this window. '
        read -r _answer
    fi
    exit "$1"
}

if [ ! -f "$APP" ]; then
    problem "$APP is not in this folder ($(pwd)).

Keep this launcher in the same folder as the rest of the app's files."
    finish 1
fi

# Started from a file manager there is no terminal. Open one and start again
# inside it, so the first-run messages can be seen.
if [ ! -t 0 ] && [ -z "${LZ_LAUNCHER_OWN_TERMINAL:-}" ] && [ -z "${LZ_LAUNCHER_NO_TERMINAL:-}" ] \
        && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    LZ_LAUNCHER_OWN_TERMINAL=1
    export LZ_LAUNCHER_OWN_TERMINAL
    for term in x-terminal-emulator gnome-terminal mate-terminal xfce4-terminal konsole lxterminal xterm; do
        command -v "$term" >/dev/null 2>&1 || continue
        case "$term" in
            gnome-terminal)               exec "$term" --title="$TITLE" -- sh "$SELF" "$@" ;;
            mate-terminal|xfce4-terminal) exec "$term" --title="$TITLE" -x sh "$SELF" "$@" ;;
            *)                            exec "$term" -e sh "$SELF" "$@" ;;
        esac
    done
    unset LZ_LAUNCHER_OWN_TERMINAL      # no terminal program found: carry on without one
fi

# The first Python 3 that has the window toolkit (tkinter); a computer can have several Pythons.
PY=""
PY_NO_TK=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    "$candidate" -c 'import sys; sys.exit(sys.version_info[0] != 3)' >/dev/null 2>&1 || continue
    if "$candidate" -c 'import tkinter' >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
    [ -z "$PY_NO_TK" ] && PY_NO_TK="$candidate"
done
if [ -z "$PY" ] && [ -n "$PY_NO_TK" ]; then
    problem "Python's window toolkit (tkinter) is missing.

Open a terminal and run this, then double-click the launcher again:

    sudo apt install python3-tk python3-venv v4l-utils"
    finish 1
fi
if [ -z "$PY" ]; then
    problem "Python 3 is not installed, and Lizard Scanner needs it to start.

Open a terminal and run this, then double-click the launcher again:

    sudo apt install python3 python3-venv python3-tk v4l-utils

(Fedora: sudo dnf install python3 python3-tkinter v4l-utils      Arch: sudo pacman -S python tk v4l-utils)"
    finish 1
fi

"$PY" "$APP" "$@"
code=$?
if [ "$code" -ne 0 ]; then
    printf '\nLizard Scanner stopped with an error (code %s).\n' "$code" >&2
    printf 'The messages above, and bootstrap.log in this folder, say why.\n' >&2
fi
finish "$code"
