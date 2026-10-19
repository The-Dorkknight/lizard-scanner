#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# lizard_panel.py -- the Lizard's buttons and helper windows (tkinter), shared by
# Lizard Scanner and, later, Scantastic
# https://github.com/The-Dorkknight/lizard-scanner
#
# WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN. Only run against the simulated scanner so far.
#
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
#
"""
Everything here talks to a "host": the app window that owns the scanner. A host
needs only these (Scantastic can give them with a small adapter class):

    host.root          the Tk root window
    host.lizard        a lizard_camera.Lizard (None while the scanner is off)
    host.tuner         a lizard_camera.Tuner for camera A (None while off)
    host.serial        the scanner's serial ("" while unknown)
    host.is_sim        True when the simulated scanner is in use
    host.muted         colour for secondary text
    host.status(text)  show a line in the status bar
    host.calibration_changed(result, reference)   a new wall calibration was saved
    host.reports_dir   folder for test reports

Background work never touches Tk: it runs in a thread and hands its result back
through a queue that the window polls.
"""

import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np

import lizard_camera as lc
import lizard_diag as diag

def report_serial(host):
    """The scanner's serial for a saved report: hidden (CRSL*******) unless the host's 'hide serial' box is off."""
    serial = getattr(host, "serial", "") or "unknown"
    var = getattr(host, "hide_serial_var", None)
    try:
        hide = True if var is None else bool(var.get())
    except Exception:
        hide = True
    if not hide or serial in ("unknown", "SIMULATED"):
        return serial
    return serial[:4] + "*" * max(len(serial) - 4, 0)


PANEL_VERSION = "0.5.2-wip"
AMBER = "#d9902b"
GO_GREEN = "#2e9e5b"
CAL_BLUE = "#2f6db5"
STOP_RED = "#c0392b"
SUGGESTED_WALLS_MM = (180, 230, 280, 330, 380)   # spans the working range: errors grow past the last wall


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _mix(hex_colour, other, f):
    a = [int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * f) for x, y in zip(a, b))


