"""lizard_camera.py against the simulated scanner: calibration, depth, tuning, recordings and the
camera contract Scantastic uses. Every number here comes from the simulator, not a real Lizard."""
import os
import sys
import time

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import lizard_camera as lc  # noqa: E402

WALLS = (200, 250, 300, 350)


def _sim(**settings):
    s = lc.SimStream(realtime=False)
    s.settings.update(auto_exposure=1, exposure_time_absolute=10, gain=65, **settings)
    return s


def _calibrated(walls=WALLS, transpose=False, error_mm=0.0):
    s = _sim()
    cal = lc.WallCalibration()
    rng = np.random.default_rng(1)
    for d in walls:
        s.scene = lc.SimScene(d + (rng.normal(0, error_mm) if error_mm else 0.0))
        imgs = [s.render(True) for _ in range(3)]
        if transpose:
            imgs = [np.ascontiguousarray(i.T) for i in imgs]
        cal.add_wall(d, imgs)
    return s, cal, cal.fit()


def _truth(scene, shape):
    h, w = shape
    return scene.depth(w, h, lc.SIM_FOCAL_PX) - lc.SIM_GLASS_MM


def test_pulse_tracker_finds_the_lit_frames():
    p = lc.PulseTracker()
    for i in range(20):
        p.add(80.0 if i % 2 == 0 else 20.0)
    pulsing, share, ratio = p.state()
    assert pulsing and share > 0.9 and ratio > 3
    assert p.classify(80.0) is True and p.classify(20.0) is False


def test_steady_light_is_not_pulsing():
    p = lc.PulseTracker()
    for i in range(20):
        p.add(50.0 + (i % 3) * 0.1)
    assert p.state()[0] is False
    assert p.classify(50.0) is True          # everything counts as lit when nothing pulses


def test_wall_fit_recovers_the_simulated_geometry():
    _s, _cal, r = _calibrated()
    assert r["axis"] == "x"
    k_true = lc.SIM_FOCAL_PX * lc.SIM_BASELINE_MM
    assert abs(r["k_px_mm"] - k_true) / k_true < 0.05
    assert abs(r["c_mm"] - lc.SIM_GLASS_MM) < 5.0
    assert all(abs(w["error_mm"]) < 1.0 for w in r["walls"])


def test_sideways_pictures_are_refused_clearly():
    # the real Lizard's stripes are vertical and slide along x; turned pictures have nothing to follow
    with pytest.raises(ValueError, match="stripe pattern"):
        _calibrated(transpose=True)


def test_two_walls_fit_without_the_centre_and_say_so():
    _s, cal, r = _calibrated(walls=(220, 320))
    assert r["c_mm"] == 0.0
    assert any("third" in line for line in cal.summary_lines())


def test_one_wall_is_refused():
    cal = lc.WallCalibration()
    cal.add_wall(250, [_sim().render(True)])
    with pytest.raises(ValueError):
        cal.fit()


def test_same_distance_twice_replaces_the_wall():
    s = _sim()
    cal = lc.WallCalibration()
    cal.add_wall(250, [s.render(True)])
    cal.add_wall(250, [s.render(True)])
    assert len(cal.walls) == 1


@pytest.mark.parametrize("scene", [lc.SimScene(270), lc.SimScene(330, ball=(0, 0, 220, 40))])
def test_depth_from_one_picture(scene):
    s, cal, r = _calibrated()
    solver = lc.DepthSolver(r, cal.reference)
    s.scene = scene
    z, score = solver.solve(s.render(True))
    truth = _truth(scene, z.shape)
    ok = z > 0
    assert ok.mean() > 0.75, "too little of the view has depth"
    err = np.abs(z - truth)[ok]
    assert np.median(err) < 1.0
    assert np.percentile(err, 90) < 4.0


def test_depth_is_quicker_while_tracking():
    s, cal, r = _calibrated()
    solver = lc.DepthSolver(r, cal.reference)
    s.scene = lc.SimScene(280)
    solver.solve(s.render(True))
    assert solver.last_full
    z, _ = solver.solve(s.render(True))
    assert not solver.last_full                      # searched only near the shifts seen last time
    ok = z > 0
    assert ok.mean() > 0.75 and np.median(np.abs(z[ok] - 280.0)) < 1.0


