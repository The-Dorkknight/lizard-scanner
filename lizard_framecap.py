#!/usr/bin/env python3
"""
lizard_framecap.py - frame capture and data logger for scanner cameras (built for the CR-Scan Lizard).

Saves every captured picture with the moment it was taken and the camera settings in force at that moment
(exposure, gain, auto-exposure and every other control), plus a log of setting changes and your own markers.
Standard library only, so it drops into Who are yoUSB? (single file, no dependencies) and Lizard Scanner alike.
WORK IN PROGRESS: tested with simulated cameras only.

Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
Licensed under the PolyForm Noncommercial License 1.0.0. No warranty, no liability.

One capture = one folder:
    capture_20261016_174500_<label>/
        session.json      what was captured: app, start/stop time, cameras, frame counts (serial hidden by default)
        frames.csv        one row per saved frame: time, camera, file, size, brightness, lit, fps, and the settings
        events.jsonl      one JSON line per event: settings snapshots, setting changes, markers, stream start/stop
        frames/A_000001.jpg, frames/B_000001.png ...

Use:
    log = FrameLogger("captures", label="ring test", app="Lizard Scanner 0.3.1")
    log.camera_info("A", node="/dev/video2", vid=0xbcdc, pid=0x000a, mode="mjpg 1280x720")
    log.snapshot_settings("A", {"exposure_time_absolute": 10, "gain": 65, "auto_exposure": 1})
    log.add_frame("A", jpeg_bytes, kind="jpeg", level=42.0, lit=True, fps=9.8)   # or kind="grey", width, height
    log.setting_changed("A", "gain", 40)                                      # later frames carry gain=40
    log.mark("covered camera A with a finger")
    log.close()

Extra columns (0.1.1): FrameLogger(..., extra_columns=("detail", "dots")) adds them to the end of frames.csv;
pass add_frame(..., extra={"detail": 3.2, "dots": 0.07}). Without them the file is exactly as in 0.1.0.
"""
import csv, datetime, json, os, re, socket, struct, threading, time, zlib

__version__ = "0.1.1-wip"   # 0.1.1: optional extra_columns (Lizard Scanner); files unchanged otherwise

# Settings that get their own column in frames.csv (every setting is in events.jsonl anyway)
KEY_SETTINGS = ("exposure_time_absolute", "exposure_absolute", "gain", "auto_exposure", "exposure_auto",
                "brightness", "contrast", "gamma", "power_line_frequency")
CSV_COLUMNS = ["time_iso", "t_s", "camera", "index", "file", "kind", "width", "height", "bytes", "level", "lit",
               "fps", "exposure_100us", "exposure_ms", "gain", "auto_exposure", "settings_rev", "marker"]


def png_grey(width, height, pix, level=1):
    """8-bit grey PNG from raw bytes (row-major, width*height)."""
    raw = b"".join(b"\x00" + bytes(pix[y * width:(y + 1) * width]) for y in range(height))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, level)) + chunk(b"IEND", b""))


def jpeg_size(b):
    """(width, height) from a JPEG's start-of-frame marker, or None."""
    i = 2
    while i + 9 < len(b) and b[i] == 0xFF:
        m, ln = b[i + 1], struct.unpack_from(">H", b, i + 2)[0]
        if m in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack_from(">HH", b, i + 5)
            return w, h
        i += 2 + ln
    return None


def grey_level(pix, step=97):
    """Mean brightness 0-255 of a grey picture, sampled (cheap enough for every frame)."""
    s = pix[::step]
    return sum(s) / len(s) if len(s) else 0.0


def _slug(text, n=32):
    return re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-")[:n]


def _hide(value):
    """CRSL1234567 -> CRSL*******; anything else long enough keeps its first 4 characters."""
    v = str(value)
    return v[:4] + "*" * max(len(v) - 4, 0) if len(v) > 4 else "*" * len(v)