class BigButton(tk.Canvas):
    """A large, colourful, rounded button (from Scantastic). .config(text=, subtext=, colour=, state=);
    set_pulse(True) makes it breathe."""

    def __init__(self, parent, text, command, colour, subtext="", height=72, font_px=17):
        try:
            bg = ttk.Style().lookup("TFrame", "background") or "#d9d9d9"
        except Exception:
            bg = "#d9d9d9"
        super().__init__(parent, width=120, height=height, highlightthickness=0, borderwidth=0, bg=bg,
                         cursor="hand2")
        self._text, self._subtext, self._colour, self._command = text, subtext, colour, command
        self._state = tk.NORMAL
        self._font_px = font_px
        self._hover = self._down = self._pulse = self._pulse_on = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, down=False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()

    def configure(self, cnf=None, **kw):
        if cnf is not None and not isinstance(cnf, dict):
            return super().configure(cnf)
        kw = {**(cnf or {}), **kw}
        changed = False
        for key in ("text", "subtext", "colour", "state"):
            if key in kw:
                setattr(self, "_" + key, kw.pop(key))
                changed = True
        if kw:
            super().configure(**kw)
        if changed:
            try:
                super().configure(cursor="arrow" if self._state == tk.DISABLED else "hand2")
            except tk.TclError:
                pass
            self._draw()
    config = configure

    def set_pulse(self, on):
        on = bool(on)
        if on == self._pulse:
            return
        self._pulse, self._pulse_on = on, False
        if on:
            self._tick()
        else:
            self._draw()

    def _tick(self):
        if not self._pulse:
            return
        self._pulse_on = not self._pulse_on
        self._draw()
        try:
            if self.winfo_exists():
                self.after(500, self._tick)
        except tk.TclError:
            pass

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, "_" + k, v)
        self._draw()

    def _press(self, _event):
        if self._state != tk.DISABLED:
            self._set(down=True)

    def _release(self, event):
        was_down = self._down
        self._set(down=False)
        if (was_down and self._state != tk.DISABLED
                and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()):
            self._command()

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
               x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.create_polygon(pts, smooth=True, **kw)

    def _draw(self):
        try:
            self.delete("all")
            w, h = max(self.winfo_width(), 60), max(self.winfo_height(), 30)
            off = self._state == tk.DISABLED
            base = "#6e727b" if off else self._colour
            if not off:
                if self._down:
                    base = _mix(base, "#000000", 0.18)
                elif self._pulse_on:
                    base = _mix(base, "#ffffff", 0.30)
                elif self._hover:
                    base = _mix(base, "#ffffff", 0.15)
            pad, r, lift = 3, min(16, h // 3), (0 if self._down else 3)
            self._round_rect(pad, pad + 4, w - pad, h - pad, r, fill=_mix(base, "#000000", 0.45), outline="")
            top, bottom = pad + (4 - lift), h - pad - lift
            self._round_rect(pad, top, w - pad, bottom, r, fill=base, outline="")
            self.create_line(pad + r, top + 3, w - pad - r, top + 3, fill=_mix(base, "#ffffff", 0.40), width=2)
            ink = "#c3c7ce" if off else "#ffffff"
            mid = (top + bottom) / 2.0
            fpx = self._font_px
            if self._subtext:
                self.create_text(w / 2, mid - fpx * 0.55, text=self._text, fill=ink,
                                 font=("Helvetica", fpx, "bold"))
                self.create_text(w / 2, mid + fpx * 0.85, text=self._subtext, fill=ink,
                                 font=("Helvetica", max(8, fpx // 2)), width=w - 16)
            else:
                self.create_text(w / 2, mid, text=self._text, fill=ink, font=("Helvetica", fpx, "bold"))
        except tk.TclError:
            pass


class Worker:
    """Runs a function off the Tk thread and calls done(result) back on it.

    Tk objects must only ever be freed on the Tk thread, or Tcl aborts the whole program. So the
    functions given here stay referenced until the Tk thread has run done(), and only then are let go,
    on the Tk thread. (The app also runs Python's cycle collector only on the Tk thread; see
    lizard_scanner.py.)"""

    def __init__(self, widget):
        self.widget = widget
        self.q = queue.Queue()
        self._keep = {}
        self._next = 0
        self._timer = None
        self._dead = False
        widget.bind("<Destroy>", self._destroyed, add="+")
        self._poll()

    def _destroyed(self, event):
        if event.widget is self.widget:
            self._dead = True
            if self._timer is not None:
                try:
                    self.widget.after_cancel(self._timer)
                except tk.TclError:
                    pass

    def _poll(self):
        try:
            while True:
                fn, args, token = self.q.get_nowait()
                try:
                    fn(*args)
                except Exception as exc:          # never let a callback kill the poll loop
                    print(f"lizard_panel: {exc}")
                finally:
                    if token is not None:
                        self._keep.pop(token, None)
        except queue.Empty:
            pass
        if self._dead:
            return
        try:
            self._timer = self.widget.after(80, self._poll)
        except tk.TclError:
            pass

    def call(self, fn, *args):
        """Ask the Tk thread to run fn(*args) soon (safe from any thread)."""
        self.q.put((fn, args, None))

    def run(self, work, done=None):
        token = self._next
        self._next += 1
        self._keep[token] = (work, done)
        q = self.q

        def go():
            try:
                res = work()
            except Exception as exc:
                res = exc
            q.put((done if done is not None else (lambda _r: None), (res,), token))
        t = threading.Thread(target=go, daemon=True)
        t.start()
        return t


# ---------------------------------------------------------------------------
# The light buttons
# ---------------------------------------------------------------------------

class LightPanel(ttk.LabelFrame):
    """AUTO-TUNE, three presets, Dimmer / Brighter, camera defaults. No sliders."""

    def __init__(self, parent, host, **kw):
        super().__init__(parent, text="Light (automagic)", padding=8, **kw)
        self.host = host
        self.worker = Worker(self)
        self.cancel = threading.Event()
        self.busy = False
        self.auto_btn = BigButton(self, "AUTO-TUNE", self.auto_tune, AMBER,
                                  subtext="finds the right light for what's in front", height=58, font_px=15)
        self.auto_btn.pack(fill=tk.X, pady=(0, 6))
        row = ttk.Frame(self)
        row.pack(fill=tk.X)
        self.preset_btns = []
        for i, (name, _step, why) in enumerate(lc.PRESETS):
            b = ttk.Button(row, text=name, command=lambda n=name: self.preset(n))
            b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 3, 0))
            row.columnconfigure(i, weight=1)
            _Tip(b, why)
            self.preset_btns.append(b)
        step_row = ttk.Frame(self)
        step_row.pack(fill=tk.X, pady=(5, 0))
        self.dim_btn = ttk.Button(step_row, text="◀  Dimmer", command=self.dimmer)
        self.dim_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.bright_btn = ttk.Button(step_row, text="Brighter  ▶", command=self.brighter)
        self.bright_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))
        self.level_var = tk.StringVar(value="Scanner off")
        ttk.Label(self, textvariable=self.level_var, foreground=host.muted, wraplength=260,
                  justify="left").pack(anchor="w", pady=(5, 0))
        self.meter = tk.Canvas(self, height=10, highlightthickness=0, borderwidth=0,
                               bg=ttk.Style().lookup("TFrame", "background") or "#d9d9d9")
        self.meter.pack(fill=tk.X, pady=(3, 0))
        self.long_var = tk.BooleanVar(value=False)
        self.long_check = ttk.Checkbutton(self, text="Allow longer flashes (brighter ring)", variable=self.long_var,
                                          command=self.toggle_long)
        self.long_check.pack(anchor="w", pady=(6, 0))
        _Tip(self.long_check, "The ring flashes for as long as the camera exposes. Off: never longer than CR Scan's "
                              "1 ms. On: up to 6 ms, the camera's own starting value. Resets when the app restarts.")
        self.default_btn = ttk.Button(self, text="Camera's own settings (long flashes)", command=self.defaults)
        self.default_btn.pack(fill=tk.X, pady=(4, 0))
        self.refresh()

    def toggle_long(self):
        t = getattr(self.host, "tuner", None)
        if self.long_var.get():
            if not messagebox.askyesno(
                    "Longer flashes",
                    "The white ring and the projector flash for as long as the camera exposes. Creality's app never "
                    "goes past 1 ms, and at 1 ms the ring is already very bright.\n\n"
                    "Longer flashes (up to 6 ms, the camera's own starting value) make the ring much brighter and the "
                    "LEDs warmer. Don't look into the ring, and use them only for dark objects.\n\n"
                    "Allow longer flashes until the app is closed?", parent=self.winfo_toplevel()):
                self.long_var.set(False)
                return
            if t is not None:
                t.allow_long = True
            self.host.status("Longer flashes allowed: Brighter now goes past 1 ms (up to 6 ms).")
        else:
            if t is not None:
                t.allow_long = False
                if t.step is None or (t.flash_ms() or 0) > lc.SAFE_EXPOSURE / 10:
                    self._do("Back to 1 ms flashes", lambda: t.apply_step(min(t.step if t.step is not None else 5,
                                                                              len(t.ladder()) - 1)))
            self.host.status("Flashes limited to 1 ms again.")
        self.refresh()

    def _tuner(self):
        t = getattr(self.host, "tuner", None)
        if t is None:
            self.host.status("Start the scanner first.")
        return t

    def _do(self, label, fn):
        t = self._tuner()
        if t is None or self.busy:
            return

        def done(err):
            self.busy = False
            self.refresh()
            if isinstance(err, Exception):
                self.host.status(f"{label}: {err}")
            else:
                self.host.status(f"{label}: {t.describe()}" + (f" (camera said: {err})" if err else ""))
        self.busy = True
        self.worker.run(fn, done)

    def preset(self, name):
        t = self._tuner()
        if t is not None:
            self._do(name, lambda: t.apply_preset(name))

    def brighter(self):
        t = self._tuner()
        if t is not None:
            self._do("Brighter", t.brighter)

    def dimmer(self):
        t = self._tuner()
        if t is not None:
            self._do("Dimmer", t.dimmer)

    def defaults(self):
        t = self._tuner()
        if t is not None and self.long_var.get():
            self._do("Camera's own settings (it chooses the flash length)", t.camera_defaults)

    def auto_tune(self):
        t = self._tuner()
        if t is None:
            return
        if self.busy:
            self.cancel.set()
            return
        self.cancel.clear()
        self.busy = True
        self.auto_btn.config(text="TUNING...", subtext="press again to stop")
        self.auto_btn.set_pulse(True)

        def work():
            return t.auto_tune(progress=lambda m: self.worker.call(self.host.status, m), cancel=self.cancel)

        def done(res):
            self.busy = False
            self.auto_btn.set_pulse(False)
            self.auto_btn.config(text="AUTO-TUNE", subtext="finds the right light for what's in front")
            self.refresh()
            self.host.status(str(res) if isinstance(res, Exception) else res[1])
        self.worker.run(work, done)

    def refresh(self):
        t = getattr(self.host, "tuner", None)
        on = t is not None
        if on and t.allow_long != bool(self.long_var.get()):
            t.allow_long = bool(self.long_var.get())     # a new scanner start gets the switch's setting
        for w in self.preset_btns + [self.dim_btn, self.bright_btn]:
            w.config(state=tk.NORMAL if on else tk.DISABLED)
        self.default_btn.config(state=tk.NORMAL if on and self.long_var.get() else tk.DISABLED)
        self.auto_btn.config(state=tk.NORMAL if on else tk.DISABLED)
        self.level_var.set(t.describe() if on else "Scanner off")
        self.meter.delete("all")
        w = max(self.meter.winfo_width(), 200)
        if on:
            ladder = t.ladder()
            n = len(ladder)
            for i in range(n):
                x0, x1 = i * w / n + 1, (i + 1) * w / n - 1
                lit = t.step is not None and i <= t.step
                long = ladder[i][0] > lc.SAFE_EXPOSURE
                colour = (STOP_RED if long else _mix(AMBER, "#ffffff", 0.2)) if lit else ("#6b3b36" if long else "#55504a")
                self.meter.create_rectangle(x0, 1, x1, 9, outline="", fill=colour)