def test_old_dot_calibration_is_refitted_from_its_walls(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "CALIBRATION_DIR", str(tmp_path))
    _s, cal, _r = _calibrated()
    folder = cal.save("CRSL0000000")
    import json
    with open(os.path.join(folder, "calibration.json")) as f:
        old = json.load(f)
    old["version"] = 1
    with open(os.path.join(folder, "calibration.json"), "w") as f:
        json.dump(old, f)
    assert lc.load_calibration("CRSL0000000") is None
    assert lc.calibration_needs_refit("CRSL0000000")
    again = lc.WallCalibration.from_folder(folder)
    assert [w[0] for w in again.walls] == list(map(float, WALLS))
    assert again.fit()["version"] == lc.CALIB_VERSION


def test_calibration_lives_in_one_folder_and_zips(tmp_path, monkeypatch):
    import zipfile
    monkeypatch.setattr(lc, "CALIBRATION_DIR", str(tmp_path))
    s = _sim()
    cal = lc.WallCalibration()
    for d in WALLS:
        s.scene = lc.SimScene(d)
        cal.add_wall(d, [s.render(True)], jpegs=[b"\xff\xd8fake jpeg\xff\xd9"])
        cal.save_walls(lc.calibration_folder("CRSL1234567"))          # after every wall, like the window
    cal.fit()
    folder = cal.save("CRSL1234567")
    names = sorted(os.listdir(folder))
    assert names == ["calibration.json", "calibration_report.txt", "reference.npz", "shift_maps.npz", "walls"]
    assert len(os.listdir(os.path.join(folder, "walls"))) == len(WALLS) + 1     # pictures + wall_patterns.npz
    path = lc.zip_calibration(folder, hide_serial=True)
    assert "1234567" not in os.path.basename(path)
    with zipfile.ZipFile(path) as zf:
        assert all(n.startswith("CRSL*******/") for n in zf.namelist())
        assert "1234567" not in zf.read("CRSL*******/calibration.json").decode()
        assert "CRSL*******/walls/wall_200mm_1.jpg" in zf.namelist()
    # the walls come back from the folder, pictures included
    back = lc.WallCalibration.from_folder(folder)
    assert len(back.walls) == len(WALLS) and len(back.jpegs[200.0]) == 1


def test_real_pattern_matching_finds_the_right_stripe():
    # stripes repeat every 6.3 px: only the beads' wobble tells them apart. A wrong-stripe match would be
    # off by a multiple of the spacing.
    s = _sim()
    s.scene = lc.SimScene(300)
    a = lc.code_image(s.render(True))
    s.scene = lc.SimScene(250)
    b = lc.code_image(s.render(True))
    shifts = np.arange(-80, 81)
    rows = np.arange(60, 300, 30)
    sh, top, margin, ok = lc.best_match(lc.match_rows(a, b, shifts, rows), shifts)
    true = lc.SIM_FOCAL_PX * lc.SIM_BASELINE_MM * (1 / (300 + lc.SIM_GLASS_MM) - 1 / (250 + lc.SIM_GLASS_MM))
    assert ok.mean() > 0.8
    assert np.median(np.abs(sh[ok] - true)) < 0.3


def test_a_tilted_wall_is_reported():
    s = _sim()
    cal = lc.WallCalibration()
    for d in WALLS:
        s.scene = lc.SimScene(d, tilt_deg=20 if d == 200 else 0)
        cal.add_wall(d, [s.render(True)])
    cal.fit()
    assert any("left out" in line or "angle" in line for line in cal.summary_lines())


def test_calibration_saves_and_loads(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "CALIBRATION_DIR", str(tmp_path))
    _s, cal, r = _calibrated()
    folder = cal.save("CRSL0000000")
    assert os.path.exists(os.path.join(folder, "calibration_report.txt"))
    res, ref = lc.load_calibration("CRSL0000000")
    assert res["k_px_mm"] == pytest.approx(r["k_px_mm"])
    assert ref.shape == cal.reference.shape
    assert lc.load_calibration("someone-else") is None


