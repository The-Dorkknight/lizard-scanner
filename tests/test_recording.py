"""lizard_recording: a running (simulated) Lizard saved through lizard_framecap. Simulated scanner only."""
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import lizard_camera as lc  # noqa: E402
import lizard_framecap as fc  # noqa: E402
import lizard_recording as lr  # noqa: E402


@pytest.fixture
def lz():
    z = lc.Lizard("sim")
    z.sim_fps = 30.0
    z.start("A")
    yield z
    z.stop_all()


def test_frames_carry_settings_changes_and_notes(lz, tmp_path):
    run = lr.CaptureRun(lz, str(tmp_path), label="ring test", app="test", rung=lambda: 5)
    time.sleep(0.4)
    lc.Tuner(lz, "A").apply_preset("Shiny / white")      # exposure 0.5 ms, gain 30, through Lizard.set
    run.mark("covered camera A")
    time.sleep(0.4)
    folder = run.stop()
    session, rows, events = fc.read_capture(folder)
    assert {r["exposure_ms"] for r in rows} == {"1.0", "0.5"}
    assert {r["gain"] for r in rows} >= {"65", "30"}
    assert any(r["marker"] == "covered camera A" for r in rows)
    first = rows[0]
    assert first["rung"] == "5" and first["mode"] == "mjpg" and float(first["pattern"]) > 0.25
    assert int(rows[-1]["settings_rev"]) > int(rows[0]["settings_rev"])
    assert sum(e["event"] == "setting_changed" for e in events) >= 2   # exposure and gain
    assert session["frames"]["A"] == len(rows)


def test_camera_started_later_joins_the_recording(lz, tmp_path):
    run = lr.CaptureRun(lz, str(tmp_path))
    time.sleep(0.3)
    lz.start("B")
    time.sleep(0.4)
    _s, rows, _e = fc.read_capture(run.stop())
    assert {r["camera"] for r in rows} == {"A", "B"}
    assert all(r["exposure_ms"] == "1.0" for r in rows if r["camera"] == "B")   # B started at the 1 ms cap


def test_snapshot_saves_one_frame_per_camera_and_stops(lz, tmp_path):
    lz.start("B")
    run = lr.CaptureRun(lz, str(tmp_path), snapshot=True)
    end = time.time() + 3
    while not run.closed and time.time() < end:
        time.sleep(0.05)
    assert run.closed
    _s, rows, _e = fc.read_capture(run.folder)
    assert sorted(r["camera"] for r in rows) == ["A", "B"]


def test_hooks_are_removed_when_stopped(lz, tmp_path):
    run = lr.CaptureRun(lz, str(tmp_path))
    run.stop()
    assert run._setting_changed not in lz.on_set and run.attach not in lz.on_start
    assert all(len(s.listeners) == 0 for s in lz.streams.values())


def test_serial_hidden_in_the_capture(tmp_path):
    z = lc.Lizard("sim")
    z.serial = lambda: "CRSL1234567"
    z.start("A")
    try:
        folder = lr.CaptureRun(z, str(tmp_path)).stop()
    finally:
        z.stop_all()
    text = open(os.path.join(folder, "events.jsonl")).read() + open(os.path.join(folder, "session.json")).read()
    assert "1234567" not in text and "CRSL*******" in text


def test_a_capture_plays_back(lz, tmp_path):
    run = lr.CaptureRun(lz, str(tmp_path))
    time.sleep(0.5)
    folder = run.stop()
    play = lc.Lizard(f"replay:{folder}")
    s = play.start("A")
    try:
        f = s.wait_frame(-1, timeout=3)
        assert f is not None and f.grey.shape == (360, 640) and s.mode == "mjpg"
    finally:
        play.stop_all()