class _Tip:
    """A small hover note."""

    def __init__(self, widget, text):
        self.widget, self.text, self.win = widget, text, None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def show(self, _e=None):
        if self.win or not self.text:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.win = tk.Toplevel(self.widget)
        self.win.wm_overrideredirect(True)
        self.win.geometry(f"+{x}+{y}")
        tk.Label(self.win, text=self.text, bg="#fff8dc", fg="#222", relief=tk.SOLID, borderwidth=1,
                 wraplength=260, justify="left", padx=6, pady=3).pack()

    def hide(self, _e=None):
        if self.win is not None:
            self.win.destroy()
            self.win = None


# ---------------------------------------------------------------------------
# Wall test (calibration)
# ---------------------------------------------------------------------------

class WallTestWindow(tk.Toplevel):
    """Guided wall calibration. Point the scanner square at a flat, plain wall; record it at measured
    distances. Every wall is saved the moment it is recorded, and from the third wall on the calibration
    is fitted and saved by itself. Everything lives in one folder, calibration/<serial>/, which
    ZIP CALIBRATION packs into one file to send."""

    def __init__(self, host):
        super().__init__(host.root)
        self.host = host
        self.title("Wall test: rough depth calibration")
        self.transient(host.root)
        self.worker = Worker(self)
        self.cal = lc.WallCalibration(getattr(host, "mode", lc.DEFAULT_MODE))
        self.busy = False
        self.tuned = False
        self.saved_fit = False
        body = ttk.Frame(self, padding=14)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="Wall test", font=("Helvetica", 16, "bold")).pack(anchor="w")
        steps = ("1.  A flat, plain, matt wall. Point the scanner straight at it.\n"
                 "2.  Measure to the wall from one fixed point on the scanner (the front glass is easiest; any "
                 "point works if it is the same every time). Type it in.\n"
                 "3.  RECORD THIS WALL, holding still for two seconds.\n"
                 "4.  Repeat at 18, 23, 28, 33 and 38 cm. From the third wall on, the calibration is "
                 "fitted and saved by itself after every wall.")
        ttk.Label(body, text=steps, wraplength=480, justify="left").pack(anchor="w", pady=(6, 8))
        if host.is_sim:
            ttk.Label(body, text="Simulated scanner: the pretend wall moves to whatever distance you type.",
                      foreground=AMBER).pack(anchor="w", pady=(0, 6))
        row = ttk.Frame(body)
        row.pack(fill=tk.X)
        ttk.Label(row, text="Scanner to wall (mm):").pack(side=tk.LEFT)
        self.dist_var = tk.StringVar(value=str(SUGGESTED_WALLS_MM[0]))
        e = ttk.Entry(row, textvariable=self.dist_var, width=8)
        e.pack(side=tk.LEFT, padx=6)
        e.bind("<Return>", lambda _e: self.record())
        self.rec_btn = BigButton(body, "RECORD THIS WALL", self.record, CAL_BLUE,
                                 subtext="keep the scanner still for two seconds", height=54, font_px=15)
        self.rec_btn.pack(fill=tk.X, pady=8)
        ttk.Label(body, text="Walls recorded (each saved straight away):").pack(anchor="w")
        self.walls_box = tk.Listbox(body, height=5, activestyle="none")
        self.walls_box.pack(fill=tk.X)
        btns = ttk.Frame(body)
        btns.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(btns, text="Remove selected", command=self.remove).pack(side=tk.LEFT)
        self.reload_btn = ttk.Button(btns, text="Load saved walls", command=self.load_saved)
        self.reload_btn.pack(side=tk.LEFT, padx=6)
        _Tip(self.reload_btn, "Bring back the walls saved for this scanner (also from an older version of the "
                             "app) and fit them again. No new walls needed.")
        self.width_on = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="I measured the picture's width on the wall (optional, sharper sideways sizes)",
                        variable=self.width_on, command=self._toggle_width).pack(anchor="w", pady=(8, 0))
        self.width_box = width = ttk.Frame(body, padding=(18, 2, 0, 0))
        ttk.Label(width, text="Two strips of tape at the picture's left and right edges; measure between them. "
                              "Without this the lens is guessed from Creality's spec (depth is measured either way).",
                  wraplength=460, foreground=host.muted, justify="left").pack(anchor="w")
        wr = ttk.Frame(width)
        wr.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(wr, text="Picture width (mm):").pack(side=tk.LEFT)
        self.width_var = tk.StringVar(value="")
        ttk.Entry(wr, textvariable=self.width_var, width=7).pack(side=tk.LEFT, padx=(4, 10))
        ttk.Label(wr, text="at distance (mm):").pack(side=tk.LEFT)
        self.width_at_var = tk.StringVar(value="")
        ttk.Entry(wr, textvariable=self.width_at_var, width=7).pack(side=tk.LEFT, padx=4)
        self._width_anchor = ttk.Frame(body)
        self._width_anchor.pack(fill=tk.X)
        self.fit_btn = BigButton(body, "FIT AND SAVE AGAIN", self.fit, GO_GREEN,
                                 subtext="happens by itself from the third wall", height=46, font_px=14)
        self.fit_btn.pack(fill=tk.X, pady=(10, 4))
        zrow = ttk.Frame(body)
        zrow.pack(fill=tk.X, pady=(2, 4))
        self.zip_btn = ttk.Button(zrow, text="ZIP CALIBRATION (one file to send)", command=self.zip)
        self.zip_btn.pack(side=tk.LEFT)
        ttk.Button(zrow, text="Open the folder", command=self.open_folder).pack(side=tk.LEFT, padx=6)
        self.result_var = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.result_var, wraplength=480, justify="left").pack(anchor="w")
        self.folder_var = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.folder_var, wraplength=480, foreground=host.muted,
                  justify="left").pack(anchor="w", pady=(4, 0))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._refresh()
        self.geometry("+%d+%d" % (host.root.winfo_rootx() + 60, host.root.winfo_rooty() + 40))

    # helpers --------------------------------------------------------------
    def _serial(self):
        return getattr(self.host, "serial", "") or "unknown"

    def _folder(self):
        return lc.calibration_folder(self._serial())

    def _shown_folder(self):
        f = self._folder()
        return f.replace(self._serial(), report_serial(self.host)) if self._serial() != "unknown" else f

    def _toggle_width(self):
        if self.width_on.get():
            self.width_box.pack(fill=tk.X, before=self._width_anchor)
        else:
            self.width_box.pack_forget()
            self.width_var.set("")
            self.width_at_var.set("")

    def _stream(self):
        lz = getattr(self.host, "lizard", None)
        return lz.stream("A") if lz is not None else None

    def _refresh(self):
        self.walls_box.delete(0, tk.END)
        fitted = {}
        if self.cal.result:
            fitted = {round(x["distance_mm"]): x for x in self.cal.result["walls"]}
        for d, pat in self.cal.walls:
            x = fitted.get(round(d))
            if x is None:
                how = "recorded"
            elif x["reference"]:
                how = "reference wall"
            elif x["error_mm"] is None:
                how = "NOT matched: record again"
            else:
                how = f"fit {x['error_mm']:+.1f} mm, spread {x['spread_mm']:.1f} mm, {x['matched'] * 100:.0f}% matched"
            pics = len(self.cal.jpegs.get(d, []))
            self.walls_box.insert(tk.END, f"  ✓  {d:.0f} mm   {how}" + (f"   ({pics} pictures kept)" if pics else ""))
        n = len(self.cal.walls)
        done = [int(round(d)) for d, _p in self.cal.walls]
        nxt = next((d for d in SUGGESTED_WALLS_MM if d not in done), None)
        if nxt is not None and not self.busy:
            self.dist_var.set(str(nxt))
        self.fit_btn.config(state=tk.NORMAL if n >= 2 and not self.busy else tk.DISABLED,
                            subtext=("saved" if self.saved_fit else "ready") if n >= lc.MIN_WALLS
                            else f"{n} of {lc.MIN_WALLS} walls")
        self.rec_btn.config(state=tk.DISABLED if self.busy else tk.NORMAL)
        has = os.path.isdir(self._folder()) and bool(os.listdir(self._folder()))
        self.zip_btn.config(state=tk.NORMAL if has and not self.busy else tk.DISABLED)
        self.folder_var.set(f"Everything for this calibration is in {self._shown_folder()}" if has else "")

    # recording ------------------------------------------------------------
    def record(self):
        if self.busy:
            return
        try:
            d = float(self.dist_var.get().replace(",", "."))
        except ValueError:
            messagebox.showwarning("Wall test", "Type the distance in millimetres, e.g. 250.", parent=self)
            return
        lo, hi = lc.WORKING_RANGE_MM
        if not lo * 0.8 <= d <= hi * 1.2:
            if not messagebox.askyesno("Wall test", f"{d:.0f} mm is outside the Lizard's working range "
                                                    f"({lo:.0f} to {hi:.0f} mm). Record anyway?", parent=self):
                return
        s = self._stream()
        if s is None:
            messagebox.showwarning("Wall test", "Start the scanner (camera A) first.", parent=self)
            return
        if self.host.is_sim:
            self.host.lizard.sim_scene.wall_mm = d + float(np.random.default_rng().normal(0, 0.7))
            self.host.lizard.sim_scene.ball = None
        self.busy = True
        self._refresh()
        tuner = self.host.tuner
        first = not self.tuned
        folder = self._folder()
        width, at = self._width()

        def work():
            if first and tuner is not None:      # automagic: get the light right before the first wall
                tuner.auto_tune()
            f = s.latest
            idx = f.index if f else -1
            for _ in range(3):                    # let the scanner settle
                f = s.wait_frame(idx)
                idx = f.index if f else idx
            lit, dark, jpegs = [], [], []
            t_end = time.time() + 8.0
            while len(lit) < lc.WALL_FRAMES and time.time() < t_end:
                f = s.wait_frame(idx)
                if f is None:
                    break
                idx = f.index
                (lit if f.lit else dark).append(f.grey)
                if f.lit and f.jpeg and len(jpegs) < 3:
                    jpegs.append(f.jpeg)
            if len(lit) < 2:
                raise RuntimeError("no pictures arrived from camera A")
            pulsing = s.pulse.state()[0]
            top, sat = lc.Tuner.measure(lit[-1])
            darks = dark[:len(lit)] if pulsing and len(dark) >= len(lit) else None
            self.cal.add_wall(d, lit, darks, jpegs=jpegs)
            self.cal.save_walls(folder)
            fit = None
            if len(self.cal.walls) >= lc.MIN_WALLS:
                try:
                    fit = self.cal.fit(width, at)
                    self.cal.save(self._serial(), folder)
                except ValueError as exc:
                    fit = exc
            return top, sat, fit

        def done(res):
            self.busy = False
            self.tuned = True
            if isinstance(res, Exception):
                messagebox.showerror("Wall test", f"That wall was not recorded: {res}", parent=self)
                self._refresh()
                return
            top, sat, fit = res
            note = []
            if sat > lc.TUNE_MAX_SATURATED * 3:
                note.append("parts of the picture were blown out (press Dimmer)")
            if top < 60:
                note.append("the picture was dark (press Brighter or AUTO-TUNE)")
            msg = f"Wall at {d:.0f} mm recorded and saved." + (" Check: " + "; ".join(note) + "." if note else "")
            self._show_fit(fit)
            self.host.status(msg + (" Calibration fitted and saved." if isinstance(fit, dict) else ""))
            self._refresh()
        self.worker.run(work, done)

    def _width(self):
        try:
            if self.width_var.get().strip():
                return (float(self.width_var.get().replace(",", ".")),
                        float(self.width_at_var.get().replace(",", ".")))
        except ValueError:
            pass
        return None, None

    def _show_fit(self, fit):
        if fit is None:
            self.result_var.set("")
        elif isinstance(fit, Exception):
            self.saved_fit = False
            self.result_var.set(f"Not fitted yet: {fit}")
        else:
            self.saved_fit = True
            self.result_var.set("\n".join(self.cal.summary_lines()) + "\nSaved: the Depth view uses it now.")
            self.host.calibration_changed(fit, self.cal.reference)

    def remove(self):
        sel = self.walls_box.curselection()
        if sel and not self.busy:
            d = self.cal.walls[sel[0]][0]
            self.cal.remove_wall(d)
            self.cal.result = None
            self.cal.save_walls(self._folder())
            self._refresh()
            if len(self.cal.walls) >= lc.MIN_WALLS:
                self.fit()

    def load_saved(self):
        if self.busy:
            return
        folder = self._folder()
        try:
            cal = lc.WallCalibration.from_folder(folder)
        except (OSError, KeyError, ValueError):
            messagebox.showinfo("Wall test", f"No saved walls for this scanner in {self._shown_folder()}.",
                                parent=self)
            return
        cal.mode = getattr(self.host, "mode", cal.mode)
        self.cal = cal
        self.tuned = True
        self.fit()

    def fit(self):
        if self.busy:
            return
        if self.width_var.get().strip() and self._width() == (None, None):
            messagebox.showwarning("Wall test", "The picture width and its distance must both be numbers "
                                                "(or leave both empty).", parent=self)
            return
        width, at = self._width()
        folder = self._folder()
        self.busy = True
        self.result_var.set("Fitting... (a few seconds)")
        self._refresh()

        def work():
            self.cal.save_walls(folder)
            try:
                res = self.cal.fit(width, at)
            except ValueError as exc:
                return exc
            self.cal.save(self._serial(), folder)
            return res

        def done(res):
            self.busy = False
            self._show_fit(res)
            if isinstance(res, dict):
                self.host.status("Wall calibration saved: the Depth view is on.")
            self._refresh()
        self.worker.run(work, done)

    # one folder, one zip ----------------------------------------------------
    def zip(self):
        folder = self._folder()
        if not os.path.isdir(folder):
            return
        var = getattr(self.host, "hide_serial_var", None)
        hide = True if var is None else bool(var.get())
        try:
            path = lc.zip_calibration(folder, hide_serial=hide)
        except OSError as exc:
            messagebox.showerror("Wall test", f"Could not make the zip: {exc}", parent=self)
            return
        shown = path if not hide else path.replace(self._serial(), report_serial(self.host))
        self.host.status(f"Calibration zipped: {shown}")
        messagebox.showinfo("Wall test", f"Everything for this calibration is in one file:\n\n{shown}"
                            + ("\n\nThe scanner's serial is hidden inside it." if hide else ""), parent=self)

    def open_folder(self):
        folder = self._folder()
        os.makedirs(folder, exist_ok=True)
        opener = getattr(self.host, "_open_folder", None)
        if opener is not None:
            opener(folder)
        else:
            messagebox.showinfo("Wall test", folder, parent=self)

    def close(self):
        if self.busy:
            if not messagebox.askyesno("Wall test", "Still working. Close anyway? (Walls recorded so far are "
                                                    "saved.)", parent=self):
                return
        elif self.cal.walls and not self.saved_fit and len(self.cal.walls) >= 2:
            if not messagebox.askyesno("Wall test", "The walls are saved, but there is no fitted calibration yet "
                                                    f"({len(self.cal.walls)} walls; three are needed). Close anyway? "
                                                    "'Load saved walls' brings them back.", parent=self):
                return
        self.destroy()