def test_measured_picture_width_sets_the_focal_length():
    _s, cal, r = _calibrated()
    r2 = cal.fit(picture_width_mm=150.0, at_mm=250.0)
    assert r2["focal_source"].startswith("measured")
    assert r2["focal_px"] == pytest.approx(640 * (250.0 + r2["c_mm"]) / 150.0)
    assert r["focal_source"].startswith("GUESSED")


def test_points_and_ply(tmp_path):
    z = np.zeros((40, 60), np.float32)
    z[10:30, 20:40] = 300.0
    pts = lc.depth_to_points(z, 500.0, centre_mm=12.0)
    assert len(pts) == 400 and np.allclose(pts[:, 2], 300.0)
    path = tmp_path / "p.ply"
    lc.write_ply(str(path), pts, np.full(len(pts), 128, np.uint8))
    head = path.read_bytes()[:300]
    assert b"element vertex 400" in head and b"property uchar red" in head


def test_v4l2_controls_are_read_from_v4l2_ctl_output():
    out = """
User Controls

                     brightness 0x00980900 (int)    : min=-64 max=64 step=1 default=0 value=0
                           gain 0x00980913 (int)    : min=0 max=100 step=1 default=32 value=65

Camera Controls

                  auto_exposure 0x009a0901 (menu)   : min=0 max=3 default=3 value=1 (Manual Mode)
\t\t\t\t1: Manual Mode
\t\t\t\t3: Aperture Priority Mode
         exposure_time_absolute 0x009a0902 (int)    : min=1 max=5000 step=1 default=156 value=10
"""

    class R:
        stdout = out

    ctrls = lc.v4l2_controls("/dev/video0", run=lambda *a, **k: R())
    names = {c["name"]: c for c in ctrls}
    assert names["gain"]["max"] == 100 and names["gain"]["value"] == 65
    assert names["auto_exposure"]["menu"] == {1: "Manual Mode", 3: "Aperture Priority Mode"}
    assert names["exposure_time_absolute"]["default"] == 156


def test_ladder_respects_the_cameras_limits():
    lz = lc.Lizard("sim")
    lz.start("A")
    try:
        lz.streams["A"].ranges["gain"] = (0, 100, 32)
        t = lc.Tuner(lz, "A")
        assert max(g for _e, g in t.ladder()) == 100
        assert len(set(t.ladder())) == len(t.ladder())
    finally:
        lz.stop_all()


def test_presets_and_steps():
    lz = lc.Lizard("sim")
    lz.start("A")
    try:
        t = lc.Tuner(lz, "A")
        assert t.apply_preset("Normal") == ""
        assert lz.streams["A"].settings["exposure_time_absolute"] == 10
        assert lz.streams["A"].settings["gain"] == 65
        assert lz.streams["A"].settings["auto_exposure"] == 1
        t.brighter()
        assert t.step == 6 and lz.streams["A"].settings["exposure_time_absolute"] == 10
        for _ in range(20):
            t.dimmer()
        assert t.step == 0
        t.camera_defaults()
        assert t.step is None and lz.streams["A"].settings["auto_exposure"] == 3
    finally:
        lz.stop_all()


def test_auto_tune_settles_from_either_end():
    lz = lc.Lizard("sim")
    lz.sim_fps = 60.0
    lz.start("A")
    try:
        t = lc.Tuner(lz, "A")
        found = set()
        for start in (0, 10):
            t.apply_step(start)
            ok, report = t.auto_tune()
            assert ok, report
            found.add(t.step)
        # From below it stops at the first acceptable rung, from above at the first one coming down:
        # when two neighbouring rungs are both acceptable the answers may differ by one.
        assert max(found) - min(found) <= 1, f"answers too far apart from different starts: {found}"
        s = lz.streams["A"]
        f = s.wait_frame(s.latest.index, lit=True)
        top, sat = lc.Tuner.measure(f.grey)
        assert sat <= lc.TUNE_MAX_SATURATED * 2
    finally:
        lz.stop_all()


