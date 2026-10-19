"""The window itself, with the simulated scanner: starts, shows pictures, the light buttons work,
and the wall test calibrates through its own window. Needs a display (CI uses xvfb-run)."""
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
tk = pytest.importorskip("tkinter")
if not os.environ.get("DISPLAY"):
    pytest.skip("no display", allow_module_level=True)

import lizard_camera as lc  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    import lizard_scanner as ls
    import lizard_panel as lp  # noqa: F401
    monkeypatch.setattr(lc, "CALIBRATION_DIR", str(tmp_path / "cal"))
    monkeypatch.setattr(ls, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    monkeypatch.setattr(ls, "SCANS_DIR", str(tmp_path / "scans"))
    root = tk.Tk()
    root.geometry("1100x720")
    a = ls.LizardApp(root, simulate=True)
    yield a
    if not a.closing:
        a.on_close()


def _until(app, cond, sec):
    end = time.time() + sec
    while time.time() < end and not cond():
        app.root.update()
        time.sleep(0.02)
    return cond()


def test_simulated_scanner_starts_and_shows_pictures(app):
    assert _until(app, lambda: app.lizard is not None, 15)
    assert _until(app, lambda: app._photo is not None, 10)
    assert "Simulated" in app.badge.cget("text")
    assert str(app.view_btns["depth"].cget("state")) == "disabled"


def test_light_buttons_change_the_camera(app):
    import lizard_panel as lp  # noqa: F401
    assert _until(app, lambda: app.lizard is not None, 15)
    app.light.preset("Dark / black")
    assert _until(app, lambda: not app.light.busy, 10)
    s = app.lizard.streams["A"]
    assert s.settings["exposure_time_absolute"] == 10 and s.settings["gain"] == 112   # flash stays at 1 ms
    app.light.dimmer()
    assert _until(app, lambda: not app.light.busy, 10)
    assert app.tuner.step == 7
    for _ in range(8):                               # Brighter can never pass 1 ms without the switch
        app.light.brighter()
        assert _until(app, lambda: not app.light.busy, 10)
    assert s.settings["exposure_time_absolute"] <= lc.SAFE_EXPOSURE
    assert not app.light.long_var.get()
    assert str(app.light.default_btn.cget("state")) == "disabled"


def test_wall_test_turns_on_depth(app):
    import lizard_panel as lp
    assert _until(app, lambda: app.lizard is not None, 15)
    w = lp.WallTestWindow(app)
    w.tuned = True                                  # skip the automatic tune to keep the test quick
    for d in (200, 260, 330):
        w.dist_var.set(str(d))
        w.record()
        assert _until(app, lambda: not w.busy, 30)
    assert len(w.cal.walls) == 3
    # fitted and saved by itself after the third wall, everything in one folder
    assert app.solver is not None and app.view_var.get() == "depth"
    folder = lc.calibration_folder(app.serial)
    assert {"calibration.json", "reference.npz", "walls"} <= set(os.listdir(folder))
    w.fit()                                          # fitting again by hand still works
    assert _until(app, lambda: not w.busy, 30) and w.saved_fit
    assert _until(app, lambda: app.depth_result is not None, 20)
    z = app.depth_result[0]
    assert (z > 0).mean() > 0.5


def test_diagnostics_window_runs_the_quick_checks(app, tmp_path):
    import lizard_panel as lp
    app.reports_dir = str(tmp_path / "reports")
    assert _until(app, lambda: app.lizard is not None, 15)
    d = lp.DiagnosticsWindow(app)
    d.run_quick()
    assert _until(app, lambda: not d.busy, 60)
    assert d.results["video_a"]["ok"] is True
    assert d.results["usb"]["ok"] is None            # simulated: nothing to check
    d.save()
    zips = [f for f in os.listdir(app.reports_dir) if f.endswith(".zip")]
    assert len(zips) == 1
    d.destroy()


def test_sensor_map_window_runs_all_three_steps(app, tmp_path):
    import lizard_panel as lp
    app.reports_dir = str(tmp_path / "reports")
    assert _until(app, lambda: app.lizard is not None, 15)
    lp.SensorMapWindow.SECONDS = 0.8
    w = lp.SensorMapWindow(app)
    w.next_phase()
    for _ in range(3):
        assert _until(app, lambda: w.ask.winfo_ismapped(), 20)
        w.answer("Couldn't tell")
    assert "sensor_map" in app.eye_results
    assert app.lizard.stream("A") is not None                       # camera A is back
    d = lp.DiagnosticsWindow(app)                                   # and the result shows there
    assert d.results["sensor_map"]["name"] == "Sensor map"
    d.destroy()


def test_record_with_a_note_and_save_frame(app, tmp_path):
    import lizard_scanner as ls
    import lizard_framecap as fc
    assert _until(app, lambda: app.lizard is not None, 15)
    app.capture_label_var.set("ring test")
    app.toggle_record()
    _until(app, lambda: False, 0.5)
    app.note_var.set("covered camera A")
    app.add_note()
    _until(app, lambda: False, 0.5)
    app.toggle_record()
    _s, rows, _e = fc.read_capture(app.last_capture)
    assert rows and any(r["marker"] == "covered camera A" for r in rows)
    assert "ring-test" in os.path.basename(app.last_capture)
    app.save_frame_now()
    assert _until(app, lambda: "Saved a picture" in app.status_var.get(), 10)
    assert len(os.listdir(ls.SCANS_DIR)) == 2