class EyeTestWindow(tk.Toplevel):
    """Steps one setting of one camera while you watch the scanner's front, then asks what you saw.
    kind="exposure": exposure 0.1 ms to 1 ms (to 6 ms only with longer flashes allowed), gain held.
    kind="gain": gain 16 to 128 with the flash held at 1 ms. Together they show what sets each light's
    brightness. The light watched is the white ring (camera B) or the red infrared glow (camera A).
    Saves a report."""

    HOLD_S = 2.5
    LIGHTS = [("White ring (camera B)", "B"), ("Red infrared glow (camera A)", "A")]

    def __init__(self, host, kind="exposure"):
        super().__init__(host.root)
        self.host, self.kind = host, kind
        self.title("Light test" if kind == "exposure" else "Gain test")
        self.transient(host.root)
        self.worker = Worker(self)
        self.cancel = threading.Event()
        self.rows = []
        self.cam = "B"
        body = ttk.Frame(self, padding=14)
        body.pack(fill=tk.BOTH, expand=True)
        question = ("Does the flash length set the light's brightness?" if kind == "exposure"
                    else "Does gain change the light's brightness?")
        ttk.Label(body, text=question, font=("Helvetica", 15, "bold"), wraplength=440).pack(anchor="w")
        row = ttk.Frame(body)
        row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(row, text="Watch:").pack(side=tk.LEFT)
        self.light_var = tk.StringVar(value="B")
        for label, cam in self.LIGHTS:
            ttk.Radiobutton(row, text=label, value=cam, variable=self.light_var).pack(side=tk.LEFT, padx=(8, 0))
        what = ("The app steps that camera's exposure (how long its light flashes) from 0.1 ms up to 1 ms"
                + (" and then to 6 ms" if self._long() else "") + ", holding each for a moment."
                if kind == "exposure" else
                "The app holds that camera's flash at 1 ms and steps its gain (picture amplification) from 16 to 128.")
        ttk.Label(body, text="Look at the scanner's front, not the screen, from the side (not into the ring). "
                             + what + " Camera B is started for the test if it is not running.",
                  wraplength=440, justify="left").pack(anchor="w", pady=(6, 10))
        self.big = tk.StringVar(value="Ready")
        ttk.Label(body, textvariable=self.big, font=("Helvetica", 28, "bold")).pack(pady=6)
        self.go = BigButton(body, "START", self.start, AMBER, height=50, font_px=15)
        self.go.pack(fill=tk.X, pady=6)
        self.ask = ttk.Frame(body)
        ttk.Label(self.ask, text="Did the light's brightness change as the numbers went up?").pack(anchor="w")
        for text in ("Yes, clearly", "A little", "No change", "Couldn't tell"):
            ttk.Button(self.ask, text=text, command=lambda tx=text: self.answer(tx)).pack(side=tk.LEFT, padx=(0, 4),
                                                                                         pady=4)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _long(self):
        t = getattr(self.host, "tuner", None)
        return bool(t is not None and t.allow_long)

    def start(self):
        lz = self.host.lizard
        if lz is None or lz.stream("A") is None:
            messagebox.showwarning(self.title(), "Start the scanner first.", parent=self)
            return
        cam = self.cam = self.light_var.get()
        if not lz.connected(cam):
            messagebox.showwarning(self.title(), f"Camera {cam} is not there.", parent=self)
            return
        self.go.config(state=tk.DISABLED)
        mode = getattr(self.host, "mode", lc.DEFAULT_MODE)
        started = lz.stream(cam) is None

        def work():
            if started:
                lz.start(cam, mode)                 # starts at a 1 ms flash
                time.sleep(0.8)
            try:
                ctrls = lz.controls(cam)
                exp = lc._control(ctrls, "exposure_time_absolute", "exposure_absolute")
                exp_name = exp["name"] if exp else "exposure_time_absolute"
                if self.kind == "exposure":
                    control, values, fixed = exp_name, diag.exposure_test_values(self._long()), {}
                    label = lambda v: f"flash {v / 10:.1f} ms"
                else:
                    control, values, fixed = "gain", diag.gain_test_values(ctrls), {exp_name: lc.SAFE_EXPOSURE}
                    label = lambda v: f"gain {v}"
                return diag.sweep(lz, control, values, hold_s=self.HOLD_S, cam=cam, fixed=fixed, cancel=self.cancel,
                                  progress=lambda v: self.worker.call(self.big.set, label(v)))
            finally:
                if started:
                    lz.stop(cam)

        def done(rows):
            if isinstance(rows, Exception):
                messagebox.showerror(self.title(), str(rows), parent=self)
                self.go.config(state=tk.NORMAL)
                return
            self.rows = rows
            self.big.set("Done")
            self.ask.pack(fill=tk.X, pady=(6, 0))
        self.worker.run(work, done)

    def answer(self, text):
        verdict = diag.EYE_VERDICTS[self.kind][text]
        os.makedirs(self.host.reports_dir, exist_ok=True)
        path = os.path.join(self.host.reports_dir, time.strftime(f"{self.kind}_test_%Y%m%d_%H%M%S.txt"))
        unit = "exposure_ms" if self.kind == "exposure" else "gain"
        with open(path, "w") as f:
            f.write(f"Lizard {self.kind} test {time.strftime('%Y-%m-%d %H:%M')} (lizard_camera {lc.MODULE_VERSION})\n")
            f.write(f"Scanner: {report_serial(self.host)}\n")
            f.write(f"Light watched: {dict((c, n) for n, c in self.LIGHTS)[self.cam]}\n")
            f.write(f"Seen by eye: did its brightness change? {text}\n")
            f.write(f"Conclusion: {verdict}\n")
            f.write(f"Picture: {diag.sweep_summary(self.rows)}\n\n")
            f.write(f"{unit:>11s}  lit_frame_level  dark_frame_level\n")
            for v, lit, dark in self.rows:
                f.write(f"{(v / 10 if self.kind == 'exposure' else v):11g} {lit:16.1f} {dark:17.1f}\n")
            f.write("\nThe picture always gets brighter with exposure and gain; only your eye can tell what the "
                    "ring itself did.\n")
        if hasattr(self.host, "eye_results"):
            self.host.eye_results[self.kind] = diag.Result(self.title(), None, f"{text}: {verdict}", rows=self.rows)
        self.host.status(f"{self.title()} saved ({os.path.basename(path)}). {verdict}")
        self.destroy()

    def close(self):
        self.cancel.set()
        self.destroy()