def test_recording_plays_back(tmp_path):
    lz = lc.Lizard("sim")
    lz.sim_fps = 40.0
    s = lz.start("A")
    rec = lc.SessionRecorder(str(tmp_path / "rec"), dict(mode="mjpg", serial="SIMULATED"))
    s.listeners.append(rec.add)
    time.sleep(0.6)
    s.listeners.remove(rec.add)
    rec.close()
    lz.stop_all()
    assert rec.count >= 8
    rows = lc.read_session_rows(str(tmp_path / "rec"))
    assert {r["lit"] for r in rows} <= {"0", "1"}
    play = lc.Lizard(f"replay:{tmp_path / 'rec'}")
    p = play.start("A")
    try:
        f = p.wait_frame(-1, timeout=3)
        assert f is not None and f.grey.shape == (360, 640)
        assert play.serial() == "SIMULATED"
    finally:
        play.stop_all()


def test_camera_contract_for_scantastic(tmp_path, monkeypatch):
    """What Scantastic's camera loop relies on: (RGB uint8, uint16 depth lined up, intrinsic)."""
    monkeypatch.setattr(lc, "CALIBRATION_DIR", str(tmp_path))
    _s, cal, _r = _calibrated()
    cal.save("SIMULATED")
    cam = lc.LizardCamera(backend="sim", require_depth=True)
    cam.start(log=lambda m: None)
    try:
        color, depth, intr = cam.get_frame()
        assert color.dtype == np.uint8 and color.ndim == 3 and color.shape[2] == 3
        assert depth.dtype == np.uint16 and depth.shape == color.shape[:2]
        mm = depth[depth > 0].astype(np.float64) * cam.depth_scale * 1000.0
        assert 150 < np.median(mm) < 400
        assert cam.family == "Lizard" and cam.serial == "SIMULATED" and cam.has_depth
        assert intr is not None
        lo, hi = cam.guide_range
        assert 0 < lo < hi < 1
    finally:
        cam.stop()


def test_scantastic_camera_refuses_to_start_uncalibrated(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "CALIBRATION_DIR", str(tmp_path))
    cam = lc.make_camera()
    cam.backend = "sim"
    with pytest.raises(lc.CameraError, match="WALL TEST"):
        cam.start(log=lambda m: None)
    cam.stop()


def test_pictures_only_without_calibration(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "CALIBRATION_DIR", str(tmp_path))
    cam = lc.LizardCamera(backend="sim")
    cam.start(log=lambda m: None)
    try:
        color, depth, intr = cam.get_frame()
        assert color is not None and depth is None and intr is None
    finally:
        cam.stop()


def test_flashes_never_pass_1ms_unless_allowed():
    lz = lc.Lizard("sim")
    lz.start("A")
    try:
        t = lc.Tuner(lz, "A")
        assert max(e for e, _g in t.ladder()) == lc.SAFE_EXPOSURE
        for _ in range(30):
            t.brighter()
        assert lz.streams["A"].settings["exposure_time_absolute"] <= lc.SAFE_EXPOSURE
        assert t.flash_ms() == 1.0
        t.allow_long = True
        assert max(e for e, _g in t.ladder()) == lc.LONG_EXPOSURE_MAX
        for _ in range(30):
            t.brighter()
        assert lz.streams["A"].settings["exposure_time_absolute"] == lc.LONG_EXPOSURE_MAX
        assert "LONG FLASHES" in t.describe()
    finally:
        lz.stop_all()


def test_auto_tune_on_a_dark_scene_keeps_the_flash_short():
    lz = lc.Lizard("sim")
    lz.sim_fps = 60.0
    lz.sim_scene = lc.SimScene(400)                  # far wall: dim pictures
    lz.start("A")
    try:
        t = lc.Tuner(lz, "A")
        t.apply_step(0)
        ok, _report = t.auto_tune()
        assert ok
        assert lz.streams["A"].settings["exposure_time_absolute"] <= lc.SAFE_EXPOSURE
    finally:
        lz.stop_all()


