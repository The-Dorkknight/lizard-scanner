"""lizard_diag.py against the simulated scanner. Every number comes from the simulator."""
import os
import sys
import zipfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import lizard_camera as lc  # noqa: E402
import lizard_diag as ld  # noqa: E402


@pytest.fixture
def lz():
    z = lc.Lizard("sim")
    z.sim_fps = 30.0
    z.sim_scene = lc.SimScene(270)
    z.start("A")
    lc.Tuner(z, "A").apply_preset("Normal")
    yield z
    z.stop_all()


@pytest.fixture(scope="module")
def calibrated():
    s = lc.SimStream(realtime=False)
    s.settings.update(auto_exposure=1, exposure_time_absolute=10, gain=65)
    cal = lc.WallCalibration()
    for d in (180, 230, 280, 330, 380):
        s.scene = lc.SimScene(d)
        cal.add_wall(d, [s.render(True)])
    r = cal.fit()
    return r, lc.DepthSolver(r, cal.reference)


def test_quick_checks_on_the_simulator(lz, calibrated):
    _r, solver = calibrated
    res = ld.run_quick_checks(lz, "mjpg", solver)
    assert set(res) == set(ld.QUICK)
    assert res["usb"]["ok"] is None                     # nothing to check on a pretend scanner
    assert res["video_a"]["ok"] is True and res["video_a"]["details"]["dots_every_picture"]
    assert res["projector"]["ok"] is True
    assert res["serial"]["details"]["serial"] == "SIMULATED"
    assert res["depth_speed"]["details"]["ms"] > 0


def test_room_infrared_is_flagged(lz):
    s = lz.streams["A"]
    orig = s.render

    def sunny(lit):
        return np.clip(orig(lit).astype(int) + 90, 0, 255).astype(np.uint8)
    s.render = sunny
    r = ld.projector_check(lz, seconds=1.0)
    assert r["ok"] is False and "infrared" in r["summary"]


def test_flat_wall_check_reads_the_wall(lz, calibrated):
    r, solver = calibrated
    res = ld.flat_wall_check(lz, solver, r, tape_mm=270)
    d = res["details"]
    assert res["ok"] is True
    assert abs(d["error_mm"]) < 1.5 and d["rms_mm"] < 1.0 and d["tilt_deg"] < 2.0


def test_flat_wall_check_catches_a_wrong_tape_reading(lz, calibrated):
    r, solver = calibrated
    res = ld.flat_wall_check(lz, solver, r, tape_mm=250)
    assert res["ok"] is False and "against your 250 mm" in res["summary"]


def test_flat_wall_check_needs_a_calibration(lz):
    assert ld.flat_wall_check(lz, None, None)["ok"] is None


def test_sweep_puts_the_settings_back(lz):
    before = dict(lz.streams["A"].settings)
    rows = ld.sweep(lz, "gain", ld.gain_test_values(lz.controls("A")), hold_s=0.3,
                    fixed={"exposure_time_absolute": lc.SAFE_EXPOSURE})
    assert len(rows) == 5
    assert rows[-1][1] > rows[0][1]                     # brighter pictures with more gain
    assert lz.streams["A"].settings == before


def test_exposure_test_stays_short_unless_allowed():
    assert max(ld.exposure_test_values(False)) == lc.SAFE_EXPOSURE
    assert max(ld.exposure_test_values(True)) == lc.LONG_EXPOSURE_MAX


def test_camera_b_check_restarts_camera_a(lz):
    r = ld.camera_b_check(lz, seconds=0.8)
    assert r["ok"] is True and "Camera B" in r["summary"]
    assert lz.stream("A") is not None and lz.stream("B") is None


