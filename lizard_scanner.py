#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Lizard Scanner -- a standalone app for the Creality CR-Scan Lizard
# https://github.com/The-Dorkknight/lizard-scanner
#
# WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN.
# Built on the bones of Scantastic (https://github.com/The-Dorkknight/scantastic) and the Lizard engine
# from Who are yoUSB? (https://github.com/The-Dorkknight/who-are-yousb). Only the simulated scanner has
# been run with this app so far.
#
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
#
"""
Double-click a launcher (Lizard-Scanner-Linux.sh, -Windows.bat or -Mac.command) or run:
    python3 lizard_scanner.py              the real scanner
    python3 lizard_scanner.py --simulate   a pretend scanner (no hardware needed)
    python3 lizard_scanner.py --plain      without the wood-panelled TV look
    python3 lizard_scanner.py --version
The first run installs numpy and Pillow (and OpenCV on Windows / macOS) into lz_env/ next to this file.
"""

import os
import platform
import subprocess
import sys

APP_NAME = "Lizard Scanner"
APP_VERSION = "0.5.2-wip"
APP_BUILD = "2026-10-19 17:30"
APP_WIP_NOTICE = "WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN"
APP_COPYRIGHT = "Copyright Matthew Armstrong (https://github.com/The-Dorkknight)"
APP_LICENCE_LINE = "Licensed under the PolyForm Noncommercial License 1.0.0. No warranty."

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(SCRIPT_DIR, "lz_env")
BOOTSTRAP_LOG = os.path.join(SCRIPT_DIR, "bootstrap.log")
SETTINGS_PATH = os.path.join(SCRIPT_DIR, "settings.json")
SCANS_DIR = os.path.join(SCRIPT_DIR, "captures")   # recordings and saved frames (lizard_framecap folders)
REPORTS_DIR = os.path.join(SCRIPT_DIR, "reports")

if "--version" in sys.argv[1:]:
    print(f"{APP_NAME} v{APP_VERSION} (Built {APP_BUILD}) - {APP_WIP_NOTICE}")
    sys.exit(0)


# ===========================================================================
# First run: install what the app needs into its own folder
# ===========================================================================

def _log(msg):
    print(msg, flush=True)
    try:
        with open(BOOTSTRAP_LOG, "a") as f:
            f.write(msg + "\n")
    except OSError:
        pass


def _venv_python():
    return os.path.join(VENV_DIR, "Scripts", "python.exe") if os.name == "nt" else os.path.join(VENV_DIR, "bin", "python")


def _packages():
    pkgs = ["numpy", "Pillow"]
    if not sys.platform.startswith("linux"):
        pkgs.append("opencv-python")          # the camera path on Windows and macOS
    return pkgs


def _bootstrap():
    """Import numpy and Pillow, or set up lz_env/ with them and start again inside it."""
    try:
        import tkinter  # noqa: F401
    except ImportError:
        _log("The window toolkit (tkinter) is missing. On Linux: sudo apt install python3-tk")
        sys.exit(1)
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
        if not sys.platform.startswith("linux"):
            import cv2  # noqa: F401
        return
    except ImportError:
        pass
    if os.path.abspath(sys.prefix) == os.path.abspath(VENV_DIR):
        _log("The app's own Python environment is missing packages. Delete the lz_env folder and start again.")
        sys.exit(1)
    py = _venv_python()
    if not os.path.exists(py):
        _log(f"First run: making a private Python environment in {VENV_DIR} ...")
        r = subprocess.run([sys.executable, "-m", "venv", VENV_DIR])
        if r.returncode != 0 or not os.path.exists(py):
            _log("Could not make it. On Linux install the venv module: sudo apt install python3-venv python3-tk")
            sys.exit(1)
    _log("Installing " + ", ".join(_packages()) + " (once) ...")
    r = subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "-q"] + _packages())
    if r.returncode != 0:
        _log("The packages could not be installed (no internet?). The messages above say why.")
        sys.exit(1)
    _log("Done. Starting the app.")
    sys.exit(subprocess.call([py, os.path.abspath(__file__)] + sys.argv[1:]))


if __name__ == "__main__":
    _bootstrap()

import gc  # noqa: E402
import json  # noqa: E402
import queue  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import tkinter as tk  # noqa: E402
from tkinter import ttk, filedialog, messagebox  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont, ImageTk  # noqa: E402

sys.path.insert(0, SCRIPT_DIR)
import lizard_camera as lc  # noqa: E402
import lizard_panel as lp  # noqa: E402
import lizard_recording as lrec  # noqa: E402
try:
    import retro_skin          # optional look: a wood-panelled television (shared with Scantastic)
except Exception:
    retro_skin = None

LOOKS = [("Retro TV", "retro"), ("Retro TV, still screen", "retro_still"), ("Plain", "plain")]
SOURCES = [("CR-Scan Lizard (USB)", "auto"), ("Simulated scanner", "sim"), ("Play back a recording...", "replay")]
MODE_NAMES = [("1280x720 MJPEG, 10 fps (CR Scan's)", "mjpg"), ("640x480 uncompressed, 5 fps", "yuyv")]
VIEWS = [("Picture", "picture"), ("Pattern only", "dots"), ("Depth", "depth"), ("A + B", "both")]
WATCH_MS = 2500
GUI_MS = 66                          # screen refresh, about 15 a second
GC_MS = 4000                         # Python's cycle collector runs this often, on the window's thread
GUIDE_BEST_MM = (180.0, 350.0)       # green band of the depth colours
GUIDE_FADE_MM = 60.0
SIM_DEMO_SCENE = dict(wall_mm=330.0, ball=(0.0, 10.0, 230.0, 45.0))


def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(s):
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(s, f, indent=2)
    except OSError:
        pass