class FrameLogger:
    """Thread-safe: cameras may call add_frame from their own reader threads."""

    def __init__(self, root, label="", app="", every=1, max_frames=None, save_images=True, hide_serial=True,
                 clock=time.time, extra_columns=()):
        self.clock = clock
        self.t0 = clock()
        stamp = datetime.datetime.fromtimestamp(self.t0).strftime("%Y%m%d_%H%M%S")
        self.folder = os.path.join(root, f"capture_{stamp}" + (f"_{_slug(label)}" if _slug(label) else ""))
        n = 1
        while os.path.exists(self.folder):  # two captures in the same second
            n += 1
            self.folder = os.path.join(root, f"capture_{stamp}_{n}" + (f"_{_slug(label)}" if _slug(label) else ""))
        os.makedirs(os.path.join(self.folder, "frames"))
        self.every, self.max_frames = max(1, int(every)), max_frames
        self.save_images, self.hide_serial = save_images, hide_serial
        self.extra_columns = tuple(c for c in extra_columns if c not in CSV_COLUMNS)
        self.lock = threading.Lock()
        self.settings = {}        # camera -> {name: value}
        self.rev = 0              # bumps on every settings change, stamped on frames
        self.seen = {}            # camera -> frames offered
        self.saved = {}           # camera -> frames saved
        self.pending_marker = ""
        self.closed = False
        self.session = dict(app=app, module=f"lizard_framecap {__version__}", label=label,
                            started=datetime.datetime.fromtimestamp(self.t0).isoformat(timespec="milliseconds"),
                            host=socket.gethostname() if not hide_serial else "", cameras={}, every=self.every,
                            max_frames=max_frames, save_images=save_images, serial_hidden=hide_serial,
                            extra_columns=list(self.extra_columns))
        self._csv_f = open(os.path.join(self.folder, "frames.csv"), "w", newline="")
        self._csv = csv.writer(self._csv_f)
        self._csv.writerow(CSV_COLUMNS + list(self.extra_columns))
        self._ev = open(os.path.join(self.folder, "events.jsonl"), "w")
        self._write_session()
        self.event("capture_started", label=label)

    # ---- metadata
    def camera_info(self, cam, **info):
        """Describe a camera once (node, vid, pid, mode, serial ...). The serial is masked unless hide_serial=False."""
        if "serial" in info and info["serial"] and self.hide_serial:
            info["serial"] = _hide(info["serial"])
        for k in ("vid", "pid"):
            if isinstance(info.get(k), int):
                info[k] = f"{info[k]:04x}"
        with self.lock:
            self.session["cameras"].setdefault(cam, {}).update(info)
            self._write_session()
        self.event("camera_info", camera=cam, **info)

    def snapshot_settings(self, cam, values):
        """Record every setting of a camera at once: a dict {name: value} or v4l2-style [{'name':..,'value':..}]."""
        if isinstance(values, (list, tuple)):
            values = {c["name"]: c.get("value") for c in values if "name" in c}
        with self.lock:
            self.settings[cam] = dict(values)
            self.rev += 1
        self.event("settings_snapshot", camera=cam, settings=values, rev=self.rev)

    def setting_changed(self, cam, name, value, ok=True, error=""):
        """Call after changing a setting; later frames carry the new value. Refused changes are logged only."""
        if ok:
            with self.lock:
                self.settings.setdefault(cam, {})[name] = value
                self.rev += 1
        self.event("setting_changed", camera=cam, name=name, value=value, ok=ok, error=error, rev=self.rev)

    def mark(self, note):
        """A note from you ("covered the IR camera"); also stamped on the next saved frame of every camera."""
        with self.lock:
            self.pending_marker = note
        self.event("marker", note=note)

    def event(self, kind, **data):
        if self.closed:
            return
        now = self.clock()
        rec = dict(time_iso=datetime.datetime.fromtimestamp(now).isoformat(timespec="milliseconds"),
                   t_s=round(now - self.t0, 4), event=kind, **data)
        with self.lock:
            self._ev.write(json.dumps(rec, default=str) + "\n")
            self._ev.flush()

    # ---- frames
    def add_frame(self, cam, data, kind="jpeg", width=None, height=None, level=None, lit=None, fps=None, t=None,
                  extra=None):
        """Offer one frame. kind: 'jpeg' (camera's own bytes, saved as-is) or 'grey' (8-bit, width*height bytes,
        saved as PNG). Returns the saved file name, or None when skipped (every-Nth, max reached, closed)."""
        if self.closed:
            return None
        now = self.clock() if t is None else t
        with self.lock:
            n = self.seen[cam] = self.seen.get(cam, 0) + 1
            if (n - 1) % self.every:
                return None
            if self.max_frames is not None and sum(self.saved.values()) >= self.max_frames:
                return None
            idx = self.saved[cam] = self.saved.get(cam, 0) + 1
            st = dict(self.settings.get(cam, {}))
            rev, marker, self.pending_marker = self.rev, self.pending_marker, ""
        if kind == "jpeg":
            dims = jpeg_size(data) or (width, height)
            width, height = dims
            name = f"{cam}_{idx:06d}.jpg"
            blob = bytes(data)
        else:
            if width is None or height is None:
                raise ValueError("grey frames need width and height")
            if level is None:
                level = grey_level(data)
            name = f"{cam}_{idx:06d}.png"
            blob = png_grey(width, height, data) if self.save_images else b""
        if self.save_images:
            with open(os.path.join(self.folder, "frames", name), "wb") as f:
                f.write(blob)
        exposure = next((st[k] for k in ("exposure_time_absolute", "exposure_absolute") if k in st), "")
        auto = next((st[k] for k in ("auto_exposure", "exposure_auto") if k in st), "")
        row = [datetime.datetime.fromtimestamp(now).isoformat(timespec="milliseconds"), round(now - self.t0, 4),
               cam, idx, f"frames/{name}" if self.save_images else "", kind, width or "", height or "",
               len(data), "" if level is None else round(float(level), 2),
               "" if lit is None else int(bool(lit)), "" if fps is None else round(float(fps), 2),
               exposure, "" if exposure == "" else round(float(exposure) / 10, 2), st.get("gain", ""), auto,
               rev, marker]
        if self.extra_columns:
            extra = extra or {}
            row += ["" if extra.get(c) is None else extra.get(c) for c in self.extra_columns]
        with self.lock:
            self._csv.writerow(row)
            self._csv_f.flush()
        return name

    def count(self, cam=None):
        with self.lock:
            return self.saved.get(cam, 0) if cam else sum(self.saved.values())

    def full(self):
        return self.max_frames is not None and self.count() >= self.max_frames

    # ---- end
    def close(self):
        if self.closed:
            return self.folder
        self.event("capture_stopped", frames=dict(self.saved), offered=dict(self.seen))
        with self.lock:
            self.closed = True
            now = self.clock()
            self.session.update(stopped=datetime.datetime.fromtimestamp(now).isoformat(timespec="milliseconds"),
                                seconds=round(now - self.t0, 2), frames=dict(self.saved), offered=dict(self.seen),
                                last_settings=self.settings)
            self._write_session()
            self._csv_f.close()
            self._ev.close()
        return self.folder

    def _write_session(self):
        with open(os.path.join(self.folder, "session.json"), "w") as f:
            json.dump(self.session, f, indent=2, default=str)