def test_report_zip_hides_the_serial(tmp_path):
    res = {"serial": ld.Result("Serial number", True, "Serial: CRSL1234567", serial="CRSL1234567"),
           "pic": ld.Result("Projector", True, "fine", lit_picture=np.zeros((20, 30), np.uint8))}
    path = ld.save_report(res, str(tmp_path), info={"app": "test"}, hide_serial=True)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        text = z.read("report.txt").decode() + z.read("results.json").decode()
    assert "pictures/pic_lit_picture.png" in names
    assert "CRSL1234567" not in text and "CRSL0000000" in text


def test_plane_fit_ignores_strays():
    rng = np.random.default_rng(0)
    xy = rng.uniform(-50, 50, (4000, 2))
    z = 300 + 0.1 * xy[:, 0] + rng.normal(0, 0.2, 4000)
    z[:200] += rng.uniform(-80, 80, 200)                # 5% strays
    n, _d, rms, _res, strays = ld.fit_plane(np.column_stack([xy, z]))
    assert rms < 0.4 and 0.03 < strays < 0.08


def test_ring_test_steps_camera_b_and_puts_it_back(lz):
    lz.start("B")
    before_a, before_b = dict(lz.streams["A"].settings), dict(lz.streams["B"].settings)
    rows = ld.sweep(lz, "exposure_time_absolute", ld.exposure_test_values(False), hold_s=0.25, cam="B")
    assert len(rows) == 6 and rows[-1][1] > rows[0][1]
    assert lz.streams["B"].settings == before_b and lz.streams["A"].settings == before_a


def test_video_check_reports_the_pattern_and_saves_every_frame(lz):
    lz.streams["A"].pulse_pattern = (True, False, False)
    r = ld.video_check(lz, "A", seconds=1.5)
    assert r["ok"] is True and "1 lit in every 3" in r["summary"]
    rows = r["details"]["frames_table"]
    assert len(rows) > 20 and len(rows[0]) == 6


def test_projector_lit_in_every_picture_is_not_a_problem(lz):
    """Flashing once per exposure: every picture has dots, the eye sees flicker, nothing alternates."""
    lz.streams["A"].pulse_pattern = (True,)
    import time as _t
    _t.sleep(0.8)
    r = ld.video_check(lz, "A", seconds=1.5)
    assert r["ok"] is True and "stripe pattern in every picture" in r["summary"]


def test_no_dots_at_all_is_a_problem(lz):
    lz.streams["A"].pulse_pattern = (False,)
    import time as _t
    _t.sleep(0.8)
    r = ld.video_check(lz, "A", seconds=1.5)
    assert r["ok"] is False and "no stripe pattern" in r["summary"].lower()


def test_dot_score_tells_dots_from_even_light():
    s = lc.SimStream(realtime=False)
    s.settings.update(auto_exposure=1, exposure_time_absolute=10, gain=65)
    s.scene = lc.SimScene(300)
    b = lc.SimStream(cam="B", realtime=False)
    b.settings.update(auto_exposure=1, exposure_time_absolute=10, gain=65)
    b.scene = lc.SimScene(300)
    assert ld.dot_score(s.render(True)) > ld.DOTS_MIN
    assert ld.dot_score(s.render(False)) < ld.DOTS_MIN
    assert ld.dot_score(b.render(True)) < ld.DOTS_MIN


def test_sensor_map_on_the_simulator(lz):
    phases = {k: ld.measure_cameras(lz, cams, seconds=1.0) for k, cams in (("A", ("A",)), ("B", ("B",)),
                                                                          ("AB", ("A", "B")))}
    assert phases["A"]["A"]["dots"] > ld.DOTS_MIN and phases["B"]["B"]["dots"] < ld.DOTS_MIN
    assert set(phases["AB"]) == {"A", "B"}
    r = ld.sensor_map_result(phases, {"A": "Red glow", "B": "White ring", "AB": "Both"})
    f = r["details"]["findings"]
    assert "infrared projector" in f["lights"] and "camera A is the depth camera" in f["dots"]
    assert lz.stream("A") is None and lz.stream("B") is None       # measure_cameras leaves them stopped
