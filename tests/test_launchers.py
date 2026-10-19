"""The double-click launchers: present, executable, right line endings, and the Linux one run for real.
The Windows and Mac launchers are only checked as text (no Windows or macOS machine in the test run)."""
import os
import stat
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINUX = os.path.join(ROOT, "Lizard-Scanner-Linux.sh")
MAC = os.path.join(ROOT, "Lizard-Scanner-Mac.command")
WINDOWS = os.path.join(ROOT, "Lizard-Scanner-Windows.bat")


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def test_launchers_exist_and_name_the_app():
    for path in (LINUX, MAC, WINDOWS):
        text = _read(path).decode("ascii")
        assert "lizard_scanner.py" in text
        assert "Required Notice: Copyright Matthew Armstrong" in text


def test_unix_launchers_are_executable_with_lf_endings():
    for path in (LINUX, MAC):
        assert os.stat(path).st_mode & stat.S_IXUSR, path
        data = _read(path)
        assert data.startswith(b"#!/bin/sh\n") and b"\r\n" not in data


def test_windows_launcher_has_crlf_endings():
    data = _read(WINDOWS)
    assert data.count(b"\r\n") == data.count(b"\n")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux launcher")
def test_linux_launcher_starts_the_app():
    env = dict(os.environ, LZ_LAUNCHER_NO_TERMINAL="1", LZ_LAUNCHER_NO_DIALOG="1")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        pytest.skip("this Python has no tkinter, so the launcher would rightly refuse")
    r = subprocess.run(["sh", LINUX, "--version"], capture_output=True, text=True, env=env, timeout=60,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert "Lizard Scanner v" in r.stdout and "WORK IN PROGRESS" in r.stdout