def read_capture(folder):
    """(session dict, frame rows as dicts, events as dicts) of a saved capture."""
    with open(os.path.join(folder, "session.json")) as f:
        session = json.load(f)
    with open(os.path.join(folder, "frames.csv"), newline="") as f:
        rows = list(csv.DictReader(f))
    with open(os.path.join(folder, "events.jsonl")) as f:
        events = [json.loads(line) for line in f if line.strip()]
    return session, rows, events


def summarise(folder):
    """Short text report: frames per camera, rate, brightness per setting combination."""
    session, rows, events = read_capture(folder)
    out = [f"Capture {os.path.basename(folder)}: {len(rows)} frames over {session.get('seconds', '?')} s"]
    groups = {}
    for r in rows:
        key = (r["camera"], r["exposure_ms"], r["gain"])
        groups.setdefault(key, []).append(r)
    for (cam, exp_ms, gain), rs in sorted(groups.items()):
        lv = [float(r["level"]) for r in rs if r["level"]]
        # MJPEG frames carry no brightness here; their size grows with light and detail, so show that instead
        what = (f"brightness {sum(lv) / len(lv):.1f}" if lv
                else f"average {sum(int(r['bytes']) for r in rs) / len(rs) / 1000:.0f} KB per frame")
        out.append(f"  camera {cam}  exposure {exp_ms or '?'} ms  gain {gain or '?'}: {len(rs)} frames, {what}")
    marks = [e for e in events if e["event"] == "marker"]
    for e in marks:
        out.append(f"  marker at +{e['t_s']:.1f}s: {e['note']}")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    print(summarise(sys.argv[1]) if len(sys.argv) > 1 else __doc__)