def _font(px, bold=False):
    for name in (["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf"] if bold
                 else ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "arial.ttf"]):
        try:
            return ImageFont.truetype(name, px)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def depth_colours(z_mm, relief=True):
    """Depth as a picture: green inside the scanner's sweet spot, through yellow to red outside it,
    black = no depth. Like Scantastic's distance guide, plus relief shading (light from the top left)
    so the shape shows."""
    z = np.asarray(z_mm, np.float32)
    ok = z > 0
    lo, hi = GUIDE_BEST_MM
    out = np.maximum(lo - z, z - hi)
    t = np.clip(out / GUIDE_FADE_MM, 0, 1)                  # 0 = in the band, 1 = far outside
    shade = np.clip(1.15 - (z - 150.0) / 400.0, 0.35, 1.0)  # nearer = brighter
    if relief and z.ndim == 2 and z.shape[0] > 2 and z.shape[1] > 2:
        zf = np.where(ok, z, np.nan)
        gy, gx = np.gradient(zf)
        gx, gy = np.nan_to_num(gx), np.nan_to_num(gy)
        k = 0.6                                              # about one pixel's width in mm at 30 cm
        n = np.sqrt(gx * gx + gy * gy + k * k)
        lam = (gx * 0.55 + gy * 0.55 + k * 0.63) / n         # light from the top left, towards the scene
        shade = shade * np.clip(0.45 + 0.75 * lam, 0.3, 1.2)
    r = np.where(t < 0.5, t * 2, 1.0) * 255
    g = np.where(t < 0.5, 1.0, (1 - t) * 2) * 220
    b = np.zeros_like(z) + 40
    rgb = np.clip(np.stack([r, g, b], -1) * shade[..., None], 0, 255)
    rgb[~ok] = (12, 14, 13)
    return rgb.astype(np.uint8)


