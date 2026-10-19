#!/usr/bin/env python3
# Lizard Scanner: how the wall calibration copes with tape-measure errors, on the simulated scanner.
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Prints a table used in DESIGN_NOTES.md section 4. Simulated data only."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lizard_camera as lc  # noqa: E402


def run(walls, err_mm, seed, tilt=0.0):
    rng = np.random.default_rng(seed)
    s = lc.SimStream(realtime=False, seed=seed)
    s.settings.update(auto_exposure=1, exposure_time_absolute=10, gain=65)
    cal = lc.WallCalibration()
    for d in walls:
        s.scene = lc.SimScene(d + rng.normal(0, err_mm) if err_mm else d, tilt_deg=tilt)
        cal.add_wall(d, [s.render(True) for _ in range(3)])
    r = cal.fit()
    solver = lc.DepthSolver(r, cal.reference)
    solver.track = False                     # every distance is a new view here
    errs = []
    for z_true in (180, 230, 280, 330, 380):
        s.scene = lc.SimScene(z_true)
        z, _ = solver.solve(s.render(True))
        ok = z > 0
        errs.append(float(np.median(z[ok])) - z_true if ok.any() else np.nan)
    return r, errs


def main():
    print("walls (mm)           tape error  k error  c (mm)  depth error at 180/230/280/330/380 mm (median of 8 runs)")
    for walls in ((200, 250, 300, 350), (200, 300), (180, 220, 260, 300, 340, 380)):
        for err in (0.0, 1.0, 2.0):
            ks, cs, es = [], [], []
            for seed in range(8):
                r, e = run(walls, err, seed)
                ks.append(r["k_px_mm"] / (lc.SIM_FOCAL_PX * lc.SIM_BASELINE_MM) - 1)
                cs.append(r["c_mm"])
                es.append(e)
            e = np.nanmedian(np.abs(np.array(es)), axis=0)
            print(f"{str(walls):22s} {err:4.1f} mm   {np.median(np.abs(ks)) * 100:5.1f}%  {np.median(cs):5.1f}   "
                  + "  ".join(f"{x:4.1f}" for x in e))
    s = lc.SimStream(realtime=False)
    s.settings.update(auto_exposure=1, exposure_time_absolute=10, gain=65)
    r, _ = run((200, 250, 300, 350), 0.0, 0)
    cal = lc.WallCalibration()
    for d in (200, 250, 300, 350):
        s.scene = lc.SimScene(d)
        cal.add_wall(d, [s.render(True)])
    solver = lc.DepthSolver(cal.fit(), cal.reference)
    s.scene = lc.SimScene(300, ball=(0, 0, 220, 40))
    img = s.render(True)
    full, tracked = [], []
    for _ in range(12):
        solver.solve(img)
        (full if solver.last_full else tracked).append(solver.last_ms)
    print(f"\nOne depth picture (640x360): full search {np.median(full):.0f} ms, tracking {np.median(tracked):.0f} ms "
          "on this computer")


if __name__ == "__main__":
    main()