class SensorMapWindow(tk.Toplevel):
    """Which camera does what. Runs camera A alone, camera B alone, then both, for a few seconds each,
    measures every picture (rate, dots, colour, pulse) and asks which light you saw at the front.
    Puts camera A back afterwards."""

    PHASES = [("A", ("A",), "Camera A alone"), ("B", ("B",), "Camera B alone"), ("AB", ("A", "B"), "Both together")]
    ANSWERS = ("White ring", "Red glow", "Both", "Neither", "Couldn't tell")
    SECONDS = 3.0

    def __init__(self, host, on_done=None):
        super().__init__(host.root)
        self.host, self.on_done = host, on_done
        self.title("Sensor map")
        self.transient(host.root)
        self.worker = Worker(self)
        self.phases, self.answers, self.i = {}, {}, 0
        body = ttk.Frame(self, padding=14)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="Which camera does what?", font=("Helvetica", 15, "bold")).pack(anchor="w")
        ttk.Label(body, text="Point the scanner at a plain wall 20 to 30 cm away and watch its FRONT (from the side, "
                             "not into the ring). The app runs camera A alone, camera B alone, then both, "
                             f"{self.SECONDS:.0f} seconds each, and asks which light you saw each time.",
                  wraplength=460, justify="left").pack(anchor="w", pady=(6, 10))
        self.big = tk.StringVar(value="Ready")
        ttk.Label(body, textvariable=self.big, font=("Helvetica", 22, "bold")).pack(pady=6)
        self.go = BigButton(body, "START", self.next_phase, AMBER, height=50, font_px=15)
        self.go.pack(fill=tk.X, pady=6)
        self.ask = ttk.Frame(body)
        self.ask_label = tk.StringVar(value="")
        ttk.Label(self.ask, textvariable=self.ask_label).pack(anchor="w")
        row = ttk.Frame(self.ask)
        row.pack(fill=tk.X)
        for text in self.ANSWERS:
            ttk.Button(row, text=text, command=lambda tx=text: self.answer(tx)).pack(side=tk.LEFT, padx=(0, 4), pady=4)
        self.result_var = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.result_var, wraplength=460, justify="left").pack(anchor="w", pady=(6, 0))
        self.protocol("WM_DELETE_WINDOW", self.close)

    def next_phase(self):
        lz = self.host.lizard
        if lz is None:
            messagebox.showwarning("Sensor map", "Start the scanner first.", parent=self)
            return
        if self.i >= len(self.PHASES):
            return
        key, cams, label = self.PHASES[self.i]
        if any(not lz.connected(c) for c in cams):
            self.phases[key] = {}
            self.answers[key] = "camera missing"
            self.i += 1
            return self.next_phase() if self.i < len(self.PHASES) else self.finish()
        self.go.config(state=tk.DISABLED)
        self.ask.pack_forget()
        self.big.set(f"{label}: watch the front")
        mode = getattr(self.host, "mode", lc.DEFAULT_MODE)

        def done(res):
            if isinstance(res, Exception):
                self.result_var.set(f"{label} failed: {res}")
                res = {}
            self.phases[key] = res
            self.big.set(f"{label}: done")
            self.ask_label.set(f"Which light did you see while {label.lower()} ran?")
            self.ask.pack(fill=tk.X, pady=(6, 0))
        self.worker.run(lambda: diag.measure_cameras(lz, cams, mode, self.SECONDS), done)

    def answer(self, text):
        key = self.PHASES[self.i][0]
        self.answers[key] = text
        self.i += 1
        self.ask.pack_forget()
        if self.i < len(self.PHASES):
            self.next_phase()
        else:
            self.finish()

    def finish(self):
        self._restore()
        r = diag.sensor_map_result(self.phases, self.answers)
        self.big.set("Finished")
        self.result_var.set("\n".join(r["details"]["lines"]) + "\n\n" + r["summary"])
        if hasattr(self.host, "eye_results"):
            self.host.eye_results["sensor_map"] = r
        os.makedirs(self.host.reports_dir, exist_ok=True)
        path = os.path.join(self.host.reports_dir, time.strftime("sensor_map_%Y%m%d_%H%M%S.txt"))
        with open(path, "w") as f:
            f.write(f"Lizard sensor map {time.strftime('%Y-%m-%d %H:%M')}, scanner {report_serial(self.host)}\n")
            f.write("\n".join(r["details"]["lines"]) + "\n\n" + r["summary"] + "\n")
        self.host.status(f"Sensor map saved ({os.path.basename(path)}). {r['summary']}")
        if self.on_done:
            self.on_done(r)

    def _restore(self):
        lz = self.host.lizard
        if lz is None:
            return
        try:
            for c in ("A", "B"):
                lz.stop(c)
            lz.start("A", getattr(self.host, "mode", lc.DEFAULT_MODE))
            t = getattr(self.host, "tuner", None)
            if t is not None and t.step is not None:
                t.apply_step(t.step)
        except Exception as exc:
            self.host.status(f"Camera A did not restart: {exc}")

    def close(self):
        if self.i and self.i < len(self.PHASES):
            self._restore()
        self.destroy()


