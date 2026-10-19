#!/usr/bin/env python3
# Lizard Scanner: redraws the README screenshots from the simulated scanner.
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Run under a virtual display from a copy of the app at a realistic path, e.g.
    xvfb-run -a -s "-screen 0 1180x780x24" python3 tools/make_screenshots.py docs/images
Needs ImageMagick's `import`. Every picture is of the SIMULATED scanner."""
import os
import shutil
import subprocess
import sys
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(APP, "docs", "images"))
sys.path.insert(0, APP)
import tkinter as tk  # noqa: E402

import lizard_camera as lc  # noqa: E402
import lizard_panel as lp  # noqa: E402
import lizard_scanner as ls  # noqa: E402

W, H = 1180, 780


def grab(root, name):
    root.update()
    time.sleep(0.4)
    root.update()
    png = os.path.join(OUT, name + ".png")
    subprocess.run(["import", "-window", "root", "-crop", f"{W}x{H}+0+0", png], check=True)
    from PIL import Image
    Image.open(png).convert("RGB").save(os.path.join(OUT, name + ".webp"), lossless=True, method=6)
    os.remove(png)
    print("saved", name)


def pump(root, sec, cond=None):
    end = time.time() + sec
    while time.time() < end:
        if cond is not None and cond():
            return True
        root.update()
        time.sleep(0.02)
    return cond() if cond else True


def fresh(settings):
    for f in ("settings.json",):
        try:
            os.remove(os.path.join(APP, f))
        except OSError:
            pass
    ls.save_settings(settings)


def main():
    os.makedirs(OUT, exist_ok=True)
    shutil.rmtree(os.path.join(APP, "calibration", "SIMULATED"), ignore_errors=True)
    # 1. scanner off: the TV static
    fresh({"source": "auto"})
    root = tk.Tk()
    root.geometry(f"{W}x{H}+0+0")
    app = ls.LizardApp(root)
    pump(root, 1.5)
    grab(root, "scanner-off")
    app.on_close()
    # 2-6. the simulated scanner
    fresh({"source": "sim"})
    root = tk.Tk()
    root.geometry(f"{W}x{H}+0+0")
    app = ls.LizardApp(root, simulate=True)
    assert pump(root, 15, lambda: app.lizard is not None)
    app.light.auto_tune()
    pump(root, 60, lambda: not app.light.busy)
    pump(root, 2.0)
    grab(root, "main-window")
    app.view_var.set("dots")
    app._view_changed()
    pump(root, 1.5)
    grab(root, "dots-only")
    w = lp.WallTestWindow(app)
    w.geometry("+600+20")
    for d in lp.SUGGESTED_WALLS_MM:
        w.dist_var.set(str(d))
        w.record()
        pump(root, 60, lambda: not w.busy)
    pump(root, 60, lambda: not w.busy)
    pump(root, 1.0)
    grab(root, "wall-test")
    w.destroy()
    pump(root, 20, lambda: app.depth_result is not None)
    pump(root, 2.5)
    grab(root, "depth")
    d = lp.DiagnosticsWindow(app)
    d.geometry("+300+20")
    d.run_quick()
    pump(root, 60, lambda: not d.busy)
    d.tape_var.set("330")
    app.lizard.sim_scene.ball = None              # a plain wall for the flat wall check
    pump(root, 1.0)
    d.run_flat_wall("flat_wall")
    pump(root, 30, lambda: not d.busy)
    d.results.pop("camera_b", None)
    pump(root, 0.5)
    grab(root, "diagnostics")
    d.destroy()
    app.on_close()
    # 7. the plain look
    fresh({"source": "sim", "look": "plain"})
    root = tk.Tk()
    root.geometry(f"{W}x{H}+0+0")
    app = ls.LizardApp(root, simulate=True)
    pump(root, 15, lambda: app.lizard is not None)
    pump(root, 3.0)
    grab(root, "plain-look")
    app.on_close()
    fresh({})
    os.remove(os.path.join(APP, "settings.json"))
    shutil.rmtree(os.path.join(APP, "calibration", "SIMULATED"), ignore_errors=True)


if __name__ == "__main__":
    main()
