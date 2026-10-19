#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# lizard_recording.py -- joins a running Lizard (lizard_camera) to the frame capture and data logger
# (lizard_framecap, from Who are yoUSB? 0.5.0)
# https://github.com/The-Dorkknight/lizard-scanner
#
# WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN. Only run against the simulated scanner so far.
#
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
#
"""
    run = CaptureRun(lizard, "captures", label="ring test", app="Lizard Scanner 0.4.0", mode="mjpg")
    run.mark("covered camera A")          # notes land on the next frame and in events.jsonl
    folder = run.stop()

Every frame of every running camera is saved with the moment it arrived and the settings in force
(exposure, gain, auto-exposure and every other control), plus, in extra columns: the stream's own
picture number, fine detail, how much of the picture shows the stripe pattern, and the light ladder rung.
Every setting change made through Lizard.set (light buttons, AUTO-TUNE, tests, diagnostics) is logged
and stamped on the frames that follow. Cameras started later (camera B in the A + B view) join in.

snapshot=True saves one frame from each running camera and stops by itself.
"""

import threading
import time

import lizard_camera as lc
import lizard_framecap as fc

EXTRA_COLUMNS = ("stream_index", "detail", "pattern", "rung", "mode", "sharpness")


def dot_share(grey):
    """Share of the picture showing the projector's stripe pattern (lizard_diag.pattern_score); 0 on failure."""
    try:
        import lizard_diag
        return round(lizard_diag.pattern_score(grey), 4)
    except Exception:
        return None


def sharpness(grey):
    """Mean squared difference between neighbouring pixels: higher = sharper focus / finer pattern."""
    import numpy as np
    g = np.asarray(grey[::2], np.float32)
    return round(float((np.diff(g, axis=1) ** 2).mean()), 2)


class CaptureRun:
    def __init__(self, lizard, root, label="", app="", mode=lc.DEFAULT_MODE, every=1, snapshot=False,
                 hide_serial=True, rung=lambda: None, on_done=None, snapshot_timeout=4.0):
        self.lizard, self.mode, self.snapshot = lizard, mode, snapshot
        self.rung = rung
        self.on_done = on_done
        self.log = fc.FrameLogger(root, label=label or ("snapshot" if snapshot else ""), app=app,
                                  every=1 if snapshot else every, hide_serial=hide_serial,
                                  extra_columns=EXTRA_COLUMNS)
        self._lock = threading.Lock()
        self._hooks = {}                  # cam -> (stream, hook)
        self._snap_claimed = set()       # cameras whose snapshot frame is being saved
        self._snap_done = set()          # ...and saved
        self.closed = False
        self.folder = self.log.folder
        serial = ""
        try:
            serial = lizard.serial()
        except Exception:
            pass
        self.log.event("scanner", serial=fc._hide(serial) if (hide_serial and serial) else serial,
                       backend=lizard.kind(), module=f"lizard_camera {lc.MODULE_VERSION}")
        # a snapshot waits for every camera running now, attached or not yet
        self._expect = {c for c, s in lizard.streams.items() if s.running()}
        lizard.on_set.append(self._setting_changed)
        lizard.on_start.append(self.attach)
        for cam, s in list(lizard.streams.items()):
            if s.running():
                self.attach(cam, s)
        if snapshot:
            t = threading.Timer(snapshot_timeout, self._snapshot_timeout)
            t.daemon = True
            t.start()

    # -------------------------------------------------------------- wiring
    def attach(self, cam, stream):
        """Start saving this camera's frames (called for every camera started while the run is on)."""
        with self._lock:
            if self.closed or cam in self._hooks and self._hooks[cam][0] is stream:
                return
            old = self._hooks.pop(cam, None)
        if old is not None and old[1] in old[0].listeners:
            old[0].listeners.remove(old[1])
        dev = None
        if self.lizard.kind() == "v4l2":
            for letter, vid, pid in lc.LIZARD_CAMS:
                if letter == cam:
                    dev = (vid, pid)
        w, h = lc.MODES[stream.mode]["size"]
        self.log.camera_info(cam, node=self.lizard.nodes.get(cam), mode=stream.mode, size=f"{w}x{h}",
                             backend=getattr(stream, "backend", ""),
                             **({"vid": dev[0], "pid": dev[1]} if dev else {}))
        try:
            self.log.snapshot_settings(cam, stream.controls())
        except Exception as exc:
            self.log.event("settings_unreadable", camera=cam, error=str(exc))
        self.log.event("stream_joined", camera=cam, mode=stream.mode)

        def hook(f, cam=cam, s=stream):
            self._frame(cam, s, f)
        stream.listeners.append(hook)
        with self._lock:
            self._hooks[cam] = (stream, hook)

    def _frame(self, cam, s, f):
        if self.closed:
            return
        if self.snapshot:
            with self._lock:
                if cam in self._snap_claimed:
                    return
                self._snap_claimed.add(cam)
        extra = dict(stream_index=f.index, detail=round(f.detail, 3), pattern=dot_share(f.grey),
                     rung=self.rung(), mode=s.mode, sharpness=sharpness(f.grey))
        if f.jpeg:
            self.log.add_frame(cam, f.jpeg, "jpeg", level=f.level, lit=f.lit, fps=s.fps(), t=f.t, extra=extra)
        else:
            h, w = f.grey.shape
            self.log.add_frame(cam, f.grey.tobytes(), "grey", w, h, level=f.level, lit=f.lit, fps=s.fps(), t=f.t,
                               extra=extra)
        if self.snapshot:
            with self._lock:
                self._snap_done.add(cam)
                done = self._expect <= self._snap_done
            if done:
                threading.Thread(target=self.stop, daemon=True).start()

    def _snapshot_timeout(self):
        if not self.closed:
            self.log.event("snapshot_timeout", saved=sorted(self._snap_done))
            self.stop()

    def _setting_changed(self, cam, name, value, error):
        self.log.setting_changed(cam, name, value, ok=not error, error=error or "")

    # -------------------------------------------------------------- use
    def mark(self, note):
        self.log.mark(note)

    def event(self, kind, **data):
        self.log.event(kind, **data)

    def count(self, cam=None):
        return self.log.count(cam)

    def stop(self):
        with self._lock:
            if self.closed:
                return self.folder
            self.closed = True
            hooks, self._hooks = self._hooks, {}
        for st, hook in hooks.values():
            if hook in st.listeners:
                st.listeners.remove(hook)
        for lst, fn in ((self.lizard.on_set, self._setting_changed), (self.lizard.on_start, self.attach)):
            if fn in lst:
                lst.remove(fn)
        folder = self.log.close()
        if self.on_done is not None:
            try:
                self.on_done(self)
            except Exception:
                pass
        return folder

    def summary(self):
        return fc.summarise(self.folder)