# Kept for older callers: the light test is the exposure eye test.
def LightTestWindow(host):
    return EyeTestWindow(host, "exposure")


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

class DiagnosticsWindow(tk.Toplevel):
    """Every check in one place: run the quick ones together, the others one by one, then save one zip
    to attach to a GitHub issue or keep."""

    def __init__(self, host):
        super().__init__(host.root)
        self.host = host
        self.title("Diagnostics")
        self.transient(host.root)
        self.worker = Worker(self)
        self.results = {}
        self.rows = {}
        self.busy = False
        body = ttk.Frame(self, padding=14)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="Diagnostics", font=("Helvetica", 16, "bold")).pack(anchor="w")
        ttk.Label(body, text="Point the scanner at a plain wall or surface 20 to 30 cm away, then press RUN QUICK "
                             "CHECKS (about 10 seconds). The others need you, and say so. Nothing here changes the "
                             "scanner for good: settings are put back after each check.",
                  wraplength=560, justify="left").pack(anchor="w", pady=(4, 8))
        self.quick_btn = BigButton(body, "RUN QUICK CHECKS", self.run_quick, GO_GREEN,
                                   subtext="USB, video, settings, serial, projector, depth speed", height=56, font_px=15)
        self.quick_btn.pack(fill=tk.X, pady=(0, 8))
        grid = ttk.Frame(body)
        grid.pack(fill=tk.X)
        checks = [
            ("usb", "USB connection", self._one(lambda: diag.usb_check(self.host.lizard))),
            ("video_a", "Camera A video", self._one(lambda: diag.video_check(self.host.lizard, "A", self.host.mode))),
            ("settings", "Video modes and settings", self._one(lambda: diag.formats_and_settings(self.host.lizard))),
            ("serial", "Serial number", self._one(lambda: diag.serial_check(self.host.lizard))),
            ("projector", "Projector and room light", self._one(lambda: diag.projector_check(self.host.lizard))),
            ("depth_speed", "Depth speed", self._one(lambda: diag.depth_speed_check(self.host.lizard,
                                                                                     self.host.solver))),
            ("sensor_map", "Sensor map: which camera does what", self.run_sensor_map),
            ("camera_b", "What is camera B? (camera A pauses)",
             self._one(lambda: diag.camera_b_check(self.host.lizard, self.host.mode))),
            ("flat_wall", "Flat wall check (needs WALL TEST)", self.run_flat_wall),
            ("light", "Light test: watch the ring", lambda key: EyeTestWindow(self.host, "exposure")),
            ("gain", "Gain test: watch the ring", lambda key: EyeTestWindow(self.host, "gain")),
        ]
        for i, (key, title, fn) in enumerate(checks):
            ttk.Button(grid, text="Run", width=5, command=lambda k=key, f=fn: f(k)).grid(row=i, column=0, pady=2,
                                                                                         sticky="w")
            ttk.Label(grid, text=title).grid(row=i, column=1, sticky="w", padx=(8, 8))
            mark = tk.Label(grid, text="", width=9, fg="white", bg="#5a5a5a", font=("TkDefaultFont", 9, "bold"))
            mark.grid(row=i, column=2, sticky="w")
            var = tk.StringVar(value="")
            ttk.Label(grid, textvariable=var, wraplength=330, justify="left", foreground=host.muted).grid(
                row=i, column=3, sticky="w", padx=(8, 0))
            self.rows[key] = (mark, var)
        tape_row = ttk.Frame(body)
        tape_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(tape_row, text="For the flat wall check, scanner to wall by tape (mm, optional):").pack(side=tk.LEFT)
        self.tape_var = tk.StringVar(value="")
        ttk.Entry(tape_row, textvariable=self.tape_var, width=7).pack(side=tk.LEFT, padx=6)
        save_row = ttk.Frame(body)
        save_row.pack(fill=tk.X, pady=(10, 0))
        self.hide_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(save_row, text="Hide the scanner's serial in the report", variable=self.hide_var).pack(
            side=tk.LEFT)
        ttk.Button(save_row, text="Save diagnostics report (.zip)...", command=self.save).pack(side=tk.RIGHT)
        self.note_var = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.note_var, wraplength=560, justify="left").pack(anchor="w", pady=(8, 0))
        self.geometry("+%d+%d" % (host.root.winfo_rootx() + 40, host.root.winfo_rooty() + 30))
        for key, r in getattr(host, "eye_results", {}).items():
            row = {"exposure": "light"}.get(key, key)          # the light test is filed as "exposure"
            if row in self.rows:
                self.results[row] = r
                self._show(row, r)

    def _show(self, key, r):
        mark, var = self.rows[key]
        colour = {True: "#2e7d4f", False: "#b03a2e", None: "#6b6b6b"}[r["ok"]]
        mark.config(text=r.mark, bg=colour)
        var.set(r["summary"] + (f"  {r['advice']}" if r["advice"] else ""))

    def _one(self, fn):
        def go(key):
            if self.busy:
                return
            if self.host.lizard is None:
                self.note_var.set("Start the scanner first.")
                return
            self.busy = True
            self.rows[key][0].config(text="...", bg="#8a6d1e")

            def done(r):
                self.busy = False
                if isinstance(r, Exception):
                    r = diag.Result(key, False, f"The check itself failed: {r}")
                self.results[key] = r
                self._show(key, r)
            self.worker.run(fn, done)
        return go

    def run_sensor_map(self, key):
        if self.host.lizard is None:
            self.note_var.set("Start the scanner first.")
            return

        def got(r):
            self.results[key] = r
            self._show(key, r)
        SensorMapWindow(self.host, on_done=got)

    def run_flat_wall(self, key):
        tape = None
        if self.tape_var.get().strip():
            try:
                tape = float(self.tape_var.get().replace(",", "."))
            except ValueError:
                self.note_var.set("The tape distance must be a number in millimetres (or empty).")
                return
        self._one(lambda: diag.flat_wall_check(self.host.lizard, self.host.solver, self.host.calib, tape))(key)

    def run_quick(self):
        if self.busy:
            return
        if self.host.lizard is None:
            self.note_var.set("Start the scanner first.")
            return
        self.busy = True
        self.quick_btn.config(text="CHECKING...", state=tk.DISABLED)
        for k in diag.QUICK:
            self.rows[k][0].config(text="...", bg="#8a6d1e")

        def done(res):
            self.busy = False
            self.quick_btn.config(text="RUN QUICK CHECKS", state=tk.NORMAL)
            if isinstance(res, Exception):
                self.note_var.set(f"The checks failed: {res}")
                return
            for k, r in res.items():
                self.results[k] = r
                self._show(k, r)
            good, bad, skipped = diag.summary_counts(res)
            self.note_var.set(f"{good} fine, {bad} with a problem, {skipped} skipped. Save the report to keep it.")
        lz, mode, solver = self.host.lizard, self.host.mode, self.host.solver
        self.worker.run(lambda: diag.run_quick_checks(
            lz, mode, solver, progress=lambda n: self.worker.call(self.note_var.set, f"Checking: {n}...")), done)

    def save(self):
        if not self.results:
            self.note_var.set("Run some checks first.")
            return
        extra = []
        serial = getattr(self.host, "serial", "") or ""
        if serial:
            extra.append(os.path.join(lc.calibration_folder(serial), "calibration_report.txt"))
            extra.append(os.path.join(lc.calibration_folder(serial), "calibration.json"))
        rep = self.host.reports_dir
        if os.path.isdir(rep):
            extra += [os.path.join(rep, f) for f in sorted(os.listdir(rep))
                      if f.endswith(".txt") and f.split("_")[0] in ("exposure", "gain", "light")][-6:]
        info = diag.system_info(getattr(self.host, "app_version", ""))
        try:
            path = diag.save_report(self.results, rep, info, extra, hide_serial=self.hide_var.get())
        except Exception as exc:
            self.note_var.set(f"Could not save: {exc}")
            return
        self.note_var.set(f"Saved {path}")
        self.host.status(f"Diagnostics report saved: {path}")