class LizardApp:
    def __init__(self, root, simulate=False):
        self.root = root
        self.title = f"{APP_NAME} v{APP_VERSION} (Built {APP_BUILD})"
        root.title(self.title)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.settings = load_settings()
        if simulate:
            self.settings["source"] = "sim"
        self.lizard = None
        self.tuner = None
        self.serial = ""
        self.mode = self.settings.get("mode", lc.DEFAULT_MODE)
        if self.mode not in lc.MODES:
            self.mode = lc.DEFAULT_MODE
        self.reports_dir = REPORTS_DIR
        self.app_version = f"{APP_NAME} {APP_VERSION} (Built {APP_BUILD})"
        self.eye_results = {}                 # light / gain test answers, for the diagnostics report
        self.calib = None
        self.solver = None
        self.recorder = None                 # lizard_recording.CaptureRun while RECORD is on
        self._pending_note = ""
        self.last_capture = None
        self.record_started = 0.0
        self.starting = False
        self.closing = False
        self.enabled = self.settings.get("scanner_on", True) is not False
        self.replay_folder = None
        self.msg = queue.Queue()
        self.depth_lock = threading.Lock()
        self.depth_result = None              # (z_mm, grey, frame index)
        self.depth_thread = None
        self._photo = None
        self._ticks = 0
        self._timers = {}
        self._tag2 = ""
        self.view_var_cache = "picture"
        self.muted = "#666666"
        self._apply_look()
        self._build_ui()
        self._refresh_ui()
        # Python's cycle collector may otherwise run on a camera thread and free Tk objects there, which
        # makes Tcl abort the program. So it only runs here, on the window's thread, every few seconds.
        gc.disable()
        self._later("collect", GC_MS, self._collect)
        self._later("tick", GUI_MS, self._tick)
        self._later("watch", 300, self._watch)
        self._later("poll", 100, self._poll)

    def _later(self, name, ms, fn):
        """A named timer; on_close cancels them all, so none fires into a closed window."""
        if not self.closing:
            self._timers[name] = self.root.after(ms, fn)

    def _collect(self):
        if self.closing:
            return
        gc.collect()
        self._later("collect", GC_MS, self._collect)

    # ------------------------------------------------------------------ look
    @property
    def is_sim(self):
        return self.source_code() == "sim"

    def _apply_look(self):
        """Decide the look before any widget exists. Any trouble with the retro skin means the plain look."""
        self.skin = None
        want = self.settings.get("look", "retro")
        self.look = want if want in ("retro", "retro_still") else "plain"
        if "--plain" in sys.argv[1:] or retro_skin is None:
            self.look = "plain"
        if self.look == "plain":
            return
        try:
            self.skin = retro_skin.apply_style(self.root)
            self.muted = self.skin["muted"]
        except Exception as exc:
            self.skin = None
            self.look = "plain"
            print(f"Retro look not applied ({exc}); using the plain look.")

    # ------------------------------------------------------------------ layout
    def _build_ui(self):
        retro = self.skin is not None
        ttk.Label(self.root, text=f"v{APP_VERSION}  •  {APP_BUILD}  •  {APP_WIP_NOTICE}",
                  anchor="e", foreground=self.muted, padding=(10, 1)).pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="Plug in the CR-Scan Lizard: the picture starts by itself. "
                                             "No scanner? Choose Simulated scanner.")
        status = ttk.Label(self.root, textvariable=self.status_var, anchor="w", padding=(10, 4), relief=tk.SUNKEN)
        status.pack(side=tk.BOTTOM, fill=tk.X)
        self.root.bind("<Configure>", lambda e: status.configure(wraplength=max(200, self.root.winfo_width() - 30)),
                       add="+")
        if retro:
            main = tk.Frame(self.root, highlightthickness=0, borderwidth=0,
                            bg="#%02x%02x%02x" % retro_skin.PALETTE["wood_mid"])
            self._backdrop = retro_skin.Backdrop(main)
        else:
            main = ttk.Frame(self.root, padding=8)
        main.pack(fill=tk.BOTH, expand=True)

        # ---- side panel (scrolls when the window is short)
        side = ttk.Frame(main, padding=(0, 8, 2, 8) if retro else 0)
        side.pack(side=tk.RIGHT, fill=tk.Y, padx=(18, 18) if retro else (8, 0), pady=18 if retro else 0)
        sc = tk.Canvas(side, highlightthickness=0, borderwidth=0, width=300)
        if retro:
            sc.configure(bg=self.skin["panel"])
        sbar = ttk.Scrollbar(side, orient=tk.VERTICAL, command=sc.yview)
        sc.configure(yscrollcommand=sbar.set)
        sc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = ttk.Frame(sc, padding=(10, 0, 4, 0))
        sc.create_window((0, 0), window=right, anchor="nw")

        def fit(_e=None):
            sc.configure(scrollregion=sc.bbox("all"), width=right.winfo_reqwidth())
            if right.winfo_reqheight() > sc.winfo_height() > 1:
                sbar.pack(side=tk.RIGHT, fill=tk.Y)
            else:
                sbar.pack_forget()
        right.bind("<Configure>", fit)
        sc.bind("<Configure>", fit)

        def wheel(e):
            if sbar.winfo_ismapped() and str(e.widget).startswith(str(side)):
                sc.yview_scroll(-1 if (getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0) else 1, "units")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            sc.bind_all(seq, wheel, add="+")

        scan = ttk.LabelFrame(right, text="Scanner", padding=8)
        scan.pack(fill=tk.X, pady=(0, 10))
        self.badge = tk.Label(scan, text="", fg="white", bg="#5a5a5a", anchor="w", justify="left", padx=8, pady=5,
                              wraplength=260, font=("TkDefaultFont", 11, "bold"))
        self.badge.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(scan, text="Source:").pack(anchor="w")
        self.source_var = tk.StringVar(value=next((n for n, c in SOURCES if c == self.settings.get("source", "auto")),
                                                  SOURCES[0][0]))
        box = ttk.Combobox(scan, textvariable=self.source_var, state="readonly", width=30,
                           values=[n for n, _c in SOURCES])
        box.pack(fill=tk.X, pady=(2, 6))
        box.bind("<<ComboboxSelected>>", lambda e: self._source_changed())
        ttk.Label(scan, text="Video:").pack(anchor="w")
        self.mode_var = tk.StringVar(value=dict((c, n) for n, c in MODE_NAMES)[self.mode])
        mbox = ttk.Combobox(scan, textvariable=self.mode_var, state="readonly", width=30,
                            values=[n for n, _c in MODE_NAMES])
        mbox.pack(fill=tk.X, pady=(2, 6))
        mbox.bind("<<ComboboxSelected>>", lambda e: self._mode_changed())
        self.state_var = tk.StringVar(value="")
        ttk.Label(scan, textvariable=self.state_var, foreground=self.muted, wraplength=260,
                  justify="left").pack(anchor="w")

        self.light = lp.LightPanel(right, self)
        self.light.pack(fill=tk.X, pady=(0, 10))

        depth = ttk.LabelFrame(right, text="Depth (wall calibration)", padding=8)
        depth.pack(fill=tk.X, pady=(0, 10))
        self.calib_var = tk.StringVar(value="Not calibrated yet.")
        ttk.Label(depth, textvariable=self.calib_var, foreground=self.muted, wraplength=260,
                  justify="left").pack(anchor="w")
        ttk.Button(depth, text="Wall test (calibrate)...", command=self.wall_test).pack(fill=tk.X, pady=(6, 2))
        ttk.Button(depth, text="Zip this scanner's calibration (one file)...",
                   command=self.zip_calibration).pack(fill=tk.X, pady=2)
        self.ply_btn = ttk.Button(depth, text="Save this view as points (.ply)...", command=self.save_ply)
        self.ply_btn.pack(fill=tk.X, pady=2)

        tests = ttk.LabelFrame(right, text="Diagnostics", padding=8)
        tests.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(tests, text="Diagnostics: quick checks and report...",
                   command=self.diagnostics).pack(fill=tk.X, pady=2)
        eye = ttk.Frame(tests)
        eye.pack(fill=tk.X)
        ttk.Button(eye, text="Light test", command=self.light_test).pack(side=tk.LEFT, fill=tk.X, expand=True, pady=2)
        ttk.Button(eye, text="Gain test", command=self.gain_test).pack(side=tk.LEFT, fill=tk.X, expand=True,
                                                                       padx=(3, 0), pady=2)
        ttk.Button(tests, text="Every camera setting (advanced)...",
                   command=lambda: lp.SettingsWindow(self) if self.lizard else self.status("Start the scanner first.")
                   ).pack(fill=tk.X, pady=2)

        cap = ttk.LabelFrame(right, text="Capture", padding=8)
        cap.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(cap, text="Save frame now (each running camera)", command=self.save_frame_now).pack(fill=tk.X,
                                                                                                    pady=2)
        lab = ttk.Frame(cap)
        lab.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(lab, text="Name:").pack(side=tk.LEFT)
        self.capture_label_var = tk.StringVar(value="")
        ttk.Entry(lab, textvariable=self.capture_label_var, width=18).pack(side=tk.LEFT, padx=4, fill=tk.X,
                                                                          expand=True)
        ttk.Label(lab, text="every").pack(side=tk.LEFT)
        self.every_var = tk.StringVar(value="1")
        ttk.Spinbox(lab, from_=1, to=30, textvariable=self.every_var, width=3).pack(side=tk.LEFT, padx=(4, 0))
        note = ttk.Frame(cap)
        note.pack(fill=tk.X, pady=(4, 0))
        self.note_var = tk.StringVar(value="")
        e = ttk.Entry(note, textvariable=self.note_var)
        e.pack(side=tk.LEFT, fill=tk.X, expand=True)
        e.bind("<Return>", lambda _e: self.add_note())
        ttk.Button(note, text="Add note", command=self.add_note).pack(side=tk.LEFT, padx=(4, 0))
        self.hide_serial_var = tk.BooleanVar(value=self.settings.get("hide_serial", True) is not False)
        ttk.Checkbutton(cap, text="Hide the scanner's serial in saved files", variable=self.hide_serial_var,
                        command=lambda: (self.settings.__setitem__("hide_serial", bool(self.hide_serial_var.get())),
                                         save_settings(self.settings))).pack(anchor="w", pady=(4, 0))
        self.capture_info_var = tk.StringVar(value="Every saved picture carries its time, camera, exposure, gain "
                                                   "and every other setting. Notes mark the next picture.")
        ttk.Label(cap, textvariable=self.capture_info_var, foreground=self.muted, wraplength=260,
                  justify="left").pack(anchor="w", pady=(4, 0))

        more = ttk.LabelFrame(right, text="Files", padding=8)
        more.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(more, text="Open the captures folder", command=lambda: self._open_folder(SCANS_DIR)).pack(
            fill=tk.X, pady=2)
        ttk.Button(more, text="About / licence...", command=self.about).pack(fill=tk.X, pady=2)
        look_row = ttk.Frame(more)
        look_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(look_row, text="Look:").pack(side=tk.LEFT)
        self.look_var = tk.StringVar(value=next(n for n, c in LOOKS if c == self.settings.get("look", "retro")))
        lbox = ttk.Combobox(look_row, textvariable=self.look_var, state="readonly", width=20,
                            values=[n for n, _c in LOOKS])
        lbox.pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)
        lbox.bind("<<ComboboxSelected>>", lambda e: self._look_changed())
        if retro_skin is None:
            lbox.config(state=tk.DISABLED)

        # ---- left: view bar, screen, big buttons
        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(18, 0) if retro else 0,
                  pady=(18, 0) if retro else 0)
        bar = ttk.Frame(left, padding=(8, 6) if retro else (0, 0, 0, 6))
        bar.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(bar, text="View:").pack(side=tk.LEFT)
        self.view_var = tk.StringVar(value=self.settings.get("view", "picture"))
        self.view_btns = {}
        for name, code in VIEWS:
            b = ttk.Radiobutton(bar, text=name, value=code, variable=self.view_var, command=self._view_changed)
            b.pack(side=tk.LEFT, padx=(8, 0))
            self.view_btns[code] = b

        strip = ttk.Frame(left, padding=8 if retro else 0)
        strip.pack(side=tk.BOTTOM, fill=tk.X, pady=(14, 18) if retro else (8, 0))
        self.start_btn = lp.BigButton(strip, "START SCANNER", self.toggle_scanner, lp.GO_GREEN,
                                      subtext="or just plug it in")
        self.start_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.rec_btn = lp.BigButton(strip, "RECORD", self.toggle_record, lp.STOP_RED,
                                    subtext="saves every picture with its settings")
        self.rec_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        self.cal_btn = lp.BigButton(strip, "WALL TEST", self.wall_test, lp.CAL_BLUE,
                                    subtext="rough depth calibration")
        self.cal_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        self.static = None
        self._lamp = None
        if retro:
            skin = self.skin
            bezel = tk.Frame(left, bg=skin["bezel"], highlightthickness=2, highlightbackground=skin["bezel_edge"])
            bezel.pack(fill=tk.BOTH, expand=True)
            foot = tk.Canvas(bezel, height=18, bg=skin["bezel"], highlightthickness=0, borderwidth=0)
            foot.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=(3, 4))
            dot = foot.create_oval(2, 4, 12, 14, fill=skin["amber"], outline="#0c0b0a")
            label = foot.create_text(20, 9, anchor="w", text="Scanner on standby", fill=skin["muted"],
                                     font=("TkDefaultFont", 8))
            self._lamp = (foot, dot, label)
            self.canvas = tk.Canvas(bezel, width=640, height=360, bg="#090c0b", highlightthickness=0)
            self.canvas.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 0))
            self.static = retro_skin.Static(self.canvas, animate=self.look == "retro",
                                            lines=("Scanner off", "Plug in the Lizard, or choose Simulated scanner"))
            self._corners = [ImageTk.PhotoImage(t) for t in retro_skin.corner_masks()]
        else:
            self.canvas = tk.Canvas(left, width=640, height=360, bg="black", highlightthickness=0)
            self.canvas.pack(fill=tk.BOTH, expand=True)
            self._corners = None
        self._later("offscreen", 200, self._show_off_screen)

    def _show_off_screen(self):
        if self.lizard is not None:
            return
        if self.static is not None:
            self.static.start()
        else:
            self.canvas.delete("all")
            w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
            self.canvas.create_text(w / 2, h / 2, fill="#bbbbbb", font=("Helvetica", 14),
                                    text="Scanner off\nPlug in the Lizard, or choose Simulated scanner")

    def _set_lamp(self, live):
        if self._lamp is None:
            return
        foot, dot, label = self._lamp
        foot.itemconfigure(dot, fill=self.skin["live"] if live else self.skin["amber"])
        foot.itemconfigure(label, text="Scanner live" if live else "Scanner on standby")

    # ------------------------------------------------------------------ host interface (lizard_panel)
    def status(self, text):
        self.status_var.set(text)

    def calibration_changed(self, result, reference):
        self.calib = result
        self.solver = lc.DepthSolver(result, reference)
        if self.is_sim and self.lizard is not None:
            self.lizard.sim_scene.wall_mm = SIM_DEMO_SCENE["wall_mm"]
            self.lizard.sim_scene.ball = SIM_DEMO_SCENE["ball"]
        self.view_var.set("depth")
        self._view_changed()
        self._refresh_ui()

    # ------------------------------------------------------------------ choices
    def source_code(self):
        return dict(SOURCES).get(self.source_var.get(), "auto")

    def _source_changed(self):
        code = self.source_code()
        if code == "replay":
            folder = filedialog.askdirectory(title="Choose a recording folder (it holds session.json)",
                                             initialdir=SCANS_DIR if os.path.isdir(SCANS_DIR) else SCRIPT_DIR)
            if not folder or not os.path.exists(os.path.join(folder, "session.json")):
                self.status("That folder is not a recording (no session.json).")
                self.source_var.set(SOURCES[0][0])
                return
            self.replay_folder = folder
        self.settings["source"] = code if code != "replay" else "auto"
        save_settings(self.settings)
        self.stop_scanner()
        self.enabled = True
        self._later("restart", 200, self.start_scanner)

    def _mode_changed(self):
        self.mode = dict(MODE_NAMES).get(self.mode_var.get(), lc.DEFAULT_MODE)
        self.settings["mode"] = self.mode
        save_settings(self.settings)
        if self.lizard is not None:
            self.stop_scanner()
            self._later("restart", 300, self.start_scanner)
        self._load_calibration()

    def _look_changed(self):
        self.settings["look"] = dict(LOOKS).get(self.look_var.get(), "retro")
        save_settings(self.settings)
        self.status("The new look applies the next time the app starts.")

    def _view_changed(self):
        v = self.view_var.get()
        self.view_var_cache = v
        if v == "depth" and self.solver is None:
            self.view_var.set("picture")
            self.status("Depth needs a wall calibration first: press WALL TEST.")
            return
        self.settings["view"] = self.view_var.get()
        save_settings(self.settings)
        if v == "both" and self.lizard is not None and self.lizard.stream("B") is None and self.lizard.connected("B"):
            try:
                self.lizard.start("B", self.mode)          # starts at a 1 ms flash
                if self.tuner is not None and self.tuner.step is not None:
                    self.tuner.apply_step(self.tuner.step)   # then the same light level as camera A
            except Exception as exc:
                self.status(f"Camera B would not start: {exc}")
        elif v != "both" and self.lizard is not None and self.lizard.stream("B") is not None:
            self.lizard.stop("B")
        self._ensure_depth_thread()

    # ------------------------------------------------------------------ scanner on / off
    def toggle_scanner(self):
        if self.lizard is not None or self.starting:
            self.enabled = False
            self.settings["scanner_on"] = False
            save_settings(self.settings)
            self.stop_scanner()
            self.status("Scanner off. It stays off until you press START SCANNER.")
        else:
            self.enabled = True
            self.settings["scanner_on"] = True
            save_settings(self.settings)
            self.start_scanner()

    def start_scanner(self, auto=False):
        if self.lizard is not None or self.starting or self.closing:
            return
        code = self.source_code()
        backend = "sim" if code == "sim" else (f"replay:{self.replay_folder}" if code == "replay" and
                                               self.replay_folder else "auto")
        self.starting = True
        self._refresh_ui()
        self.status("Starting the scanner...")
        mode = self.mode

        def work():
            if backend == "auto" and sys.platform.startswith("linux"):
                import shutil
                if shutil.which("v4l2-ctl") is None:
                    raise lc.CameraError("v4l2-ctl is missing. Open a terminal and run:  sudo apt install v4l-utils")
            lz = lc.Lizard(backend)
            if backend == "sim":
                lz.sim_scene = lc.SimScene(**SIM_DEMO_SCENE)
            if not lz.connected("A"):
                raise lc.CameraError(lc.diagnose_no_camera())
            serial = lz.serial() or "unknown"
            lz.start("A", mode)
            tuner = lc.Tuner(lz, "A")
            if not backend.startswith("replay"):
                tuner.apply_preset("Normal")          # CR Scan's own starting point
            return lz, tuner, serial

        def done(res):
            self.starting = False
            if isinstance(res, Exception):
                if not auto:
                    self.status(f"The scanner did not start: {res}")
                self._last_auto_fail = auto
                self._refresh_ui()
                return
            self.lizard, self.tuner, self.serial = res
            if self.static is not None:
                self.static.stop()
            self._set_lamp(True)
            self._load_calibration()
            self._view_changed()
            self.status(f"Scanner running ({self.serial})." + ("" if self.solver else
                        " Press WALL TEST once to turn on depth."))
            self._refresh_ui()

        self.light.worker.run(work, done)

    def stop_scanner(self):
        self.stop_record()
        lz, self.lizard, self.tuner = self.lizard, None, None
        if lz is not None:
            threading.Thread(target=lz.stop_all, daemon=True).start()
        self._set_lamp(False)
        with self.depth_lock:
            self.depth_result = None
        self._refresh_ui()
        self._later("offscreen", 150, self._show_off_screen)

    def _watch(self):
        """Start by itself when a Lizard is plugged in (USB source, Linux); notice when it goes."""
        if self.closing:
            return
        try:
            if self.lizard is not None:
                s = self.lizard.streams.get("A")
                if s is not None and not s.running():
                    why = s.error or "the scanner stopped sending pictures (unplugged?)"
                    self.stop_scanner()
                    self.status(f"Scanner stopped: {why}. It starts again by itself when it is back.")
            elif self.enabled and not self.starting:
                code = self.source_code()
                if code == "sim" or (code == "replay" and self.replay_folder):
                    self.start_scanner(auto=True)
                elif code == "auto":
                    found = lc.detect()
                    if found is None or found:
                        self.start_scanner(auto=True)
        finally:
            self._later("watch", WATCH_MS, self._watch)

    # ------------------------------------------------------------------ calibration
    def _load_calibration(self):
        self.calib = self.solver = None
        if self.serial:
            got = lc.load_calibration(self.serial)
            if (not got and self.lizard is not None and self.lizard.kind() == "replay"):
                # a capture with the serial hidden: use the scanner's calibration if there is just one for this mode
                mine = [s for s, m in lc.list_calibrations() if m == self.mode and s != "SIMULATED"]
                if len(mine) == 1:
                    got = lc.load_calibration(mine[0])
            if got and got[0].get("mode") == self.mode:
                self.calib = got[0]
                self.solver = lc.DepthSolver(*got)
        self._refresh_ui()

    def wall_test(self):
        if self.lizard is None:
            self.status("Start the scanner first, then press WALL TEST.")
            return
        lp.WallTestWindow(self)

    def zip_calibration(self):
        """Everything in calibration/<serial>/ (fit, wall patterns, the camera's own wall pictures, report)
        in one zip next to it, serial hidden unless the Capture box says otherwise."""
        serial = self.serial or ""
        folder = lc.calibration_folder(serial) if serial else None
        if not folder or not os.path.isdir(folder):
            have = lc.list_calibrations()
            if len(have) == 1:
                folder = lc.calibration_folder(have[0][0])
                serial = have[0][0]
            else:
                self.status("No calibration for this scanner yet (start it, or run the wall test).")
                return
        hide = bool(self.hide_serial_var.get())
        try:
            path = lc.zip_calibration(folder, hide_serial=hide)
        except OSError as exc:
            self.status(f"Could not make the zip: {exc}")
            return
        shown = path.replace(serial, lc.mask_serial(serial)) if hide and serial else path
        self.status(f"Calibration zipped: {shown}")
        self._open_folder(os.path.dirname(path))

    def light_test(self):
        if self.lizard is None:
            self.status("Start the scanner first.")
            return
        lp.EyeTestWindow(self, "exposure")

    def gain_test(self):
        if self.lizard is None:
            self.status("Start the scanner first.")
            return
        lp.EyeTestWindow(self, "gain")

    def diagnostics(self):
        lp.DiagnosticsWindow(self)

    # ------------------------------------------------------------------ depth (background)
    def _ensure_depth_thread(self):
        if self.view_var.get() != "depth" or self.solver is None:
            return
        if self.depth_thread is not None and self.depth_thread.is_alive():
            return
        self.depth_thread = threading.Thread(target=self._depth_loop, daemon=True)
        self.depth_thread.start()

    def _depth_loop(self):
        last = -1
        while not self.closing and self.view_var_cache == "depth":
            lz, solver = self.lizard, self.solver
            s = lz.stream("A") if lz else None
            if s is None or solver is None:
                time.sleep(0.2)
                continue
            f = s.wait_frame(last, lit=True, timeout=1.0)
            if f is None:
                continue
            last = f.index
            try:
                z, _score = solver.solve(f.grey)
            except Exception as exc:
                self.msg.put(("status", f"Depth failed: {exc}"))
                time.sleep(1.0)
                continue
            with self.depth_lock:
                self.depth_result = (z, f.grey, f.index)

    # ------------------------------------------------------------------ recording
    def _capture_args(self, snapshot=False):
        return dict(app=self.app_version, mode=self.mode, snapshot=snapshot,
                    hide_serial=bool(self.hide_serial_var.get()),
                    every=1 if snapshot else max(1, int(self.every_var.get() or 1)),
                    rung=lambda: (None if self.tuner is None or self.tuner.custom else self.tuner.step))

    def toggle_record(self):
        if self.recorder is not None:
            self.stop_record()
            return
        if self.lizard is None:
            self.status("Start the scanner first.")
            return
        label = self.capture_label_var.get().strip()
        self.recorder = lrec.CaptureRun(self.lizard, SCANS_DIR, label=label, **self._capture_args())
        self.recorder.event("app_state", view=self.view_var.get(), light=self.tuner.describe() if self.tuner else "",
                            calibrated=bool(self.calib), source=self.source_code())
        if self._pending_note:
            self.recorder.mark(self._pending_note)
            self._pending_note = ""
        self.record_started = time.time()
        self.rec_btn.config(text="STOP RECORDING", subtext="0 pictures")
        self.rec_btn.set_pulse(True)
        self.status(f"Recording every picture with its settings to {self.recorder.folder}")

    def stop_record(self):
        rec, self.recorder = self.recorder, None
        if rec is None:
            return
        rec.stop()
        self.rec_btn.set_pulse(False)
        self.rec_btn.config(text="RECORD", subtext="saves every picture with its settings")
        self.last_capture = rec.folder
        self.capture_info_var.set(rec.summary())
        self.status(f"Saved {rec.count()} pictures in {rec.folder}. Play it back with Source > Play back a recording.")

    def save_frame_now(self):
        """One picture from each running camera, with every setting, into its own capture folder."""
        if self.lizard is None:
            self.status("Start the scanner first.")
            return
        if self.recorder is not None:
            self.status("Recording already saves every picture; add a note instead.")
            return

        def done(run):
            self.msg.put(("snapshot", run.folder))
        run = lrec.CaptureRun(self.lizard, SCANS_DIR, label=self.capture_label_var.get().strip() or "snapshot",
                              on_done=done, **self._capture_args(snapshot=True))
        run.event("app_state", view=self.view_var.get(), light=self.tuner.describe() if self.tuner else "",
                  calibrated=bool(self.calib), source=self.source_code())
        if self._pending_note:
            run.mark(self._pending_note)
            self._pending_note = ""
        self.status("Saving one picture from each running camera...")

    def add_note(self):
        note = self.note_var.get().strip()
        if not note:
            self.status("Type a note first, e.g. 'covered camera A'.")
            return
        self.note_var.set("")
        if self.recorder is not None:
            self.recorder.mark(note)
            self.status(f"Note added to the recording at {time.time() - self.record_started:.1f} s: {note}")
        else:
            self._pending_note = note
            self.status(f"Note kept for the next recording or saved frame: {note}")

    def save_ply(self):
        with self.depth_lock:
            res = self.depth_result
        if res is None or self.calib is None:
            self.status("Turn on the Depth view first (it needs a wall calibration).")
            return
        z, grey, _i = res
        os.makedirs(SCANS_DIR, exist_ok=True)
        path = filedialog.asksaveasfilename(title="Save points", defaultextension=".ply",
                                            initialdir=SCANS_DIR, initialfile=time.strftime("lizard_%Y%m%d_%H%M%S.ply"),
                                            filetypes=[("Point cloud", "*.ply")])
        if not path:
            return
        pts = lc.depth_to_points(z, self.calib["focal_px"], self.calib["c_mm"])
        lc.write_ply(path, pts, grey[z > 0])
        self.status(f"Saved {len(pts)} points (millimetres) to {path}")

    # ------------------------------------------------------------------ screen
    def _tick(self):
        if self.closing:
            return
        try:
            self.view_var_cache = self.view_var.get()
            if self.lizard is not None:
                self._draw_live()
            if self.recorder is not None:
                secs = int(time.time() - self.record_started)
                self.rec_btn.config(subtext=f"{self.recorder.count()} pictures  •  {secs // 60}:{secs % 60:02d}")
            self._ticks += 1
            if self._ticks % 8 == 0:                  # about twice a second is plenty for the readouts
                if not self.light.busy:
                    self.light.refresh()
                self._refresh_state_line()
        finally:
            self._later("tick", GUI_MS, self._tick)

    def _poll(self):
        try:
            while True:
                kind, payload = self.msg.get_nowait()
                if kind == "status":
                    self.status(payload)
                elif kind == "snapshot":
                    self.last_capture = payload
                    try:
                        self.capture_info_var.set(lrec.fc.summarise(payload))
                    except Exception:
                        pass
                    self.status(f"Saved a picture from each running camera, with their settings, in {payload}")
        except queue.Empty:
            pass
        self._later("poll", 150, self._poll)

    def _pattern_word(self, a, long=False):
        """What the tag says when the light is in every picture: is the projector's stripe pattern there?"""
        f = a.latest
        if f is None:
            return ""
        key = (id(a), f.index)
        if getattr(self, "_pattern_cache", (None,))[0] != key:
            try:
                score = lp.diag.pattern_score(f.grey)
            except Exception:
                score = 0.0
            self._pattern_cache = (key, score)
        score = self._pattern_cache[1]
        if score > lp.diag.PATTERN_MIN:
            return (f"stripe pattern in {score * 100:.0f}% of the view (lit every picture)" if long
                    else f"stripes {score * 100:.0f}%")
        return "no stripe pattern seen (closer to a surface, or Brighter)" if long else "no stripes seen"

    def _frame_picture(self):
        """The picture for the chosen view (RGB numpy) plus a tag line, or (None, None)."""
        lz = self.lizard
        a = lz.stream("A")
        v = self.view_var.get()
        if a is None or a.latest is None:
            return None, None
        pulse, _share, ratio = a.pulse.state()
        tag = f"A  •  {a.fps():.1f} fps  •  " + ("lights pulsing" if pulse else self._pattern_word(a))
        if v == "dots" and a.latest_lit is not None:
            dark = a.latest_dark.grey if (a.latest_dark is not None and pulse) else None
            pat = lc.pattern_image(a.latest_lit.grey, dark)
            g = np.clip(pat * 45 + 110, 0, 255).astype(np.uint8)
            rgb = np.stack([g, (g * 0.75).astype(np.uint8), (g * 0.35).astype(np.uint8)], -1)
            return rgb, tag + "  •  pattern only"
        if v == "depth":
            with self.depth_lock:
                res = self.depth_result
            if res is None:
                g = a.latest.grey
                return np.repeat(g[..., None], 3, 2), tag + "  •  working out depth..."
            z, _g, _i = res
            ok = z > 0
            med = float(np.median(z[ok])) if ok.any() else 0
            ms = getattr(self.solver, "last_ms", 0.0) if self.solver is not None else 0.0
            self._tag2 = (f"Depth: {ok.mean() * 100:.0f}% of the view  •  middle {med / 10:.1f} cm  •  "
                          f"{ms:.0f} ms  •  rough")
            return depth_colours(z), tag
        if v == "both":
            b = lz.stream("B")
            ga = a.latest.grey
            gb = b.latest.grey if (b is not None and b.latest is not None) else np.zeros_like(ga)
            if gb.shape != ga.shape:
                gb = np.asarray(Image.fromarray(gb).resize((ga.shape[1], ga.shape[0])))
            both = np.hstack([ga, np.full((ga.shape[0], 6), 30, np.uint8), gb])
            return np.repeat(both[..., None], 3, 2), "A (left)  •  B (right)" + ("" if b else "  •  B not running")
        g = (a.latest_lit or a.latest).grey
        return np.repeat(g[..., None], 3, 2), tag

    def _draw_live(self):
        self._tag2 = ""
        rgb, tag = self._frame_picture()
        if rgb is None:
            return
        cw, ch = max(self.canvas.winfo_width(), 32), max(self.canvas.winfo_height(), 32)
        img = Image.fromarray(rgb)
        scale = min(cw / img.width, ch / img.height)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.BILINEAR)
        frame = Image.new("RGB", (cw, ch), (9, 12, 11))
        frame.paste(img, ((cw - img.width) // 2, (ch - img.height) // 2))
        d = ImageDraw.Draw(frame)
        f = _font(13, True)
        text = f"CR-Scan Lizard  •  {tag}"
        if self.recorder is not None:
            text = "● REC  " + text
        box = d.textbbox((0, 0), text, font=f)
        d.rounded_rectangle((10, 10, 22 + box[2] - box[0], 18 + box[3] - box[1] + 4), radius=6,
                            fill=(176, 58, 46) if self.recorder is not None else (40, 110, 70))
        d.text((16, 12 - box[1] + 2), text, font=f, fill=(255, 255, 255))
        if self._tag2:
            f2 = _font(12)
            b2 = d.textbbox((0, 0), self._tag2, font=f2)
            y = 18 + box[3] - box[1] + 10
            d.rounded_rectangle((10, y, 22 + b2[2] - b2[0], y + 8 + b2[3] - b2[1] + 2), radius=6, fill=(20, 24, 22))
            d.text((16, y + 4 - b2[1]), self._tag2, font=f2, fill=(230, 230, 230))
        if self.view_var.get() == "depth":
            self._draw_depth_key(d, cw, ch)
        self._photo = ImageTk.PhotoImage(frame)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        if self._corners:
            r = self._corners[0].width()
            for photo, (x, y) in zip(self._corners, ((0, 0), (cw - r, 0), (0, ch - r), (cw - r, ch - r))):
                self.canvas.create_image(x, y, image=photo, anchor="nw")

    def _draw_depth_key(self, d, cw, ch):
        w, h = 220, 14
        x0, y0 = cw - w - 18, ch - h - 34
        zs = np.linspace(120, 420, w, dtype=np.float32)[None, :].repeat(h, 0)
        key = Image.fromarray(depth_colours(zs))
        d._image.paste(key, (x0, y0))
        f = _font(11)
        for mm in (150, 250, 350, 400):
            x = x0 + int((mm - 120) / 300 * w)
            d.line((x, y0 + h, x, y0 + h + 4), fill=(230, 230, 230))
            d.text((x - 12, y0 + h + 5), f"{mm // 10} cm", font=f, fill=(230, 230, 230))
        d.text((x0, y0 - 16), "green = best distance", font=f, fill=(230, 230, 230))

    # ------------------------------------------------------------------ status
    def _refresh_state_line(self):
        if self.lizard is None:
            self.state_var.set("Off" if not self.starting else "Starting...")
            return
        a = self.lizard.stream("A")
        if a is None:
            self.state_var.set("Camera A not running")
            return
        pi = a.pulse.info()
        pulse, ratio = pi["pulsing"], pi["ratio"]
        self.state_var.set(f"Camera A: {a.fps():.1f} pictures a second, "
                           + (f"lights pulsing, {pi['pattern']} (lit {ratio:.1f}× dark)" if pulse
                              else self._pattern_word(a, long=True))
                           + f". Camera B: {'running' if self.lizard.stream('B') else 'off'}.")

    def _refresh_ui(self):
        on = self.lizard is not None
        if on:
            kind = self.lizard.kind()
            label = {"sim": "Simulated Lizard", "replay": "Recording (play-back)"}.get(kind, "CR-Scan Lizard")
            self.badge.config(text=f"{label}\n{self.serial}", bg="#2e7d4f" if kind not in ("sim", "replay") else "#7a5a1e")
        else:
            self.badge.config(text="No scanner running", bg="#5a5a5a")
        self.start_btn.config(text="STOP SCANNER" if on or self.starting else "START SCANNER",
                              colour="#7b7f87" if on else lp.GO_GREEN,
                              subtext="turns the lights off" if on else "or just plug it in")
        self.rec_btn.config(state=tk.NORMAL if on else tk.DISABLED)
        self.cal_btn.config(state=tk.NORMAL if on else tk.DISABLED)
        self.view_btns["depth"].config(state=tk.NORMAL if self.solver is not None else tk.DISABLED)
        self.ply_btn.config(state=tk.NORMAL if self.solver is not None else tk.DISABLED)
        if self.calib:
            worst = max((abs(w["error_mm"]) for w in self.calib["walls"] if w.get("error_mm") is not None),
                        default=0.0)
            guess = "guessed" if self.calib["focal_source"].startswith("GUESSED") else "measured"
            self.calib_var.set(f"Calibrated {self.calib.get('made', '')}: {len(self.calib['walls'])} walls, "
                               f"worst {worst:.1f} mm off. Lens width {guess}. Depth is rough.")
        elif self.serial and lc.calibration_needs_refit(self.serial):
            self.calib_var.set("This scanner's calibration was made for a dot pattern (older version). Open the "
                               "wall test and press 'Load saved walls': it is refitted for the stripes, no new walls.")
        else:
            self.calib_var.set("Not calibrated yet: the scanner only gives pictures. "
                               "Press WALL TEST (five minutes, a tape measure and a plain wall).")
        self.light.refresh()

    # ------------------------------------------------------------------ misc
    def about(self):
        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME} v{APP_VERSION} (Built {APP_BUILD})\n{APP_WIP_NOTICE}\n\n"
            "A standalone app for the Creality CR-Scan Lizard: live infrared picture, automagic light "
            "settings, a wall-test depth calibration and recordings. Built on Scantastic and the Lizard "
            "engine from Who are yoUSB?.\n\n"
            f"lizard_camera {lc.MODULE_VERSION}, lizard_panel {lp.PANEL_VERSION}\n\n"
            f"{APP_COPYRIGHT}\n{APP_LICENCE_LINE}\n\n"
            "Not made by or affiliated with Creality. Source-available, non-commercial.",
            parent=self.root)

    def _open_folder(self, path):
        os.makedirs(path, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            self.status(f"{path} ({exc})")

    def on_close(self):
        self.closing = True
        self.stop_record()
        if self.lizard is not None:
            self.lizard.stop_all()
        if self.depth_thread is not None:
            self.depth_thread.join(timeout=3)
        for tid in self._timers.values():
            try:
                self.root.after_cancel(tid)
            except tk.TclError:
                pass
        if self.static is not None:
            self.static.stop()
        self.root.destroy()
        gc.collect()
        gc.enable()


def _install_errcode():
    """Optional: Matthew's ErrCode plug-in (error codes and a QR report), used when it is installed."""
    try:
        import errcode
        errcode.install(app=APP_NAME)
    except Exception:
        pass


def main():
    _install_errcode()
    if platform.system() == "Windows":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    try:
        root.iconphoto(True, tk.PhotoImage(file=os.path.join(SCRIPT_DIR, "icons", "icon-64.png")))
    except Exception:
        pass
    root.geometry("1160x760")
    root.minsize(900, 600)
    LizardApp(root, simulate="--simulate" in sys.argv[1:])
    root.mainloop()


if __name__ == "__main__":
    main()