def _watch(pattern=(True, False), ambient=0.15, seconds=1.2):
    lz = lc.Lizard("sim")
    lz.sim_fps = 40.0
    lz.sim_scene = lc.SimScene(300)
    s = lz.start("A")
    s.pulse_pattern, s.ambient = pattern, ambient
    time.sleep(seconds)
    frames = []
    s.listeners.append(frames.append)
    time.sleep(0.6)
    s.listeners.remove(frames.append)
    info = s.pulse.info()
    lz.stop_all()
    truth = [bool(pattern[f.index % len(pattern)]) for f in frames]
    return info, sum(f.lit == tr for f, tr in zip(frames, truth)) / max(len(frames), 1)


@pytest.mark.parametrize("pattern,period", [((True, False), 2), ((True, False, False), 3),
                                            ((True, True, False, False), 4)])
def test_pulse_patterns_other_than_every_other_picture(pattern, period):
    info, accuracy = _watch(pattern)
    assert info["pulsing"] and info["period"] == period
    assert accuracy > 0.95


def test_pulse_found_in_a_bright_room_from_the_dots():
    """Lots of room infrared: overall brightness barely changes, the dots still do."""
    info, accuracy = _watch(ambient=3.0)
    assert info["pulsing"] and info["signal"] in ("detail", "size")
    assert accuracy > 0.95


def test_steady_projector_is_not_called_pulsing():
    info, _ = _watch(pattern=(True,))
    assert not info["pulsing"]


def test_every_camera_starts_at_a_1ms_flash():
    lz = lc.Lizard("sim")
    try:
        for cam in ("A", "B"):
            st = lz.start(cam).settings
            assert st["auto_exposure"] == 1 and st["exposure_time_absolute"] == lc.SAFE_EXPOSURE
    finally:
        lz.stop_all()


def test_camera_b_follows_the_light_buttons():
    lz = lc.Lizard("sim")
    try:
        lz.start("A")
        lz.start("B")
        t = lc.Tuner(lz, "A")
        t.apply_preset("Shiny / white")
        b = lz.streams["B"].settings
        assert (b["exposure_time_absolute"], b["gain"]) == t.ladder()[2]
        for _ in range(30):
            t.brighter()
        assert lz.streams["B"].settings["exposure_time_absolute"] <= lc.SAFE_EXPOSURE
    finally:
        lz.stop_all()


def test_real_lizard_walls_match_on_the_right_stripe():
    """Real data: two of the owner's walls (230 and 280 mm). The stripes slide about 56 px at the left of
    the picture and 51 at the right (measured in the 18 October analysis); plain square windows could not
    tell the stripes apart there, the stripe code in tall windows can."""
    z = np.load(os.path.join(ROOT, "tests", "data", "real_walls_230_280.npz"))
    a = lc.stripe_code((z["w230"].astype(np.float32) - 128) / 30)
    b = lc.stripe_code((z["w280"].astype(np.float32) - 128) / 30)
    shifts = np.arange(-120, 121)
    rows = np.array([50, 70, 90])
    sh, top, margin, ok = lc.best_match(lc.match_rows(a, b, shifts, rows, ry=40), shifts, 0.25, 0.10)
    left, right = sh[:, 60:160][ok[:, 60:160]], sh[:, 480:580][ok[:, 480:580]]
    assert ok.mean() > 0.6
    assert abs(np.median(left) - 56) < 1.5 and abs(np.median(right) - 51.5) < 2.0
    assert np.median(margin[ok]) > 0.15                           # a clear winner, not a stripe lookalike


def test_wrong_stripe_streaks_are_dropped():
    # a smooth surface with a thin vertical streak one stripe spacing (6 px) off: the streak goes, the rest stays
    shift = np.tile(np.linspace(30, 40, 200, dtype=np.float32), (12, 1))
    shift[:, 100:103] += 6.3
    ok = np.ones_like(shift, bool)
    keep = lc._agrees_with_neighbours(shift, ok)
    assert not keep[:, 100:103].any()
    assert keep[:, 20:90].all() and keep[:, 110:180].all()