class SettingsWindow(tk.Toplevel):
    """Every control the camera offers, for anyone who wants the raw numbers. The automagic buttons
    cover what CR Scan uses (exposure and gain)."""

    def __init__(self, host, cam="A"):
        super().__init__(host.root)
        self.host, self.cam = host, cam
        self.title(f"Every camera setting (camera {cam})")
        self.transient(host.root)
        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="Advanced. The buttons in the main window already handle exposure and gain; "
                             "'brightness' here is picture processing only and does not change the lights.",
                  wraplength=460, foreground=host.muted, justify="left").grid(row=0, column=0, columnspan=4,
                                                                             sticky="w", pady=(0, 8))
        ctrls = host.lizard.controls(cam) if host.lizard else []
        if not ctrls:
            ttk.Label(body, text="No settings available (start the scanner first).").grid(row=1, column=0)
        self.vars = {}
        for i, c in enumerate(ctrls, start=1):
            ttk.Label(body, text=c["name"].replace("_", " ")).grid(row=i, column=0, sticky="w", padx=(0, 8))
            var = tk.StringVar(value=str(c.get("value", "")))
            self.vars[c["name"]] = var
            if c.get("menu"):
                labels = [f"{k}: {v}" for k, v in sorted(c["menu"].items())]
                box = ttk.Combobox(body, textvariable=var, values=labels, width=24, state="readonly")
                cur = c.get("value")
                if cur in c["menu"]:
                    var.set(f"{cur}: {c['menu'][cur]}")
                box.grid(row=i, column=1, sticky="w")
            else:
                ttk.Spinbox(body, textvariable=var, from_=c.get("min", -99999), to=c.get("max", 99999),
                            increment=c.get("step", 1) or 1, width=10).grid(row=i, column=1, sticky="w")
            rng = f"{c.get('min', '?')} .. {c.get('max', '?')}, default {c.get('default', '?')}"
            ttk.Label(body, text=rng, foreground=host.muted).grid(row=i, column=2, sticky="w", padx=6)
            ttk.Button(body, text="Set", width=5,
                       command=lambda n=c["name"]: self.apply(n)).grid(row=i, column=3, pady=1)

    def apply(self, name):
        raw = self.vars[name].get().split(":")[0].strip()
        try:
            value = int(raw)
        except ValueError:
            self.host.status(f"{name}: not a whole number")
            return
        tuner = getattr(self.host, "tuner", None)
        allow_long = bool(tuner is not None and tuner.allow_long)
        if "exposure" in name and "auto" not in name:
            if value > lc.LONG_EXPOSURE_MAX:
                if not messagebox.askyesno(
                        "Very long flashes", f"Exposure {value} makes the ring flash for {value / 10:g} ms, longer than "
                        f"the camera's own starting value ({lc.LONG_EXPOSURE_MAX / 10:g} ms) and far longer than "
                        "Creality's app ever uses (1 ms). The ring will be very bright and the LEDs warmer. "
                        "Set it anyway?", parent=self):
                    return
            elif value > lc.SAFE_EXPOSURE and not allow_long:
                if not messagebox.askyesno(
                        "Longer flashes", f"Exposure {value} makes the ring flash for {value / 10:g} ms; Creality's "
                        "app never goes past 1 ms, which is already very bright. Set it anyway?", parent=self):
                    return
        if name in ("auto_exposure", "exposure_auto") and value != 1 and not allow_long:
            if not messagebox.askyesno(
                    "Automatic exposure", "With automatic exposure the camera chooses how long the ring flashes, "
                    "often much longer than 1 ms. Turn it on anyway?", parent=self):
                return
        err = self.host.lizard.set(self.cam, name, value)
        if self.cam == "A" and self.host.tuner is not None and not err and ("exposure" in name or name == "gain"):
            self.host.tuner.custom = True
        self.host.status(f"{name} = {value}" + (f": {err}" if err else ""))
