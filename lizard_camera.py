#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# lizard_camera.py -- the Creality CR-Scan Lizard as a depth camera, for the
# Lizard Scanner app and (later) for Scantastic
# https://github.com/The-Dorkknight/lizard-scanner
#
# WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN. The camera control comes from
# Who are yoUSB? 0.4.0-wip, where it ran on a real Lizard (both cameras start
# and stop, clear pictures, lights follow the video). Everything new here
# (auto-tune, wall calibration, depth) has only been run against the built-in
# simulated scanner. Treat the first run on the real scanner as a debugging session.
#
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
#
"""
One module, four layers, each usable on its own:

1. Cameras: find the Lizard's two USB cameras (A = bcdc:000a, B = bcdc:000b)
   and stream them. Linux uses v4l2-ctl (nothing to install but v4l-utils);
   Windows and macOS use OpenCV (experimental, untested); "sim" is a
   simulated Lizard (dot projector, wall or ball) and "replay" plays back a
   recorded session. Frames come with a lit / dark flag, because the Lizard's
   lights pulse every other frame.

2. Settings: exposure and gain are the only things CR Scan changes, so they
   are wrapped as a ladder of light levels with named presets, an auto-tune
   and Brighter / Dimmer steps. No sliders needed.

3. Calibration: the wall test. The IR camera sees a fixed dot pattern from
   the projector, like a Kinect v1. Point the scanner at a flat wall at three
   or more measured distances; the pattern slides sideways as the distance
   changes, and the slide follows K * (1/(Z + c) - 1/(Zref + c)). Fitting K
   and c gives a rough depth calibration. ROUGH: no lens model, no
   per-pixel correction, focal length guessed unless measured.

4. Depth: block matching of a live picture against the reference wall turns
   every lit frame into a depth map. LizardCamera wraps all of it with the
   same shape as Scantastic's cameras:
       cam.start(log=print); color, depth, intrinsic = cam.get_frame(); cam.stop()
   plus depth_scale, guide_range, near_m / far_m, family ("Lizard"), serial.
   Without a calibration get_frame() returns (picture, None, None): pictures
   only, no depth.

Needs numpy and Pillow. OpenCV only for the Windows/macOS camera path.
"""

import collections
import csv
import glob
import io
import json
import math
import os
import re
import subprocess
import sys
import threading
import time

import numpy as np

try:
    from PIL import Image
except ImportError as _exc:                       # pragma: no cover
    raise ImportError("Pillow is required (the Lizard Scanner app installs it)") from _exc

MODULE_VERSION = "0.5.2-wip"
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
# Where wall calibrations live. LIZARD_CALIBRATION_DIR lets another app (Scantastic) share Lizard Scanner's.
CALIBRATION_DIR = os.environ.get("LIZARD_CALIBRATION_DIR") or os.path.join(MODULE_DIR, "calibration")

LIZARD_VID = 0xBCDC
LIZARD_CAMS = (("A", LIZARD_VID, 0x000A), ("B", LIZARD_VID, 0x000B))
SYSFS = "/sys/bus/usb/devices"
SYS_ROOT = "/sys"

# Creality: working distance 150-400 mm, single capture area 200 x 100 mm.
WORKING_RANGE_MM = (150.0, 400.0)
SPEC_CAPTURE_WIDTH_MM = 200.0
SPEC_CAPTURE_AT_MM = 300.0     # ASSUMPTION: Creality does not say at which distance the 200 mm applies

# Video modes. "work" is the size pictures are decoded to for everything the app
# does (preview, tuning, depth): half of 1280x720, cheap enough for a 7th-gen i3.
MODES = {"mjpg": dict(fourcc="MJPG", size=(1280, 720), work=(640, 360), fps=10),
         "yuyv": dict(fourcc="YUYV", size=(640, 480), work=(640, 480), fps=5)}
DEFAULT_MODE = "mjpg"           # what CR Scan uses


class CameraError(RuntimeError):
    """The scanner could not be found or started."""


# ===========================================================================
# 1. Cameras
# ===========================================================================

def _rd(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def find_devices(vid=None, pid=None):
    """USB devices from /sys (Linux), as dicts."""
    devs = []
    for p in sorted(glob.glob(SYSFS + "/*")):
        name = os.path.basename(p)
        if ":" in name:
            continue
        v = _rd(p + "/idVendor")
        if not v:
            continue
        v, pr = int(v, 16), int(_rd(p + "/idProduct", "0"), 16)
        if (vid is not None and v != vid) or (pid is not None and pr != pid):
            continue
        devs.append(dict(path=p, name=name, vid=v, pid=pr, product=_rd(p + "/product"),
                         serial=_rd(p + "/serial"), speed=_rd(p + "/speed")))
    return devs


def _class_nodes(dev, cls):
    root = os.path.realpath(dev["path"]) + "/"
    return ["/dev/" + os.path.basename(n) for n in sorted(glob.glob(f"{SYS_ROOT}/class/{cls}/*"))
            if os.path.realpath(n + "/device").startswith(root)]


def capture_node(dev):
    """The /dev/video node that delivers pictures (UVC also makes a metadata node with a higher number)."""
    nodes = _class_nodes(dev, "video4linux")
    return sorted(nodes, key=lambda n: int(re.sub(r"\D", "", n) or 0))[0] if nodes else None


def lizard_nodes():
    """{'A': '/dev/videoN' or None, 'B': ...} on Linux; {} elsewhere."""
    if not sys.platform.startswith("linux"):
        return {}
    out = {}
    for letter, vid, pid in LIZARD_CAMS:
        devs = find_devices(vid, pid)
        out[letter] = capture_node(devs[0]) if devs else None
    return out


def detect():
    """Short descriptions of the Lizard cameras plugged in now; () when none; None when that cannot be
    checked cheaply (not Linux). Never raises."""
    try:
        if not sys.platform.startswith("linux"):
            return None
        return tuple(f"CR-Scan Lizard camera {k} ({v})" for k, v in sorted(lizard_nodes().items()) if v)
    except Exception:
        return None


_CTRL_LINE = re.compile(r"^\s*(\w+)\s+0x([0-9a-f]+)\s+\((\w+)\)\s*:\s*(.*)$")


def v4l2_controls(node, run=subprocess.run):
    """Every control the camera offers: [dict(name, id, type, min, max, step, default, value, flags, menu)]."""
    try:
        r = run(["v4l2-ctl", "-d", node, "--list-ctrls-menus"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    ctrls = []
    for line in r.stdout.splitlines():
        m = _CTRL_LINE.match(line)
        if m:
            c = dict(name=m.group(1), id=int(m.group(2), 16), type=m.group(3), menu={}, flags="")
            for k, v in re.findall(r"(\w+)=(\S+)", m.group(4)):
                if k == "flags":
                    c["flags"] = v
                else:
                    try:
                        c[k] = int(v)
                    except ValueError:
                        pass
            ctrls.append(c)
        elif ctrls and re.match(r"^\s+(\d+):\s*(.+)$", line):
            n, label = re.match(r"^\s+(\d+):\s*(.+)$", line).groups()
            ctrls[-1]["menu"][int(n)] = label.strip()
    return ctrls


def v4l2_set(node, name, value, run=subprocess.run):
    """Set one control; '' on success or the error text."""
    try:
        r = run(["v4l2-ctl", "-d", node, "-c", f"{name}={int(value)}"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return (r.stderr or r.stdout).strip() if r.returncode else ""


def _ioc(direction, typ, nr, size):
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


def _xu_query(fd, unit, selector, query, data):
    """One UVC extension-unit request through the kernel's uvcvideo driver (UVCIOC_CTRL_QUERY)."""
    import ctypes
    import fcntl

    class Q(ctypes.Structure):
        _fields_ = [("unit", ctypes.c_uint8), ("selector", ctypes.c_uint8), ("query", ctypes.c_uint8),
                    ("size", ctypes.c_uint16), ("data", ctypes.c_void_p)]
    buf = ctypes.create_string_buffer(bytes(data), len(data))
    q = Q(unit, selector, query, len(data), ctypes.cast(buf, ctypes.c_void_p))
    fcntl.ioctl(fd, _ioc(3, "u", 0x21, ctypes.sizeof(Q)), q)
    return buf.raw


def read_lizard_serial(node):
    """The request CR Scan sends at connect: write 02 c2 00 fe 14 00 00 00 to extension unit 4 selector 10,
    then read selector 11 in 8-byte pieces. Returns the text found (e.g. 'CRSL0000000')."""
    fd = os.open(node, os.O_RDWR)
    try:
        _xu_query(fd, 4, 10, 0x01, bytes.fromhex("02c200fe14000000"))
        blob = b"".join(_xu_query(fd, 4, 11, 0x81, bytes(8)) for _ in range(3))
    finally:
        os.close(fd)
    texts = [m.decode() for m in re.findall(rb"[\x20-\x7e]{4,}", blob)]
    return ", ".join(texts) or blob.hex(" ")


def picture_detail(grey):
    """How much fine detail a picture has: mean difference between neighbouring pixels along every
    fourth row. The projector's dots add a lot of it while changing the overall brightness only a
    little, so this tells lit pictures from dark ones far better than brightness does."""
    rows = np.asarray(grey[::4], np.int16)
    return float(np.abs(np.diff(rows, axis=1)).mean())


class Frame:
    """One picture. grey: uint8 numpy array at the mode's working size. jpeg: the camera's own bytes
    (MJPEG) or None. level: mean brightness; detail: fine detail (see picture_detail); size: JPEG bytes
    (0 without MJPEG). lit: True / False once the pulse pattern is known, None before."""
    __slots__ = ("t", "index", "grey", "jpeg", "level", "detail", "size", "lit", "cam")

    def __init__(self, t, index, grey, jpeg=None, cam="A"):
        self.t, self.index, self.grey, self.jpeg, self.cam = t, index, grey, jpeg, cam
        self.level = float(grey[::8, ::8].mean()) if grey is not None else 0.0
        self.detail = picture_detail(grey) if grey is not None else 0.0
        self.size = len(jpeg) if jpeg else 0
        self.lit = None


def decode_jpeg(jpeg, work_size):
    """Grey picture at work_size from MJPEG bytes. Pillow's draft mode decodes at 1/2, 1/4 or 1/8 scale
    straight from the compressed data, which is much cheaper than decoding full size."""
    img = Image.open(io.BytesIO(jpeg))
    img.draft("L", work_size)
    img = img.convert("L")
    if img.size != tuple(work_size):
        img = img.resize(work_size, Image.BILINEAR)
    return np.asarray(img, dtype=np.uint8)


PULSE_SIGNALS = ("detail", "size", "level")   # in order of preference when equally clear
PULSE_MIN_SEPARATION = 4.0      # bright and dark groups this many spreads apart...
PULSE_MIN_RATIO = 1.10          # ...and the bright group at least 10% above the dark one...
PULSE_MIN_GROUP = 0.15          # ...each group at least 15% of the pictures...
PULSE_MIN_SWITCHES = 0.25       # ...switching between them in at least a quarter of the steps


def _two_groups(values):
    """Split values into a high and a low group at the threshold that separates them best.
    Returns (threshold, mean high, mean low, separation in spreads, share high)."""
    v = np.sort(np.asarray(values, np.float64))
    n = len(v)
    best = None
    for i in range(1, n):
        lo, hi = v[:i], v[i:]
        within = np.sqrt((lo.var() * len(lo) + hi.var() * len(hi)) / n) + 1e-6 + 0.002 * abs(v.mean())
        sep = (hi.mean() - lo.mean()) / within
        if best is None or sep > best[3]:
            best = ((v[i - 1] + v[i]) / 2, hi.mean(), lo.mean(), sep, len(hi) / n)
    return best


class PulseTracker:
    """Decides which pictures are lit. The Lizard's projector pulses, so lit pictures have more of the
    dot pattern than dark ones. It looks at three signals per picture: fine detail, JPEG size (detail
    compresses badly, so lit JPEGs are bigger) and mean brightness, and uses whichever separates the
    pictures into two clear groups. It does not assume the lights alternate every other picture: any
    pattern that switches often enough counts (1 on / 1 off, 1 on / 2 off, 2 on / 2 off...).
    Until it has decided, every picture counts as lit."""

    def __init__(self, history=20):
        self.history = history
        self.series = {k: collections.deque(maxlen=history) for k in PULSE_SIGNALS}
        self._cache = None

    @staticmethod
    def features(frame_or_level):
        if isinstance(frame_or_level, (int, float, np.floating)):
            return {"level": float(frame_or_level)}
        f = frame_or_level
        out = {"level": f.level, "detail": f.detail}
        if f.size:
            out["size"] = float(f.size)
        return out

    def add(self, frame_or_level):
        """Add a Frame (or, for simple use, just a brightness level)."""
        for k, v in self.features(frame_or_level).items():
            self.series[k].append(v)
        self._cache = None

    def info(self):
        """dict(pulsing, signal, ratio, separation, on_share, switches, period, pattern, threshold)."""
        if self._cache is not None:
            return self._cache
        best = None
        for k in PULSE_SIGNALS:
            vals = list(self.series[k])
            if len(vals) < 6:
                continue
            thr, hi, lo, sep, share = _two_groups(vals)
            on = [v > thr for v in vals]
            switches = sum(1 for a, b in zip(on, on[1:]) if a != b) / (len(on) - 1)
            ratio = hi / max(lo, 1e-9)
            ok = (sep >= PULSE_MIN_SEPARATION and ratio >= PULSE_MIN_RATIO
                  and PULSE_MIN_GROUP <= share <= 1 - PULSE_MIN_GROUP and switches >= PULSE_MIN_SWITCHES)
            cand = dict(pulsing=bool(ok), signal=k, ratio=float(ratio), separation=float(sep), on_share=float(share),
                        switches=float(switches), threshold=float(thr), on=on)
            if best is None or (ok, sep) > (best["pulsing"], best["separation"]):
                best = cand
        if best is None:
            self._cache = dict(pulsing=False, signal="", ratio=1.0, separation=0.0, on_share=0.0, switches=0.0,
                               threshold=None, period=None, pattern="not enough pictures yet")
            return self._cache
        on = best.pop("on")
        best["period"] = _period(on) if best["pulsing"] else None
        if best["pulsing"]:
            p = best["period"]
            lit = round(best["on_share"] * (p or 0))
            best["pattern"] = (("every other picture" if p == 2 and lit == 1 else
                                f"{lit} lit in every {p} pictures") if p else "irregular")
        else:
            best["pattern"] = "no pulse seen"
        self._cache = best
        return best

    def state(self):
        """(is pulsing, how often it switches, bright / dark ratio) -- kept for older callers."""
        i = self.info()
        return i["pulsing"], i["switches"], i["ratio"]

    def value(self, frame):
        """The deciding signal's value for one picture (higher = more likely lit)."""
        sig = self.info().get("signal") or "level"
        return self.features(frame).get(sig, frame.level if hasattr(frame, "level") else 0.0)

    def classify(self, frame_or_level):
        i = self.info()
        if not i["pulsing"]:
            return True
        v = self.features(frame_or_level).get(i["signal"])
        return True if v is None else v > i["threshold"]


def _period(on):
    """Smallest repeat length (2 to 6) of an on / off sequence, or None."""
    a = np.asarray(on, float)
    a = a - a.mean()
    if not a.any():
        return None
    best, best_r = None, 0.5
    for p in range(2, 7):
        if len(a) <= p + 3:
            break
        r = float((a[:-p] * a[p:]).sum() / np.sqrt((a[:-p] ** 2).sum() * (a[p:] ** 2).sum() + 1e-9))
        if r > best_r + 0.05:
            best, best_r = p, r
            if r > 0.9:
                break
    return best


class _StreamBase:
    """Common part of every camera backend: a reader thread that hands frames to on_frame()."""

    def __init__(self, cam="A", mode=DEFAULT_MODE):
        self.cam, self.mode = cam, mode
        self.work = MODES[mode]["work"]
        self.error = ""
        self.frames = 0
        self.times = collections.deque(maxlen=30)
        self.pulse = PulseTracker()
        self.latest = None            # newest Frame
        self.latest_lit = None        # newest lit Frame
        self.latest_dark = None
        self.listeners = []           # callables(frame), called on the reader thread
        self._cond = threading.Condition()
        self._thread = None
        self._stop = threading.Event()

    # backends implement: _open(), _read_frame() -> (grey, jpeg) or None at end, _close(), set/controls
    def start(self):
        self._stop.clear()
        self._open()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"lizard-{self.cam}")
        self._thread.start()
        return True

    def _run(self):
        try:
            while not self._stop.is_set():
                got = self._read_frame()
                if got is None:
                    break
                grey, jpeg = got
                if grey is None:
                    continue
                self._deliver(grey, jpeg)
        except Exception as exc:          # a reader error ends the stream; running() turns False
            self.error = str(exc)
        finally:
            with self._cond:
                self._cond.notify_all()

    def _deliver(self, grey, jpeg):
        f = Frame(time.time(), self.frames, grey, jpeg, self.cam)
        self.frames += 1
        self.times.append(f.t)
        self.pulse.add(f)
        f.lit = self.pulse.classify(f)
        with self._cond:
            self.latest = f
            if f.lit:
                self.latest_lit = f
            else:
                self.latest_dark = f
            self._cond.notify_all()
        for fn in list(self.listeners):
            try:
                fn(f)
            except Exception:
                pass

    def wait_frame(self, after_index=-1, lit=None, timeout=2.0):
        """Block until a frame newer than after_index arrives (lit only if lit=True). None on timeout."""
        end = time.time() + timeout
        with self._cond:
            while True:
                f = self.latest_lit if lit else self.latest
                if f is not None and f.index > after_index:
                    return f
                left = end - time.time()
                if left <= 0 or not self.running():
                    return None
                self._cond.wait(min(left, 0.2))

    def running(self):
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def fps(self):
        if len(self.times) < 2:
            return 0.0
        return (len(self.times) - 1) / max(self.times[-1] - self.times[0], 1e-9)

    def stop(self):
        self._stop.set()
        try:
            self._close()
        finally:
            if self._thread is not None:
                self._thread.join(timeout=3)

    # settings: overridden by backends that can change them
    def controls(self):
        return []

    def set(self, name, value):
        return "not supported by this camera backend"


class V4L2Stream(_StreamBase):
    """Linux: video through v4l2-ctl --stream-to=- (no extra Python packages)."""
    backend = "v4l2"

    def __init__(self, node, cam="A", mode=DEFAULT_MODE):
        super().__init__(cam, mode)
        self.node = node
        self.proc = None
        self._buf = b""

    def _open(self):
        m = MODES[self.mode]
        w, h = m["size"]
        cmd = ["v4l2-ctl", "-d", self.node, f"--set-fmt-video=width={w},height={h},pixelformat={m['fourcc']}",
               "--stream-mmap=4", "--stream-to=-"]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            raise CameraError(f"could not run v4l2-ctl ({exc.strerror}); install it with: "
                              "sudo apt install v4l-utils") from exc

    def _read_frame(self):
        out = self.proc.stdout
        if self.mode == "yuyv":
            w, h = MODES["yuyv"]["size"]
            data = out.read(w * h * 2)
            if len(data) < w * h * 2:
                self._note_exit()
                return None
            return np.frombuffer(data, np.uint8)[0::2].reshape(h, w).copy(), None
        while True:
            a = self._buf.find(b"\xff\xd8")
            b = self._buf.find(b"\xff\xd9", a + 2) if a >= 0 else -1
            if b >= 0:
                jpeg, self._buf = self._buf[a:b + 2], self._buf[b + 2:]
                try:
                    return decode_jpeg(jpeg, self.work), jpeg
                except Exception:
                    return None, None            # a damaged frame: skip it
            chunk = out.read1(65536) if hasattr(out, "read1") else out.read(65536)
            if not chunk:
                self._note_exit()
                return None
            self._buf += chunk
            if len(self._buf) > 8 * 1024 * 1024:
                self._buf = b""

    def _note_exit(self):
        try:
            err = (self.proc.stderr.read() or b"").decode("utf-8", "replace").strip()
        except Exception:
            err = ""
        if err and not self._stop.is_set():
            self.error = err.splitlines()[-1]

    def _close(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def controls(self):
        return v4l2_controls(self.node)

    def set(self, name, value):
        return v4l2_set(self.node, name, value)


class OpenCVStream(_StreamBase):
    """Windows / macOS: video through OpenCV. EXPERIMENTAL AND UNTESTED: OpenCV cannot tell which index is
    the Lizard, so `index` is chosen by find_opencv_lizard() (the cameras that give grey, infrared-looking
    pictures) or by hand. DirectShow takes exposure as log2(seconds)."""
    backend = "opencv"

    def __init__(self, index, cam="A", mode=DEFAULT_MODE):
        super().__init__(cam, mode)
        self.index = index
        self.cap = None
        self._settings = {}

    def _open(self):
        try:
            import cv2
        except ImportError as exc:
            raise CameraError("OpenCV is needed for the scanner on this system (the app installs it)") from exc
        api = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
        self.cap = cv2.VideoCapture(self.index, api)
        if not self.cap.isOpened():
            raise CameraError(f"camera {self.index} could not be opened")
        m = MODES[self.mode]
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*m["fourcc"]))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, m["size"][0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, m["size"][1])

    def _read_frame(self):
        import cv2
        ok, img = self.cap.read()
        if not ok:
            return None
        grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        return cv2.resize(grey, self.work, interpolation=cv2.INTER_AREA), None

    def _close(self):
        if self.cap is not None:
            self.cap.release()

    def controls(self):
        return [dict(name=n, type="int", value=v) for n, v in self._settings.items()]

    def set(self, name, value):
        import cv2
        if self.cap is None:
            return "camera not open"
        if "exposure" in name and "auto" not in name:
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25 if not sys.platform.startswith("win") else 0)
            secs = max(1, int(value)) * 1e-4
            ok = self.cap.set(cv2.CAP_PROP_EXPOSURE, math.log2(secs) if sys.platform.startswith("win") else secs)
        elif name == "gain":
            ok = self.cap.set(cv2.CAP_PROP_GAIN, int(value))
        else:
            return f"{name} is not available through OpenCV"
        self._settings[name] = int(value)
        return "" if ok else f"the camera did not accept {name}"


def find_opencv_lizard(max_index=6):
    """(Windows / macOS) Camera indexes whose pictures look infrared (grey: red = green = blue).
    UNTESTED on a real Lizard."""
    try:
        import cv2
    except ImportError:
        return []
    found = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY)
        try:
            if not cap.isOpened():
                continue
            ok, img = cap.read()
            if ok and img is not None and img.ndim == 3:
                spread = float(np.abs(img[..., 0].astype(int) - img[..., 2].astype(int)).mean())
                if spread < 3.0:
                    found.append(i)
        finally:
            cap.release()
    return found


# ---------------------------------------------------------------------------
# Simulated Lizard: a stripe projector beside an infrared camera, looking at a wall
# (and optionally a ball). Used by the tests, the screenshots and the app's
# "Simulated scanner" choice, so everything can be tried without the hardware.
# The pattern copies the real one (18 October 2026): vertical stripes about 6.3 px
# apart at 640 wide, each a string of beads whose width and position wobble.
# ---------------------------------------------------------------------------

SIM_FOCAL_PX = 760.0        # at the 640-wide working size (about 46 degrees across)
SIM_BASELINE_MM = 60.0      # projector to camera, along the picture's x axis (real scanner: K about 50,000 px.mm)
SIM_GLASS_MM = 12.0         # the optical centre sits this far behind the front glass
SIM_STRIPE_PX = 6.34        # stripe spacing at 640 wide, as measured on the real scanner
SIM_SUPER = 2               # the pattern is drawn at twice the working size, so sampling it does not alias


def sim_stripe_pattern(seed=11, size=(1600, 900), period=SIM_STRIPE_PX):
    """The simulated projector's fixed pattern (float 0..1), at SIM_SUPER x the working size: vertical
    stripes, each made of beads of random length whose width, sideways position and brightness wobble."""
    rng = np.random.default_rng(seed)
    w, h = size[0] * SIM_SUPER, size[1]
    p = period * SIM_SUPER
    n = int(w / p) + 3
    off = np.zeros((n, h), np.float32)
    sig = np.zeros((n, h), np.float32)
    amp = np.zeros((n, h), np.float32)
    for k in range(n):
        y = 0
        while y < h:
            ln = int(rng.integers(3, 9))
            off[k, y:y + ln] = rng.uniform(-0.55, 0.55) * SIM_SUPER
            sig[k, y:y + ln] = rng.choice((0.5, 0.75, 1.0)) * SIM_SUPER
            amp[k, y:y + ln] = rng.choice((0.55, 0.8, 1.0))
            y += ln
            if y < h and rng.random() < 0.35:          # a short gap between beads
                gap = int(rng.integers(1, 3))
                amp[k, y:y + gap] = 0.15
                sig[k, y:y + gap] = 0.6 * SIM_SUPER
                y += gap
    x = np.arange(w, dtype=np.float32)
    k0 = np.floor(x / p).astype(int)
    pat = np.zeros((h, w), np.float32)
    for k in (k0 - 1, k0, k0 + 1):
        kk = np.clip(k, 0, n - 1)
        centre = kk[None, :] * p + off[kk].T
        d = x[None, :] - centre
        pat += amp[kk].T * np.exp(-d * d / (2 * sig[kk].T ** 2))
    return np.clip(pat, 0, 1)


def sim_dot_pattern(seed=11, size=(1600, 900)):
    """Older name, kept for anything that imported it: the simulated projector's pattern."""
    return sim_stripe_pattern(seed, size)


class SimScene:
    """What the simulated scanner looks at. Distances in mm from the front glass."""

    def __init__(self, wall_mm=300.0, ball=None, tilt_deg=0.0):
        self.wall_mm = float(wall_mm)
        self.ball = ball              # None or (x_mm, y_mm, z_mm_of_centre, radius_mm), camera coordinates
        self.tilt_deg = float(tilt_deg)

    def depth(self, w, h, f):
        """Distance from the optical centre along the axis, per pixel (mm)."""
        u = (np.arange(w, dtype=np.float32) - w / 2 + 0.5)[None, :]
        v = (np.arange(h, dtype=np.float32) - h / 2 + 0.5)[:, None]
        z0 = self.wall_mm + SIM_GLASS_MM
        if self.tilt_deg:
            t = math.tan(math.radians(self.tilt_deg))
            z = z0 / (1.0 - t * u / f) * np.ones_like(v)         # wall turned about the vertical axis
        else:
            z = np.full((h, w), z0, np.float32)
        if self.ball is not None:
            bx, by, bz, r = self.ball
            bz += SIM_GLASS_MM
            dx, dy = u / f, v / f                                  # ray (dx, dy, 1)
            a = dx * dx + dy * dy + 1.0
            b = -2.0 * (dx * bx + dy * by + bz)
            c = bx * bx + by * by + bz * bz - r * r
            disc = b * b - 4 * a * c
            hit = disc > 0
            tz = np.where(hit, (-b - np.sqrt(np.maximum(disc, 0))) / (2 * a), np.inf)
            z = np.where(hit & (tz < z), tz, z)
        return z.astype(np.float32)


class SimStream(_StreamBase):
    """A pretend Lizard. Brightness follows exposure x gain, the projector flashes once per picture, shiny
    settings saturate, and the stripes slide with distance exactly as a structured-light camera does."""
    backend = "sim"
    _pattern = None

    def __init__(self, cam="A", mode=DEFAULT_MODE, scene=None, fps=10.0, realtime=True, seed=5):
        super().__init__(cam, mode)
        self.scene = scene or SimScene()
        self.period = 1.0 / fps
        self.realtime = realtime
        self.rng = np.random.default_rng(seed)
        # Defaults as read from a real Lizard's camera A in the USB recordings: exposure 60 (6 ms), gain 16.
        # The ranges are not known; these are typical UVC ones.
        self.settings = {"auto_exposure": 3, "exposure_time_absolute": 60, "gain": 16, "brightness": 0}
        self.ranges = {"exposure_time_absolute": (1, 5000, 60), "gain": (0, 255, 16),
                       "auto_exposure": (1, 3, 3), "brightness": (-64, 64, 0)}
        self._k = 0
        # lit / dark per picture, repeating. The real scanner flashes once per picture, so every picture is
        # lit (16 October 2026); tests try other patterns.
        self.pulse_pattern = (True,)
        self.ambient = 0.15                  # room infrared, in units of the projector's light
        self.texture_ring = cam == "B"       # camera B's pictures: the white ring lights the scene evenly
        self._next = 0.0
        if SimStream._pattern is None:
            SimStream._pattern = sim_stripe_pattern()

    def _open(self):
        self._next = time.time()

    def _close(self):
        pass

    def render(self, lit):
        w, h = self.work
        f = SIM_FOCAL_PX * w / 640.0
        z = self.scene.depth(w, h, f)
        pat = SimStream._pattern
        ph, pw = pat.shape
        # projector pixel for each camera pixel: shifted along x by f * baseline / z (pattern at SIM_SUPER x)
        sx = SIM_SUPER * 640.0 / w
        u = (np.arange(w, dtype=np.float32)[None, :] - f * SIM_BASELINE_MM / z + 120) * sx + (pw - w * sx) / 2
        v = np.arange(h, dtype=np.float32)[:, None] + (ph - h) / 2 * np.ones_like(z)
        u = np.clip(u, 0, pw - 2)
        u0 = np.floor(u).astype(int)
        fu = (u - u0).astype(np.float32)
        vi = np.clip(np.rint(v).astype(int), 0, ph - 1)
        dots = pat[vi, u0] * (1 - fu) + pat[vi, u0 + 1] * fu      # sub-pixel, like a real lens
        auto = self.settings.get("auto_exposure", 3) != 1
        falloff = (300.0 / np.maximum(z, 50.0)) ** 2
        ambient = self.ambient
        if self.texture_ring:            # white ring: even light, no stripes
            dots = np.zeros_like(dots) + 0.35
        lit_light = ambient + (2.2 * dots + 0.25) * falloff
        light = lit_light if lit else ambient * np.ones_like(z)
        if auto:      # like a camera's own automatic exposure: aims the lit frames' average at about 70
            scale = 70.0 / max(float(lit_light.mean()), 1e-3)
        else:         # 1 ms at gain 64 gives 25 picture units per unit of light
            scale = self.settings.get("exposure_time_absolute", 60) / 10.0 * (self.settings.get("gain", 16) / 64.0) * 25.0
        img = light * scale + self.rng.normal(0, 2.0, z.shape) + self.settings.get("brightness", 0) * 0.5
        return np.clip(img, 0, 255).astype(np.uint8)

    def _read_frame(self):
        if self.realtime:
            delay = self._next - time.time()
            if delay > 0:
                if self._stop.wait(delay):
                    return None
            self._next = max(self._next + self.period, time.time() - self.period)
        if self._stop.is_set():
            return None
        lit = bool(self.pulse_pattern[self._k % len(self.pulse_pattern)])
        self._k += 1
        return self.render(lit), None

    def controls(self):
        out = []
        for name, (lo, hi, default) in self.ranges.items():
            out.append(dict(name=name, type="int" if name != "auto_exposure" else "menu", min=lo, max=hi,
                            default=default, value=self.settings.get(name, default), flags="",
                            menu={1: "Manual Mode", 3: "Aperture Priority Mode"} if name == "auto_exposure" else {}))
        return out

    def set(self, name, value):
        if name not in self.ranges:
            return f"unknown control {name}"
        lo, hi, _d = self.ranges[name]
        if not lo <= int(value) <= hi:
            return f"{name}={value} is outside {lo}..{hi}"
        self.settings[name] = int(value)
        return ""


def session_mode(info, cam="A"):
    """Video mode of a recording: Lizard Scanner 0.1-0.3 sessions store "mode"; lizard_framecap captures
    store it per camera ("mjpg" or e.g. "mjpg 1280x720")."""
    m = info.get("mode") or (info.get("cameras", {}).get(cam, {}) or {}).get("mode") or DEFAULT_MODE
    m = str(m).split()[0].lower()
    return m if m in MODES else DEFAULT_MODE


class ReplayStream(_StreamBase):
    """Plays back a recording in a loop: a Lizard Scanner session or a lizard_framecap capture (both are a
    folder with session.json and frames.csv)."""
    backend = "replay"

    def __init__(self, folder, cam="A", realtime=True):
        info = load_session_info(folder)
        super().__init__(cam, session_mode(info, cam))
        self.folder = folder
        self.realtime = realtime
        self.rows = [r for r in read_session_rows(folder) if r["camera"] == cam and r.get("file")]
        for r in self.rows:                       # framecap captures call the time column t_s
            r.setdefault("t", r.get("t_s", "0"))
        if not self.rows:
            raise CameraError(f"the recording has no frames from camera {cam}")
        self._i = 0
        self._t0 = None

    def _open(self):
        self._t0 = time.time()

    def _close(self):
        pass

    def _read_frame(self):
        if self._i >= len(self.rows):
            self._i = 0
            self._t0 = time.time()
        row = self.rows[self._i]
        self._i += 1
        if self.realtime:
            due = self._t0 + float(row["t"]) - float(self.rows[0]["t"])
            if self._stop.wait(max(0.0, due - time.time())):
                return None
        path = os.path.join(self.folder, row["file"])
        with open(path, "rb") as f:
            data = f.read()
        if data[:2] == b"\xff\xd8":
            return decode_jpeg(data, self.work), data
        return np.asarray(Image.open(io.BytesIO(data)).convert("L")), None


class Lizard:
    """The scanner: finds camera A and B, starts and stops them, changes their settings.
    backend: 'auto' (v4l2 on Linux, OpenCV elsewhere), 'v4l2', 'opencv', 'sim' or 'replay:<folder>'."""

    def __init__(self, backend="auto"):
        self.backend = backend
        self.streams = {}
        self.nodes = {}
        self.sim_scene = SimScene()
        self.sim_fps = 10.0           # the simulated scanner's picture rate (tests turn it up)
        self.on_set = []              # called as fn(cam, name, value, error) after every setting change
        self.on_start = []            # called as fn(cam, stream) after a camera has started
        self.refresh()

    def refresh(self):
        b = self.backend
        if b == "sim" or b.startswith("replay:"):
            self.nodes = {"A": b, "B": b}
        elif b == "v4l2" or (b == "auto" and sys.platform.startswith("linux")):
            self.nodes = lizard_nodes()
        else:
            idx = find_opencv_lizard()
            self.nodes = {"A": idx[0] if idx else None, "B": idx[1] if len(idx) > 1 else None}
        return dict(self.nodes)

    def connected(self, cam="A"):
        return self.nodes.get(cam) is not None

    def kind(self):
        b = self.backend
        if b == "sim":
            return "sim"
        if b.startswith("replay:"):
            return "replay"
        return "v4l2" if (b == "v4l2" or (b == "auto" and sys.platform.startswith("linux"))) else "opencv"

    def start(self, cam="A", mode=DEFAULT_MODE):
        node = self.nodes.get(cam)
        if node is None:
            raise CameraError(f"camera {cam} not found (is the scanner plugged in?)")
        self.stop(cam)
        kind = self.kind()
        if kind == "sim":
            s = SimStream(cam, mode, scene=self.sim_scene, fps=self.sim_fps)
        elif kind == "replay":
            s = ReplayStream(self.backend[len("replay:"):], cam)
        elif kind == "v4l2":
            s = V4L2Stream(node, cam, mode)
        else:
            s = OpenCVStream(node, cam, mode)
        if kind == "v4l2":
            self.limit_flash(cam)            # before the video starts, so the first flashes are short too
        s.start()
        self.streams[cam] = s
        if kind != "replay":
            self.limit_flash(cam)
        for fn in list(self.on_start):
            try:
                fn(cam, s)
            except Exception:
                pass
        return s

    def set_light(self, cam, exposure, gain):
        """Manual exposure (100 us units) and gain on one camera, under whatever names it uses."""
        ctrls = self.controls(cam)
        auto = _control(ctrls, "auto_exposure", "exposure_auto")
        exp = _control(ctrls, "exposure_time_absolute", "exposure_absolute")
        errors = []
        if auto is not None and auto.get("value") != 1:
            errors.append(self.set(cam, auto["name"], 1))
        if exp is not None:
            errors.append(self.set(cam, exp["name"], exposure))
        if _control(ctrls, "gain") is not None:
            errors.append(self.set(cam, "gain", gain))
        return "; ".join(e for e in errors if e)

    def limit_flash(self, cam):
        """CR Scan's own 1 ms exposure, gain 65. The lights (the white ring is camera B's, the infrared
        projector camera A's) flash for as long as their camera exposes, and a camera's own automatic
        exposure flashes much longer. Every camera starts like this; the light buttons take over from there."""
        return self.set_light(cam, SAFE_EXPOSURE, 65)

    def stream(self, cam="A"):
        s = self.streams.get(cam)
        return s if s is not None and s.running() else None

    def stop(self, cam="A"):
        s = self.streams.pop(cam, None)
        if s is not None:
            s.stop()

    def stop_all(self):
        for cam in list(self.streams):
            self.stop(cam)

    def controls(self, cam="A"):
        s = self.streams.get(cam)
        if s is not None:
            return s.controls()
        node = self.nodes.get(cam)
        return v4l2_controls(node) if self.kind() == "v4l2" and node else []

    def set(self, cam, name, value):
        """Change one camera setting; '' or the camera's error. Every change, accepted or not, is reported
        to the functions in on_set(cam, name, value, error), e.g. a running frame capture."""
        s = self.streams.get(cam)
        if s is None:
            node = self.nodes.get(cam)
            if self.kind() != "v4l2" or not node:
                err = f"camera {cam} is not running"
            else:
                err = v4l2_set(node, name, value)
        else:
            err = s.set(name, value)
        for fn in list(self.on_set):
            try:
                fn(cam, name, value, err)
            except Exception:
                pass
        return err

    def serial(self):
        if self.kind() == "sim":
            return "SIMULATED"
        if self.kind() == "replay":
            info = load_session_info(self.backend[len("replay:"):])
            return (info.get("serial") or (info.get("cameras", {}).get("A", {}) or {}).get("serial")
                    or "replay")
        node = self.nodes.get("A")
        if self.kind() != "v4l2" or not node:
            return ""
        try:
            return read_lizard_serial(node)
        except OSError:
            return ""


# ===========================================================================
# 2. Settings: a ladder of light levels instead of sliders
# ===========================================================================

# The white ring and the projector flash while the sensor exposes, so EXPOSURE IS HOW LONG THE LIGHTS FLASH
# (the owner saw CR Scan's brightness control dim the ring from "unbearably bright" to dim, and over USB that
# control only changed exposure and gain; gain cannot drive an LED). So the normal ladder never goes past
# CR Scan's own longest flash, 1 ms, and brightens further with gain only.
SAFE_EXPOSURE = 10              # 1 ms, in 100 us units: the longest flash CR Scan was seen to use
LONG_EXPOSURE_MAX = 60          # 6 ms: the camera's own starting value, so the firmware's own choice
# (exposure in 100 us units, gain). CR Scan itself was seen to use (10, 65), (5, 40) and (5, 30).
LADDER = [(2, 16), (3, 24), (5, 30), (5, 40), (8, 50), (10, 65), (10, 80), (10, 96), (10, 112), (10, 128),
          (10, 160)]
# Only with "Allow longer flashes" switched on (an advanced choice with a warning): brighter ring.
LONG_LADDER = [(15, 128), (20, 128), (30, 128), (45, 128), (60, 128)]
PRESETS = [  # name, ladder step, what it is for
    ("Shiny / white", 2, "CR Scan's dimmest setting: for light, glossy objects that glare"),
    ("Normal", 5, "CR Scan's own default (1 ms exposure, gain 65)"),
    ("Dark / black", 8, "more gain for dark objects; the flash stays at 1 ms"),
]
TUNE_TARGET = (170, 240)        # the brightest 0.5% of a lit picture should land in here...
TUNE_MAX_SATURATED = 0.004      # ...with at most this share of pixels blown out
TUNE_SETTLE_FRAMES = 3          # frames thrown away after each change, so the new setting has taken
TUNE_MEASURE_FRAMES = 3         # lit frames measured per step


def _control(controls, *names):
    for n in names:
        for c in controls:
            if c["name"] == n:
                return c
    return None


class Tuner:
    """Light-level control for one camera: presets, Brighter / Dimmer, auto-tune, camera defaults."""

    def __init__(self, lizard, cam="A", mirror=("B",)):
        self.lizard, self.cam, self.mirror = lizard, cam, tuple(mirror)
        self.step = None              # ladder step in use, None = camera's own (automatic) exposure
        self.custom = False           # True after a setting was changed by hand (Every camera setting)
        self.allow_long = False       # rungs with flashes longer than 1 ms (LONG_LADDER); off unless asked for
        self.last_report = ""

    CONTROLS_TTL_S = 3.0      # camera settings are read again at most this often (v4l2-ctl is a process)

    def _names(self, fresh=False):
        now = time.time()
        cache = getattr(self, "_ctrl_cache", None)
        if fresh or cache is None or now - cache[0] > self.CONTROLS_TTL_S:
            cache = self._ctrl_cache = (now, self.lizard.controls(self.cam))
        ctrls = cache[1]
        exp = _control(ctrls, "exposure_time_absolute", "exposure_absolute")
        gain = _control(ctrls, "gain")
        auto = _control(ctrls, "auto_exposure", "exposure_auto")
        return ctrls, exp, gain, auto

    def ladder(self):
        """The ladder clamped to what this camera accepts (duplicates removed)."""
        _ctrls, exp, gain, _auto = self._names()
        out = []
        for e, g in LADDER + (LONG_LADDER if self.allow_long else []):
            if exp is not None and "min" in exp:
                e = min(max(e, exp["min"]), exp["max"])
            if gain is not None and "min" in gain:
                g = min(max(g, gain["min"]), gain["max"])
            e = min(e, LONG_EXPOSURE_MAX if self.allow_long else SAFE_EXPOSURE)
            if not out or out[-1] != (e, g):
                out.append((e, g))
        return out

    def flash_ms(self):
        """How long the lights flash at the current rung, in ms (None when the camera decides)."""
        if self.step is None or self.custom:
            return None
        ladder = self.ladder()
        return ladder[min(self.step, len(ladder) - 1)][0] / 10.0

    def apply_step(self, step):
        ladder = self.ladder()
        step = max(0, min(len(ladder) - 1, int(step)))
        e, g = ladder[step]
        _ctrls, exp, gain, auto = self._names(fresh=True)
        errors = []
        if auto is not None and auto.get("value") != 1:
            errors.append(self.lizard.set(self.cam, auto["name"], 1))   # 1 = manual exposure
        errors.append(self.lizard.set(self.cam, exp["name"] if exp else "exposure_time_absolute", e))
        errors.append(self.lizard.set(self.cam, "gain", g))
        # The white ring is camera B's: when B runs it gets the same flash length, so the light buttons
        # (and the 1 ms cap) cover both lights.
        for other in self.mirror:
            if other != self.cam and self.lizard.stream(other) is not None:
                errors.append(self.lizard.set_light(other, e, g))
        self.step = step
        self.custom = False
        return "; ".join(x for x in errors if x)

    def apply_preset(self, name):
        for pname, step, _why in PRESETS:
            if pname == name:
                return self.apply_step(step)
        return f"no preset called {name}"

    def brighter(self):
        return self.apply_step((self.step if self.step is not None else 5) + 1)

    def dimmer(self):
        return self.apply_step((self.step if self.step is not None else 5) - 1)

    def camera_defaults(self):
        ctrls = self.lizard.controls(self.cam)
        errors = []
        for c in ctrls:
            if "default" in c and c.get("value") != c["default"] and "inactive" not in c.get("flags", ""):
                errors.append(self.lizard.set(self.cam, c["name"], c["default"]))
        self.step = None
        self.custom = False
        return "; ".join(x for x in errors if x)

    def describe(self):
        if self.custom:
            return "Custom settings (set by hand in Every camera setting)"
        if self.step is None:
            return "Camera's own automatic exposure"
        ladder = self.ladder()
        e, g = ladder[min(self.step, len(ladder) - 1)]
        long = "  \u2022  LONG FLASHES" if e > SAFE_EXPOSURE else ""
        return f"Light level {self.step + 1} of {len(ladder)}  \u2022  flash {e / 10:.1f} ms, gain {g}{long}"

    @staticmethod
    def measure(grey):
        """(brightest 0.5% level, share of blown-out pixels) of a picture."""
        g = grey[::2, ::2]
        return float(np.percentile(g, 99.5)), float((g >= 250).mean())

    def auto_tune(self, progress=lambda msg: None, cancel=None):
        """Walk the ladder until the dots are bright but not blown out. Needs the camera running.
        Returns (ok, report)."""
        s = self.lizard.stream(self.cam)
        if s is None:
            return False, "Start the camera first."
        ladder = self.ladder()
        step = self.step if self.step is not None else 5
        tried = {}
        for _ in range(len(ladder) + 2):
            if cancel is not None and cancel.is_set():
                return False, "Auto-tune cancelled."
            self.apply_step(step)
            f = s.latest
            idx = f.index if f else -1
            for _k in range(TUNE_SETTLE_FRAMES):
                f = s.wait_frame(idx)
                idx = f.index if f else idx
            vals = []
            for _k in range(TUNE_MEASURE_FRAMES):
                # The lit / dark flag lags behind a change of setting, so judge each pair of
                # frames by itself: the brighter of two neighbours is the lit one.
                pair = []
                for _p in range(2):
                    f = s.wait_frame(idx)
                    if f is None:
                        return False, "The camera stopped sending pictures."
                    idx = f.index
                    pair.append(f)
                vals.append(self.measure(max(pair, key=lambda fr: fr.detail).grey))
            top = float(np.median([v[0] for v in vals]))
            sat = float(np.median([v[1] for v in vals]))
            tried[step] = (top, sat)
            progress(f"Auto-tune: level {step + 1}: brightest {top:.0f}, blown out {sat * 100:.1f}%")
            if sat > TUNE_MAX_SATURATED or top > TUNE_TARGET[1]:
                nxt = step - 1
            elif top < TUNE_TARGET[0]:
                nxt = step + 1
            else:
                self.last_report = f"Auto-tune: level {step + 1} is right ({self.describe()})."
                return True, self.last_report
            if nxt < 0 or nxt >= len(ladder) or nxt in tried:
                # at the end of the ladder, or going back and forth: keep the best one that is not blown out
                good = [k for k, (t, sa) in tried.items() if sa <= TUNE_MAX_SATURATED and t <= 252]
                best = max(good, key=lambda k: tried[k][0]) if good else min(tried)
                self.apply_step(best)
                why = ("the object stays dark even at the brightest level" if nxt >= len(ladder) else
                       "even the dimmest level is blown out (very shiny?)" if nxt < 0 else
                       "nothing in between was perfect")
                self.last_report = f"Auto-tune: chose level {best + 1}; {why}."
                return True, self.last_report
            step = nxt
        return False, "Auto-tune did not settle."


# ===========================================================================
# 3 and 4. Wall calibration and depth
# ===========================================================================

def box_filter(a, r):
    """Mean over a (2r+1) square window (edges: shrinking window). Integral image, any dtype -> float32."""
    a = np.asarray(a, np.float32)
    h, w = a.shape
    c = np.zeros((h + 1, w + 1), np.float64)
    c[1:, 1:] = a.cumsum(0).cumsum(1)
    y0 = np.clip(np.arange(h) - r, 0, h)
    y1 = np.clip(np.arange(h) + r + 1, 0, h)
    x0 = np.clip(np.arange(w) - r, 0, w)
    x1 = np.clip(np.arange(w) + r + 1, 0, w)
    s = c[y1][:, x1] - c[y0][:, x1] - c[y1][:, x0] + c[y0][:, x0]
    n = (y1 - y0)[:, None] * (x1 - x0)[None, :]
    return (s / n).astype(np.float32)


def pattern_image(grey, dark=None, r=6):
    """The projector's pattern, flattened: background light removed, contrast evened out, so pictures taken
    under different exposure and distance compare fairly. `dark` (a frame without the projector) is
    subtracted first when given."""
    a = np.asarray(grey, np.float32)
    if dark is not None:
        a = a - np.asarray(dark, np.float32)
    hp = a - box_filter(a, r)
    # rounding in the box filter can make a flat patch's variance a hair below zero: clamp, or sqrt gives NaN
    sd = np.sqrt(np.maximum(box_filter(hp * hp, r), 0.0)) + 2.0
    return np.clip(hp / sd, -4, 4).astype(np.float32)


def phase_shift(a, b):
    """Global (dx, dy) such that a(x) ~ b(x - dx), and the correlation peak's strength."""
    h, w = a.shape
    win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    fa = np.fft.rfft2(a * win)
    fb = np.fft.rfft2(b * win)
    r = fa * np.conj(fb)
    r /= np.abs(r) + 1e-9
    c = np.fft.irfft2(r, s=(h, w))
    iy, ix = np.unravel_index(int(np.argmax(c)), c.shape)
    peak = float(c[iy, ix])

    def sub(cm, i0, i1, ip):          # parabola through the peak and its neighbours
        den = cm - 2 * i0 + ip
        return 0.0 if abs(den) < 1e-12 else 0.5 * (cm - ip) / den
    dx = ix + sub(c[iy, (ix - 1) % w], c[iy, ix], 0, c[iy, (ix + 1) % w])
    dy = iy + sub(c[(iy - 1) % h, ix], c[iy, ix], 0, c[(iy + 1) % h, ix])
    if dx > w / 2:
        dx -= w
    if dy > h / 2:
        dy -= h
    return float(dx), float(dy), peak


def tile_shifts(a, b, tiles=(4, 3)):
    """Shift of each tile (dx, dy, peak) -- shows a tilted wall or a bent lens as a spread."""
    h, w = a.shape
    tx, ty = tiles
    out = []
    for j in range(ty):
        for i in range(tx):
            ys, xs = slice(j * h // ty, (j + 1) * h // ty), slice(i * w // tx, (i + 1) * w // tx)
            out.append(phase_shift(a[ys, xs], b[ys, xs]))
    return out

# ---------------------------------------------------------------------------
# The real Lizard's projector (seen 18 October 2026): dense VERTICAL STRIPES, about 6.3 px apart at the
# 640-wide working size, each stripe a string of beads whose width and position wobble irregularly along
# it, differently on every stripe. The stripes alone repeat every 6.3 px, so matching them is ambiguous;
# the wobble is the code that tells one stripe from another. stripe_code() keeps only the wobble, by
# taking away each pixel's vertical running mean (which is the plain stripe). Matching then uses tall,
# narrow windows (many beads along a few stripes) and a wide search, and the shift is always along x.
# ---------------------------------------------------------------------------

CODE_RADIUS = 8                 # rows each side in the running mean that is taken away
MATCH_RY, MATCH_RX = 24, 10     # matching window: (2*24+1) rows x (2*10+1) columns at 640 x 360
MATCH_ROW_STEP = 6              # depth is worked out on every 6th row (and every column), then filled in
MATCH_MIN_SCORE = 0.30          # weaker matches are left out of the depth map
MATCH_MIN_MARGIN = 0.06         # ...and so are matches with a near-equal rival elsewhere (wrong stripe)
RIVAL_GAP = 3                   # rivals closer than this many px to the best are the same match
TRACK_FULL_EVERY = 6            # live depth: every 6th picture searches every shift, the others track
TRACK_MARGIN_PX = 12            # ...the shifts seen last time, plus this much either side


def stripe_code(pat, r=CODE_RADIUS):
    """The stripes' wobble: a pattern picture minus its vertical running mean over 2r+1 rows."""
    a = np.asarray(pat, np.float32)
    c = np.cumsum(np.pad(a, ((r + 1, r), (0, 0)), mode="edge"), 0, dtype=np.float64)
    return (a - (c[2 * r + 1:] - c[:-2 * r - 1]) / (2 * r + 1)).astype(np.float32)


def code_image(grey, dark=None):
    """What gets matched: pattern_image(), then stripe_code()."""
    return stripe_code(pattern_image(grey, dark))


def _hsum(a, rx):
    """Sums over 2rx+1 neighbours along the last axis (fewer at the ends)."""
    n = a.shape[-1]
    c = np.zeros(a.shape[:-1] + (n + 1,), np.float32)
    np.cumsum(a, -1, out=c[..., 1:])
    x0 = np.clip(np.arange(n) - rx, 0, n)
    x1 = np.clip(np.arange(n) + rx + 1, 0, n)
    return c[..., x1] - c[..., x0]


def match_rows(a, b, shifts, rows, ry=MATCH_RY, rx=MATCH_RX):
    """Normalised correlation of picture a(x) against b(x - s), for every shift s, in windows of
    (2ry+1) x (2rx+1) centred on the given rows and on every column. (S, len(rows), W) float32;
    -1 where the window runs off b (or off a)."""
    a = np.ascontiguousarray(a, np.float32)
    b = np.ascontiguousarray(b, np.float32)
    h, w = a.shape
    rows = np.asarray(rows, int)
    y0, y1 = np.clip(rows - ry, 0, h), np.clip(rows + ry + 1, 0, h)
    cum = np.empty((h + 1, w), np.float32)
    cum[0] = 0.0

    def vsum(img):
        np.cumsum(img, 0, out=cum[1:])
        return cum[y1] - cum[y0]

    n = ((y1 - y0) * (2 * rx + 1)).astype(np.float32)[:, None]
    sa, saa = _hsum(vsum(a), rx), _hsum(vsum(a * a), rx)
    va = np.maximum(saa - sa * sa / n, 1e-6)
    hb, hbb = _hsum(vsum(b), rx), _hsum(vsum(b * b), rx)
    vb_all = np.maximum(hbb - hb * hb / n, 1e-6)
    out = np.full((len(shifts), len(rows), w), -1.0, np.float32)
    prod = np.zeros_like(a)
    inv_a = 1.0 / np.sqrt(va)
    for i, s in enumerate(shifts):
        s = int(s)
        lo, hi = max(s + rx, rx), min(w - 1 + s - rx, w - 1 - rx)   # columns whose window fits a and b
        if lo > hi:
            continue
        x0, x1 = max(lo - rx, 0), min(hi + rx + 1, w)                # columns the product is needed on
        np.multiply(a[:, x0:x1], b[:, x0 - s:x1 - s], out=prod[:, x0:x1])
        np.cumsum(prod[:, x0:x1], 0, out=cum[1:, x0:x1])
        sp = _hsum(cum[y1, x0:x1] - cum[y0, x0:x1], rx)[:, lo - x0:hi - x0 + 1]
        sb = hb[:, lo - s:hi - s + 1]
        cov = sp - sa[:, lo:hi + 1] * sb / n
        out[i, :, lo:hi + 1] = cov * inv_a[:, lo:hi + 1] / np.sqrt(vb_all[:, lo - s:hi - s + 1])
    return out


def best_match(scores, shifts, min_score=MATCH_MIN_SCORE, min_margin=MATCH_MIN_MARGIN):
    """From match_rows' scores: (shift with sub-pixel part, score, margin over the best rival, ok)."""
    shifts = np.asarray(shifts, np.float32)
    S = len(shifts)
    j = np.argmax(scores, axis=0)
    top = np.take_along_axis(scores, j[None], 0)[0]
    idx = np.arange(S)[:, None, None]
    rival = np.where(np.abs(idx - j[None]) > RIVAL_GAP, scores, -1.0).max(0) if S > 2 * RIVAL_GAP + 1 \
        else np.full_like(top, -1.0)
    margin = top - rival
    jc = np.clip(j, 1, S - 2) if S >= 3 else j
    if S >= 3:
        a = np.take_along_axis(scores, (jc - 1)[None], 0)[0]
        b = np.take_along_axis(scores, jc[None], 0)[0]
        c = np.take_along_axis(scores, (jc + 1)[None], 0)[0]
        den = a - 2 * b + c
        sub = np.zeros_like(den)
        np.divide(0.5 * (a - c), den, out=sub, where=np.abs(den) > 1e-6)
        sub = np.where(j == jc, np.clip(sub, -0.5, 0.5), 0.0)
    else:
        sub = np.zeros_like(top)
    step = float(shifts[1] - shifts[0]) if S > 1 else 1.0
    shift = shifts[jc] + sub * step
    ok = (top >= min_score) & (margin >= min_margin) & (j > 0) & (j < S - 1)
    return shift.astype(np.float32), top, margin, ok


def _sample_x(field, xs):
    """field (R, W) sampled at float columns xs (R, W), linear; NaN outside or where field is NaN."""
    h, w = field.shape
    x0 = np.floor(xs).astype(int)
    f = xs - x0
    inside = (x0 >= 0) & (x0 < w - 1)
    x0c = np.clip(x0, 0, w - 2)
    a = np.take_along_axis(field, x0c, 1)
    b = np.take_along_axis(field, x0c + 1, 1)
    out = a * (1 - f) + b * f
    out[~inside] = np.nan
    return out


CALIB_VERSION = 2               # 1 = global shift of a dot pattern (wrong for the real Lizard)
MIN_WALLS = 3
WALL_FRAMES = 6                 # pictures averaged into one wall picture
FIT_ROW_STEP = 8                # the shift map is measured on every 8th row ...
FIT_COL_STEP = 8                # ... and fitted on every 8th column
WALL_SEARCH_FRACTION = 0.45     # neighbouring walls are searched up to this share of the picture width
SPREAD_WARN_MM = 3.0            # a wall whose depth spreads more than this was probably not flat or square
TILT_WARN_MM = 3.0              # left third vs right third
DEPTH_MEDIAN = True
WALL_PATTERNS = "wall_patterns.npz"


class WallCalibration:
    """The rough calibration. Collect walls with add_wall(), then fit().

    For every point of the picture: how far the stripe code slides between the walls. The fit is
        shift(x, y) = K(x, y) * (1 / (Z + c) - 1 / (Zref + c))
    with one c for the whole picture (where the optical centre sits relative to the front glass) and a
    smooth K map (focal length x baseline, which varies across the picture on the real scanner)."""

    def __init__(self, mode=DEFAULT_MODE):
        self.mode = mode
        self.walls = []             # (distance_mm, pattern float32)
        self.jpegs = {}             # distance_mm -> [camera's own JPEG bytes], saved with the calibration
        self.result = None
        self.reference = None
        self.fields = None

    def add_wall(self, distance_mm, greys, darks=None, jpegs=None):
        """Average several pictures of a flat wall at distance_mm (front glass to wall). jpegs: the camera's
        own full-size pictures, kept so the calibration can be refitted later from the originals."""
        pats = []
        for i, g in enumerate(greys):
            d = darks[i] if darks is not None and i < len(darks) else None
            pats.append(pattern_image(g, d))
        pat = np.mean(pats, axis=0).astype(np.float32)
        pat = pattern_image(pat * 40 + 128)         # even out again after averaging
        d = float(distance_mm)
        self.walls = [w for w in self.walls if abs(w[0] - d) > 1.0]
        self.jpegs = {k: v for k, v in self.jpegs.items() if abs(k - d) > 1.0}
        self.walls.append((d, pat))
        if jpegs:
            self.jpegs[d] = [j for j in jpegs if j]
        self.walls.sort(key=lambda w: w[0])
        return len(self.walls)

    def remove_wall(self, distance_mm):
        self.walls = [w for w in self.walls if abs(w[0] - distance_mm) > 1.0]
        self.jpegs = {k: v for k, v in self.jpegs.items() if abs(k - distance_mm) > 1.0}

    @classmethod
    def from_folder(cls, folder):
        """The walls saved in a calibration folder (any version), ready to fit() again: from
        walls/wall_patterns.npz (written as each wall is recorded) or else reference.npz."""
        path = os.path.join(folder, "walls", WALL_PATTERNS)
        if not os.path.exists(path):
            path = os.path.join(folder, "reference.npz")
        z = np.load(path)
        cal = cls()
        try:
            with open(os.path.join(folder, "calibration.json")) as f:
                cal.mode = json.load(f).get("mode", DEFAULT_MODE)
        except (OSError, ValueError):
            pass
        for d, p in zip(z["walls"], z["patterns"]):
            cal.walls.append((float(d), p.astype(np.float32)))
        for jp in sorted(glob.glob(os.path.join(folder, "walls", "*.jpg"))):
            m = re.match(r"wall_(\d+(?:\.\d+)?)mm_", os.path.basename(jp))
            if m:
                with open(jp, "rb") as f:
                    cal.jpegs.setdefault(float(m.group(1)), []).append(f.read())
        cal.walls.sort(key=lambda w: w[0])
        return cal

    def save_walls(self, folder):
        """The recorded walls so far, into folder/walls/: their patterns (wall_patterns.npz) and the
        camera's own pictures. Done after every wall, so nothing is lost if the window closes."""
        wall_dir = os.path.join(folder, "walls")
        os.makedirs(wall_dir, exist_ok=True)
        if self.walls:
            np.savez_compressed(os.path.join(wall_dir, WALL_PATTERNS), walls=np.array([w[0] for w in self.walls]),
                                patterns=np.stack([w[1] for w in self.walls]).astype(np.float16))
        elif os.path.exists(os.path.join(wall_dir, WALL_PATTERNS)):
            os.remove(os.path.join(wall_dir, WALL_PATTERNS))
        for old in glob.glob(os.path.join(wall_dir, "wall_*mm_*.jpg")):
            os.remove(old)
        for d, js in sorted(self.jpegs.items()):
            for n, data in enumerate(js, 1):
                with open(os.path.join(wall_dir, f"wall_{d:g}mm_{n}.jpg"), "wb") as f:
                    f.write(data)
        return wall_dir

    # the shift maps -----------------------------------------------------
    def _neighbour_field(self, ca, cb, rows):
        """Shift of wall a's code against wall b's on the fit rows, every column (NaN = no clear match)."""
        w = ca.shape[1]
        smax = int(w * WALL_SEARCH_FRACTION)
        shifts = np.arange(-smax, smax + 1)
        sc = match_rows(ca, cb, shifts, rows, ry=2 * MATCH_RY, rx=MATCH_RX)
        s, top, margin, ok = best_match(sc, shifts, min_score=0.25, min_margin=0.10)
        return np.where(ok, s, np.nan).astype(np.float32)

    def shift_maps(self, progress=lambda msg: None):
        """(rows, maps (n_walls, R, W) of each wall's shift against the reference wall, ref index)."""
        dists = [w[0] for w in self.walls]
        ref_i = int(np.argmin([abs(d - np.median(dists)) for d in dists]))
        codes = [stripe_code(p) for _d, p in self.walls]
        h, w = codes[0].shape
        rows = np.arange(FIT_ROW_STEP // 2, h, FIT_ROW_STEP)
        n = len(codes)
        maps = [None] * n
        maps[ref_i] = np.zeros((len(rows), w), np.float32)
        cols = np.tile(np.arange(w, dtype=np.float32), (len(rows), 1))
        # walk outwards from the reference, wall by wall: neighbouring walls are close enough that the
        # stripes cannot be confused, and each step's shift carries on to the reference
        order = list(range(ref_i - 1, -1, -1)) + list(range(ref_i + 1, n))
        for i in order:
            nb = i + 1 if i < ref_i else i - 1
            progress(f"Matching the {dists[i]:.0f} mm wall against {dists[nb]:.0f} mm ...")
            f = self._neighbour_field(codes[i], codes[nb], rows)
            # wall_i(x) = wall_nb(x - f(x)); wall_nb(x') = ref(x' - m_nb(x'))
            maps[i] = f + _sample_x(maps[nb], cols - np.nan_to_num(f, nan=-1e6))
        return rows, np.stack(maps), ref_i

    # the fit ------------------------------------------------------------
    def fit(self, picture_width_mm=None, at_mm=None, progress=lambda msg: None):
        """Work out the calibration from the walls. picture_width_mm / at_mm: optionally, how wide the
        picture is on a wall at a measured distance (gives the focal length; otherwise it is guessed from
        Creality's 200 mm capture width). Returns the result dict; raises ValueError when it cannot."""
        if len(self.walls) < 2:
            raise ValueError("Record at least two walls (three or more for a useful fit).")
        rows, maps, ref_i = self.shift_maps(progress)
        progress("Fitting ...")
        z = np.array([w[0] for w in self.walls], np.float64)
        zr = z[ref_i]
        ref = self.walls[ref_i][1]
        h, w = ref.shape
        cols = np.arange(FIT_COL_STEP // 2, w, FIT_COL_STEP)
        sg = maps[:, :, cols].astype(np.float64)                # (n, R, C)
        others = [i for i in range(len(z)) if i != ref_i]
        cover = [float(np.isfinite(maps[i]).mean()) for i in others]
        if max(cover) < 0.05:
            raise ValueError("The stripe pattern could not be followed from wall to wall. Was the projector lit "
                             "(camera A running, the red glow on), the wall plain and flat, and the walls at "
                             "different distances?")

        rr, cc = np.meshgrid(rows, cols, indexing="ij")
        u, vv = (cc - w / 2) / (w / 2), (rr - h / 2) / (h / 2)
        basis = np.stack([np.ones_like(u), u, vv, u * u, u * vv, vv * vv], -1)
        use = np.ones(len(z), bool)

        def surface_fit(c, use):
            """K as one smooth surface over the picture, all walls together: (cost, p, per-wall residual)."""
            g = 1.0 / (z + c) - 1.0 / (zr + c)
            A, y, wid = [], [], []
            for i in np.flatnonzero(use):
                if i == ref_i:
                    continue
                v = np.isfinite(sg[i])
                A.append(basis[v] * g[i])
                y.append(sg[i][v])
                wid.append(np.full(int(v.sum()), i))
            if not A:
                return 1e9, None, {}
            A, y, wid = np.vstack(A), np.concatenate(y), np.concatenate(wid)
            p = np.linalg.lstsq(A, y, rcond=None)[0]
            for _ in range(2):                          # strays (wrong stripe) left out
                r = y - A @ p
                keep = np.abs(r) < max(3.0, 3 * float(np.median(np.abs(r))))
                if keep.sum() > 12:
                    p = np.linalg.lstsq(A[keep], y[keep], rcond=None)[0]
            r = y - A @ p
            cost = float(np.minimum(r * r, 9.0).mean())
            per = {int(i): float(np.median(np.abs(r[wid == i]))) for i in np.unique(wid)}
            return cost, p, per

        def best_c(use):
            if use.sum() < 3:
                return 0.0
            best = None
            for c in np.arange(-80.0, 120.5, 2.0):
                if np.any(z + c <= 10):
                    continue
                cost = surface_fit(c, use)[0]
                if best is None or cost < best[0]:
                    best = (cost, c)
            c0 = best[1]
            for c in np.arange(c0 - 2.0, c0 + 2.01, 0.5):
                if np.all(z + c > 10):
                    cost = surface_fit(c, use)[0]
                    if cost < best[0]:
                        best = (cost, c)
            return best[1]

        # a wall that does not agree with the others (at an angle, not flat, wrong distance typed) is left
        # out, one at a time, while at least three walls stay
        left_out = []
        c = best_c(use)
        while use.sum() > 3:
            _cost, _p, per = surface_fit(c, use)
            if len(per) < 2:
                break
            worst = max(per, key=per.get)
            rest = [v for k, v in per.items() if k != worst]
            if per[worst] > max(1.5, 3.0 * float(np.median(rest))):
                use[worst] = False
                left_out.append(worst)
                c = best_c(use)
            else:
                break
        if len(z) < 3:
            c = 0.0
        sg_use = np.where(use[:, None, None], sg, np.nan)

        def solve_k(c):
            g = 1.0 / (z + c) - 1.0 / (zr + c)                  # per wall
            gg = g[:, None, None] * np.ones_like(sg_use)
            v = np.isfinite(sg_use)
            num = np.where(v, gg * np.nan_to_num(sg_use), 0).sum(0)
            den = np.where(v, gg * gg, 0).sum(0)
            k = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
            res = np.where(v, sg_use - gg * k[None], np.nan)
            return k, res

        kc, res = solve_k(c)
        # smooth K map: robust quadratic surface, plus the cells' own differences, smoothed
        good = np.isfinite(kc) & (np.nanmax(np.abs(np.nan_to_num(res)), axis=0) < 4.0)
        if good.sum() < 12:
            good = np.isfinite(kc)
        if good.sum() < 6:
            raise ValueError("Too little of the picture matched between the walls to fit. Record the walls again, "
                             "square on, with the whole picture on the wall.")
        p = np.linalg.lstsq(basis[good], kc[good], rcond=None)[0]
        for _ in range(2):
            dev = kc - basis @ p
            keep = good & (np.abs(dev) < 3 * np.nanmedian(np.abs(dev[good])) + 1e-6)
            if keep.sum() >= 6:
                p = np.linalg.lstsq(basis[keep], kc[keep], rcond=None)[0]
        smooth = basis @ p
        dev = np.where(good, kc - smooth, np.nan)
        dev[np.abs(dev) > 0.15 * np.abs(smooth)] = np.nan       # a cell that far off is a stray, not the lens
        # local correction: median of each cell's 5x5 neighbourhood of differences (holes stay on the surface)
        R, C = dev.shape
        pad = np.pad(dev, 2, constant_values=np.nan)
        stack = np.stack([pad[i:i + R, j:j + C] for i in range(5) for j in range(5)])
        with np.errstate(all="ignore"):
            cnt = np.isfinite(stack).sum(0)
            loc = np.where(cnt >= 6, np.nanmedian(np.where(cnt[None] >= 6, stack, 0.0), axis=0), 0.0)
        kmap = smooth + np.clip(np.nan_to_num(loc), -0.08 * np.abs(smooth), 0.08 * np.abs(smooth))
        # the surface is only trusted as far as the measured cells reach: no wild values in unmatched corners
        klo, khi = np.percentile(kc[good], [2, 98])
        kmap = np.clip(kmap, klo - 0.1 * abs(klo), khi + 0.1 * abs(khi)).astype(np.float32)
        k_mid = float(np.median(kmap))
        if abs(k_mid) < 100.0:
            raise ValueError("The stripe pattern did not move between the walls. Were the distances different, "
                             "and was the projector lit (camera A, red glow)?")
        # per wall: depth the fit gives back on the fit grid
        kmap_full_rows = np.stack([np.interp(np.arange(w), cols, kmap[i]) for i in range(R)])
        rows_out = []
        for i, zi in enumerate(z):
            s = maps[i]
            with np.errstate(all="ignore"):
                zf = kmap_full_rows / (s + kmap_full_rows / (zr + c)) - c
            e = (zf - zi)[np.isfinite(zf)]
            if i == ref_i:
                rows_out.append(dict(distance_mm=zi, reference=True, matched=1.0, error_mm=0.0, spread_mm=0.0,
                                     tilt_mm=0.0, shift_px=0.0))
                continue
            if e.size < 20:
                rows_out.append(dict(distance_mm=zi, reference=False, matched=round(float(np.isfinite(s).mean()), 3),
                                     error_mm=None, spread_mm=None, tilt_mm=None, shift_px=None))
                continue
            med = float(np.median(e))
            spread = float(1.4826 * np.median(np.abs(e - med)))
            third = w // 3
            el, er = zf[:, :third] - zi, zf[:, -third:] - zi
            el, er = el[np.isfinite(el)], er[np.isfinite(er)]
            tilt = float(np.median(er) - np.median(el)) if el.size > 10 and er.size > 10 else 0.0
            rows_out.append(dict(distance_mm=zi, reference=False, matched=round(float(np.isfinite(s).mean()), 3),
                                 error_mm=round(med, 2), spread_mm=round(spread, 2), tilt_mm=round(tilt, 2),
                                 shift_px=round(float(np.nanmedian(s)), 1)))
        for i, row in enumerate(rows_out):
            row["distance_mm"] = float(row["distance_mm"])
            row["used"] = bool(use[i])
        rv = res[np.isfinite(res)]
        rms = float(np.sqrt(np.mean(np.minimum(rv * rv, 25.0)))) if rv.size else 0.0
        if picture_width_mm and at_mm:
            f = w * (at_mm + c) / float(picture_width_mm)
            f_source = f"measured: picture {picture_width_mm:.0f} mm wide at {at_mm:.0f} mm"
        else:
            f = w * (SPEC_CAPTURE_AT_MM + c) / SPEC_CAPTURE_WIDTH_MM
            f_source = ("GUESSED from Creality's 200 mm capture width, assumed at 300 mm. Measure the picture's "
                        "width on a wall to replace it.")
        lo, hi = WORKING_RANGE_MM
        kmin, kmax = float(kmap.min()), float(kmap.max())
        cands = [k * (1.0 / (zz + c) - 1.0 / (zr + c)) for k in (kmin, kmax) for zz in (lo, hi) if zz + c > 1]
        self.fields = (rows, maps, ref_i)
        self.result = dict(
            version=CALIB_VERSION, module=MODULE_VERSION, mode=self.mode, width=w, height=h, axis="x",
            model="stripe code, K map", k_px_mm=k_mid, k_range_px_mm=[kmin, kmax], c_mm=c, ref_mm=float(zr),
            rms_px=rms, focal_px=f, focal_source=f_source,
            shift_range_px=[float(min(cands)), float(max(cands))], walls=rows_out,
            made=time.strftime("%Y-%m-%d %H:%M"), baseline_mm_if_focal_right=abs(k_mid) / f,
            map_rows=[int(x) for x in rows], map_cols=[int(x) for x in cols],
            _k_map=kmap)
        self.reference = ref
        # self-check: run every wall picture through the depth solver, exactly as live pictures will be
        progress("Checking depth on every wall ...")
        solver = DepthSolver(self.result, ref)
        solver.track = False
        for (zi, pat), row in zip(self.walls, rows_out):
            zz, _ = solver.solve_rows(np.clip(pat * 40 + 128, 0, 255).astype(np.uint8))
            okz = zz > 0
            row["depth_cover"] = round(float(okz.mean()), 3)
            row["depth_mm"] = round(float(np.median(zz[okz])), 1) if okz.sum() > 20 else None
        return self.result

    def summary_lines(self):
        r = self.result
        if not r:
            return ["No calibration yet."]
        fitted = [x for x in r["walls"] if not x["reference"] and x["error_mm"] is not None and x.get("used", True)]
        worst = max((abs(x["error_mm"]) for x in fitted), default=0.0)
        lines = [f"{len(r['walls'])} walls, reference at {r['ref_mm']:.0f} mm. The stripes slide along x.",
                 f"Fit: K {r['k_range_px_mm'][0]:.0f} to {r['k_range_px_mm'][1]:.0f} px·mm across the picture, "
                 f"optical centre {r['c_mm']:.0f} mm from the glass, {r['rms_px']:.2f} px RMS; "
                 f"worst wall {worst:.1f} mm off.",
                 f"Focal length {r['focal_px']:.0f} px ({r['focal_source'].split(':')[0].split(' from')[0].lower()})."]
        tilts = [x["tilt_mm"] for x in fitted]
        lean = (len(tilts) >= 2 and (all(t > 1.0 for t in tilts) or all(t < -1.0 for t in tilts)))
        if lean:
            far = max(fitted, key=lambda x: x["distance_mm"])
            width = r["width"] * (far["distance_mm"] + r["c_mm"]) / r["focal_px"]
            deg = math.degrees(math.atan2(abs(far["tilt_mm"]), width * 2 / 3))
            side = "right" if far["tilt_mm"] > 0 else "left"
            lines.append(f"Every wall came out deeper on the {side}, more so further away: the scanner was "
                         f"probably turned about {deg:.1f} degrees to the wall. Fine for a rough calibration; "
                         "square up for a better one.")
        for x in r["walls"]:
            if x["reference"]:
                continue
            if not x.get("used", True):
                lines.append(f"Warning: the {x['distance_mm']:.0f} mm wall did not agree with the others (at an angle, "
                             "not flat, or the distance typed wrong) and was left out. Record it again.")
            elif x["error_mm"] is None:
                lines.append(f"Warning: the {x['distance_mm']:.0f} mm wall could not be matched "
                             f"({x['matched'] * 100:.0f}% of the picture). Record it again.")
            elif abs(x["tilt_mm"]) > TILT_WARN_MM and not lean:
                lines.append(f"Warning: the {x['distance_mm']:.0f} mm wall came out {abs(x['tilt_mm']):.1f} mm deeper "
                             f"on one side: it may have been at an angle. Square up to the wall and record it again.")
            elif x["spread_mm"] > SPREAD_WARN_MM:
                lines.append(f"Warning: the {x['distance_mm']:.0f} mm wall spreads {x['spread_mm']:.1f} mm: "
                             "not flat, or not square on. Record it again.")
        checked = [x for x in r["walls"] if "depth_cover" in x]
        if checked:
            lines.append("Depth check, each wall read back as a live picture: " + ", ".join(
                f"{x['distance_mm']:.0f} mm -> " + (f"{x['depth_mm']:.0f} mm ({x['depth_cover'] * 100:.0f}% of the view)"
                                                   if x["depth_mm"] is not None else "no depth")
                for x in checked) + ".")
            weak = [x for x in checked if x["depth_cover"] < 0.2 or x["depth_mm"] is None
                    or abs(x["depth_mm"] - x["distance_mm"]) > 5]
            if weak:
                lines.append("Warning: depth is weak or wrong on " + ", ".join(f"{x['distance_mm']:.0f}"
                             for x in weak) + " mm. Record those walls again (square on, still, plain wall).")
        if len(r["walls"]) < MIN_WALLS:
            lines.append("Only two walls: the optical centre could not be fitted. Add a third.")
        return lines

    # saving / loading -------------------------------------------------
    def save(self, serial, folder=None):
        """Everything for this calibration in one folder, ready to zip: calibration.json, reference.npz
        (wall patterns, K map), calibration_report.txt and walls/ (the camera's own pictures)."""
        if not self.result:
            raise ValueError("nothing to save: fit() first")
        folder = folder or calibration_folder(serial)
        os.makedirs(folder, exist_ok=True)
        pub = {k: v for k, v in self.result.items() if not k.startswith("_")}
        with open(os.path.join(folder, "calibration.json"), "w") as f:
            json.dump(dict(pub, serial=serial), f, indent=2)
        np.savez_compressed(os.path.join(folder, "reference.npz"), reference=self.reference,
                            walls=np.array([w[0] for w in self.walls]),
                            patterns=np.stack([w[1] for w in self.walls]).astype(np.float16),
                            k_map=self.result["_k_map"], map_rows=np.array(self.result["map_rows"]),
                            map_cols=np.array(self.result["map_cols"]))
        self.save_walls(folder)
        if self.fields is not None:
            rows, maps, _ref_i = self.fields
            np.savez_compressed(os.path.join(folder, "shift_maps.npz"), rows=rows, maps=maps.astype(np.float16),
                                walls=np.array([w[0] for w in self.walls]))
        with open(os.path.join(folder, "calibration_report.txt"), "w") as f:
            f.write(f"CR-Scan Lizard wall calibration (rough), {self.result['made']}\n")
            f.write(f"Scanner {serial}, mode {self.mode}, lizard_camera {MODULE_VERSION}\n\n")
            f.write("\n".join(self.summary_lines()) + "\n\n")
            f.write("distance_mm  matched  shift_px  error_mm  spread_mm  tilt_mm   (depth check below)\n")
            for x in self.result["walls"]:
                if x["reference"]:
                    f.write(f"{x['distance_mm']:11.1f}   (reference wall)\n")
                elif not x.get("used", True) and x["error_mm"] is not None:
                    f.write(f"{x['distance_mm']:11.1f} {x['matched'] * 100:7.0f}% {x['shift_px']:9.1f}"
                            f" {x['error_mm']:9.2f} {x['spread_mm']:10.2f} {x['tilt_mm']:8.2f}   (LEFT OUT)\n")
                elif x["error_mm"] is None:
                    f.write(f"{x['distance_mm']:11.1f} {x['matched'] * 100:7.0f}%   (not matched)\n")
                else:
                    f.write(f"{x['distance_mm']:11.1f} {x['matched'] * 100:7.0f}% {x['shift_px']:9.1f}"
                            f" {x['error_mm']:9.2f} {x['spread_mm']:10.2f} {x['tilt_mm']:8.2f}\n")
            f.write("\nerror_mm: median depth the fit gives back for that wall, minus the tape measure.\n"
                    "spread_mm: how much that wall's depth varies over the picture (robust).\n"
                    "tilt_mm: right third minus left third (a wall at an angle shows here).\n")
        return folder


def zip_calibration(folder, out_path=None, hide_serial=True, extra_files=()):
    """One zip of a calibration folder, to send or keep. hide_serial: the scanner's serial is masked in
    the folder name inside the zip and in calibration.json. Returns the zip's path."""
    import zipfile
    name = os.path.basename(os.path.normpath(folder))
    shown = mask_serial(name) if hide_serial else name
    out_path = out_path or os.path.join(os.path.dirname(os.path.normpath(folder)),
                                        f"calibration_{shown}_{time.strftime('%Y%m%d_%H%M')}.zip")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, _dirs, files in os.walk(folder):
            for fn in sorted(files):
                path = os.path.join(base, fn)
                rel = os.path.join(shown, os.path.relpath(path, folder))
                if fn in ("calibration.json", "calibration_report.txt") and hide_serial:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        text = f.read()
                    zf.writestr(rel, text.replace(name, shown))
                elif fn.endswith(".zip"):
                    continue
                else:
                    zf.write(path, rel)
        for path in extra_files:
            if os.path.exists(path):
                zf.write(path, os.path.join(shown, "extra", os.path.basename(path)))
    return out_path


def mask_serial(serial):
    """CRSL1234567 -> CRSL*******: keeps the model letters, hides the number."""
    s = str(serial or "")
    m = re.match(r"([A-Za-z]{2,6})(\w+)$", s)
    if m and any(ch.isdigit() for ch in m.group(2)):
        return m.group(1) + "*" * len(m.group(2))
    return s


def _safe(serial):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", serial or "unknown") or "unknown"


def calibration_folder(serial):
    return os.path.join(CALIBRATION_DIR, _safe(serial))


def list_calibrations():
    """[(serial folder name, mode)] of every saved calibration."""
    out = []
    for d in sorted(glob.glob(os.path.join(CALIBRATION_DIR, "*", "calibration.json"))):
        try:
            with open(d) as f:
                out.append((os.path.basename(os.path.dirname(d)), json.load(f).get("mode")))
        except (OSError, ValueError):
            pass
    return out


def load_calibration(serial, folder=None):
    """(result dict, reference pattern) or None. An old (version 1, dot pattern) calibration gives None;
    its walls can be refitted with WallCalibration.from_folder()."""
    folder = folder or calibration_folder(serial)
    try:
        with open(os.path.join(folder, "calibration.json")) as f:
            res = json.load(f)
        if res.get("version") != CALIB_VERSION:
            return None
        z = np.load(os.path.join(folder, "reference.npz"))
        ref = z["reference"].astype(np.float32)
        res["_k_map"] = z["k_map"].astype(np.float32)
    except (OSError, ValueError, KeyError):
        return None
    return res, ref


def calibration_needs_refit(serial, folder=None):
    """True when an old-format calibration with saved walls is there (refit it, no new walls needed)."""
    folder = folder or calibration_folder(serial)
    try:
        with open(os.path.join(folder, "calibration.json")) as f:
            v = json.load(f).get("version")
    except (OSError, ValueError):
        return False
    return v != CALIB_VERSION and os.path.exists(os.path.join(folder, "reference.npz"))


class DepthSolver:
    """Depth from one picture: its stripe code matched against the reference wall's along x, on every
    MATCH_ROW_STEP-th row and every column, then the K map turns each shift into a distance."""

    def __init__(self, calib, reference, min_score=MATCH_MIN_SCORE, min_margin=MATCH_MIN_MARGIN,
                 row_step=MATCH_ROW_STEP, window=None):
        self.calib, self.min_score, self.min_margin = calib, min_score, min_margin
        self.c, self.zr = calib["c_mm"], calib["ref_mm"]
        self.ref = stripe_code(reference)
        h, w = self.ref.shape
        self.rows = np.arange(row_step // 2, h, row_step)
        self.row_step = row_step
        lo, hi = calib["shift_range_px"]
        self.shifts = np.arange(int(math.floor(lo)) - 3, int(math.ceil(hi)) + 4)
        # K for every column of the solve rows, from the calibration's coarse map
        kmap = calib.get("_k_map")
        mr, mc = np.asarray(calib.get("map_rows", [h // 2])), np.asarray(calib.get("map_cols", [w // 2]))
        if kmap is None:
            kmap = np.full((len(mr), len(mc)), float(calib["k_px_mm"]), np.float32)
        kmap = np.asarray(kmap, np.float64)
        by_col = np.stack([np.interp(np.arange(w), mc, kmap[i]) for i in range(kmap.shape[0])])   # (R0, W)
        ri = np.interp(self.rows, mr, np.arange(len(mr), dtype=np.float64))
        i0 = np.clip(np.floor(ri).astype(int), 0, len(mr) - 1)
        i1 = np.clip(i0 + 1, 0, len(mr) - 1)
        f = (ri - i0)[:, None]
        self.k = by_col[i0] * (1 - f) + by_col[i1] * f
        self.last_ms = 0.0
        self.last_full = True
        self.track = True
        self._n = 0
        self._range = None
        self._full_cover = 0.0

    def solve_rows(self, grey, dark=None, full=None):
        """(depth (R, W) mm from the front glass, 0 = unknown; score (R, W)) on the solve rows.
        Tracking: after a full search, the next pictures only search the shifts that were seen (plus a
        margin), which is several times quicker; every TRACK_FULL_EVERY-th picture searches everything again,
        so something new coming closer is found within a second or so. full=True forces a full search."""
        t0 = time.time()
        self._n += 1
        if full is None:
            full = not (self.track and self._range is not None and self._n % TRACK_FULL_EVERY)
        shifts = self.shifts
        if not full:
            lo, hi = self._range
            shifts = self.shifts[(self.shifts >= lo) & (self.shifts <= hi)]
            if len(shifts) < 2 * RIVAL_GAP + 4:
                shifts, full = self.shifts, True
        code = code_image(grey, dark)
        sc = match_rows(code, self.ref, shifts, self.rows)
        shift, top, margin, ok = best_match(sc, shifts, self.min_score, self.min_margin if full else 0.0)
        with np.errstate(all="ignore"):
            z = self.k / (shift + self.k / (self.zr + self.c)) - self.c
        lo_mm, hi_mm = WORKING_RANGE_MM
        ok &= np.isfinite(z) & (z > lo_mm * 0.6) & (z < hi_mm * 1.4)
        ok &= _agrees_with_neighbours(shift, ok)
        cover = float(ok.mean())
        if full:
            if cover > 0.03:
                seen = shift[ok]
                self._range = (float(np.percentile(seen, 1)) - TRACK_MARGIN_PX,
                               float(np.percentile(seen, 99)) + TRACK_MARGIN_PX)
                self._full_cover = cover
            else:
                self._range = None
        elif cover < 0.5 * self._full_cover:
            self._range = None                   # the view changed: search everything next time
        z = np.where(ok, z, 0.0).astype(np.float32)
        if DEPTH_MEDIAN:
            z = _median3(z)
        self.last_full = full
        self.last_ms = (time.time() - t0) * 1000.0
        return z, top.astype(np.float32)

    def solve(self, grey, dark=None):
        """depth (float32 mm from the front glass, 0 = unknown) and score, at working size."""
        z, top = self.solve_rows(grey, dark)
        h = self.ref.shape[0]
        idx = np.clip(np.rint((np.arange(h) - self.rows[0]) / self.row_step).astype(int), 0, len(self.rows) - 1)
        return np.ascontiguousarray(z[idx]), np.ascontiguousarray(top[idx])


CONSIST_RX = 15                 # a point's shift is compared with its neighbours 15 columns either side ...
CONSIST_RY = 1                  # ... and one solve row above and below
CONSIST_TOL_PX = 1.5            # more than this from their median: a wrong stripe (shows as a vertical streak)
CONSIST_MIN = 0.35              # and at least this share of the neighbours must have depth


def _agrees_with_neighbours(shift, ok):
    """Keep only points whose shift agrees with the median of their neighbourhood. A match on the wrong
    stripe is off by a whole stripe spacing (about 6 px) and, with tall windows, comes in thin vertical
    streaks; real surfaces change smoothly, so their neighbours agree."""
    R, W = shift.shape
    vals = np.where(ok, shift, np.nan).astype(np.float32)
    offs = [(dy, dx) for dy in range(-CONSIST_RY, CONSIST_RY + 1)
            for dx in range(-CONSIST_RX, CONSIST_RX + 1, 3)]
    pad = np.pad(vals, ((CONSIST_RY, CONSIST_RY), (CONSIST_RX, CONSIST_RX)), constant_values=np.nan)
    stack = np.stack([pad[CONSIST_RY + dy:CONSIST_RY + dy + R, CONSIST_RX + dx:CONSIST_RX + dx + W]
                      for dy, dx in offs])
    n = np.isfinite(stack).sum(0)
    with np.errstate(all="ignore"):
        med = np.nanmedian(np.where(n[None] > 0, stack, 0.0), axis=0)
    return ok & (n >= CONSIST_MIN * len(offs)) & (np.abs(shift - med) <= CONSIST_TOL_PX)


def _median3(z):
    """3x3 median that ignores holes (0) as long as most neighbours have depth."""
    h, w = z.shape
    p = np.pad(z, 1)
    stack = np.stack([p[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)])
    valid = stack > 0
    n = valid.sum(0)
    s = np.where(valid, stack, np.nan)
    s[:, n == 0] = 0.0                    # no all-empty neighbourhoods: nanmedian would warn about them
    med = np.nanmedian(s, axis=0)
    return np.where((z > 0) & (n >= 5), med, 0.0).astype(np.float32)


def depth_to_points(depth_mm, focal_px, centre_mm=0.0, step=1):
    """Points (N x 3, mm) for every pixel with depth. depth_mm is measured from the front glass; the
    lens centre sits centre_mm behind it (the calibration's c_mm), which sets the sideways scale.
    Axes: x right, y down, z forward; z is reported from the front glass."""
    h, w = depth_mm.shape
    v, u = np.mgrid[0:h:step, 0:w:step]
    z = depth_mm[::step, ::step]
    ok = z > 0
    zc = z[ok] + centre_mm
    x = (u[ok] - w / 2 + 0.5) * zc / focal_px
    y = (v[ok] - h / 2 + 0.5) * zc / focal_px
    return np.stack([x, y, z[ok]], 1).astype(np.float32)


def write_ply(path, points, grey=None):
    """ASCII-free binary PLY of points (mm), with grey colour when given."""
    n = len(points)
    with open(path, "wb") as f:
        head = ["ply", "format binary_little_endian 1.0", f"element vertex {n}",
                "property float x", "property float y", "property float z"]
        if grey is not None:
            head += ["property uchar red", "property uchar green", "property uchar blue"]
        f.write(("\n".join(head + ["end_header"]) + "\n").encode())
        if grey is None:
            f.write(np.asarray(points, "<f4").tobytes())
        else:
            g = np.asarray(grey, np.uint8)
            rec = np.zeros(n, dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
            rec["x"], rec["y"], rec["z"] = points[:, 0], points[:, 1], points[:, 2]
            rec["r"] = rec["g"] = rec["b"] = g
            f.write(rec.tobytes())


# ===========================================================================
# Sessions (recordings)
# ===========================================================================

SESSION_FIELDS = ["index", "t", "camera", "lit", "level", "detail", "size", "file"]


def load_session_info(folder):
    try:
        with open(os.path.join(folder, "session.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def read_session_rows(folder):
    with open(os.path.join(folder, "frames.csv"), newline="") as f:
        return list(csv.DictReader(f))


class SessionRecorder:
    """Writes every frame of the running cameras to a folder: the camera's own JPEG bytes when it sends
    MJPEG (no quality loss), otherwise PNG. frames.csv lists each frame; session.json holds the settings."""

    def __init__(self, folder, info):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "session.json"), "w") as f:
            json.dump(info, f, indent=2)
        self._csv = open(os.path.join(folder, "frames.csv"), "w", newline="")
        self._w = csv.DictWriter(self._csv, fieldnames=SESSION_FIELDS)
        self._w.writeheader()
        self._lock = threading.Lock()
        self.count = 0
        self.t0 = time.time()
        self.closed = False

    def add(self, frame):
        with self._lock:
            if self.closed:
                return
            sub = os.path.join(self.folder, frame.cam)
            os.makedirs(sub, exist_ok=True)
            name = f"{frame.cam}/{self.count:06d}" + (".jpg" if frame.jpeg else ".png")
            path = os.path.join(self.folder, name)
            if frame.jpeg:
                with open(path, "wb") as f:
                    f.write(frame.jpeg)
            else:
                Image.fromarray(frame.grey).save(path, compress_level=1)
            self._w.writerow(dict(index=self.count, t=f"{frame.t - self.t0:.4f}", camera=frame.cam,
                                  lit=int(bool(frame.lit)), level=f"{frame.level:.2f}",
                                  detail=f"{frame.detail:.3f}", size=frame.size, file=name))
            self.count += 1

    def close(self):
        with self._lock:
            self.closed = True
            self._csv.close()


# ===========================================================================
# The camera object for Scantastic (same shape as its RealSense and Kinect cameras)
# ===========================================================================

def _intrinsic(w, h, f):
    """open3d's PinholeCameraIntrinsic when open3d is there (Scantastic), else a plain tuple."""
    try:
        import open3d as o3d
        return o3d.camera.PinholeCameraIntrinsic(w, h, f, f, w / 2 - 0.5, h / 2 - 0.5)
    except Exception:
        return (w, h, f, f, w / 2 - 0.5, h / 2 - 0.5)


class LizardCamera:
    """Scantastic's camera contract:
        cam.start(log=print)                  raises CameraError on failure
        color, depth, intrinsic = cam.get_frame()
            color: uint8 HxWx3 RGB (the infrared picture, grey)
            depth: uint16 HxW in units of depth_scale metres (0 = none), or None without a calibration
            intrinsic: open3d PinholeCameraIntrinsic (or a tuple without open3d), None without a calibration
            (None, None, None) when no frame was ready.
        cam.depth_scale, cam.guide_range, cam.near_m / cam.far_m, cam.device_info, cam.family, cam.serial, cam.stop()
    """
    family = "Lizard"
    depth_scale = 0.0001                       # 0.1 mm units
    guide_range = (0.18, 0.35)                 # the colours of Scantastic's distance guide (m)

    def __init__(self, backend=None, mode=DEFAULT_MODE, require_depth=False):
        # LIZARD_BACKEND=sim runs the simulated scanner inside any host app (handy for trying Scantastic).
        self.backend = backend or os.environ.get("LIZARD_BACKEND", "auto")
        self.mode = mode
        self.require_depth = require_depth
        self.lizard = None
        self.tuner = None
        self.solver = None
        self.calib = None
        self.serial = ""
        self.device_info = "CR-Scan Lizard"
        # measured from the lens centre (a little behind the glass), so slightly wider than Creality's 150-400 mm
        self.near_m, self.far_m = 0.12, 0.45
        self.has_depth = False
        self._last = -1
        self.last_raw_depth = None
        # Attributes Scantastic reads from every camera; the Lizard has no use for them.
        self.last_masked = None
        self.last_ir = None
        self.native_K = None
        self.want_ir = False                    # the Lizard picture is infrared anyway

    def set_range(self, near_m, far_m):
        self.near_m, self.far_m = float(near_m), float(far_m)

    def start(self, log=print):
        self.lizard = Lizard(self.backend)
        if self.backend == "sim":
            self.lizard.sim_scene = SimScene(330.0, ball=(0.0, 10.0, 230.0, 45.0))
        if not self.lizard.connected("A"):
            raise CameraError("No CR-Scan Lizard found (USB bcdc:000a). Is it plugged in?")
        self.serial = self.lizard.serial() or "unknown"
        self.device_info = f"CR-Scan Lizard ({self.serial})"
        try:
            self.lizard.start("A", self.mode)
        except Exception as exc:
            raise CameraError(f"camera A would not start: {exc}") from exc
        self.tuner = Tuner(self.lizard, "A")
        self.tuner.apply_preset("Normal")
        loaded = load_calibration(self.serial)
        if loaded and loaded[0].get("mode") == self.mode:
            self.calib, ref = loaded
            self.solver = DepthSolver(self.calib, ref)
            self.has_depth = True
            log(f"Lizard {self.serial}: wall calibration from {self.calib.get('made', '?')} loaded: depth on.")
        else:
            self.has_depth = False
            if self.require_depth:
                self.lizard.stop_all()
                raise CameraError(f"This Lizard ({self.serial}) has no wall calibration for {self.mode} yet. "
                                  "Run WALL TEST in Lizard Scanner once (about five minutes), then start again.")
            log(f"Lizard {self.serial}: no wall calibration yet: pictures only. Run the wall test in Lizard Scanner.")

    def get_frame(self):
        s = self.lizard.stream("A") if self.lizard else None
        if s is None:
            raise CameraError(self.lizard.streams.get("A").error if self.lizard and self.lizard.streams.get("A")
                              else "camera A is not running")
        f = s.wait_frame(self._last, lit=True, timeout=2.0)
        if f is None:
            return None, None, None
        self._last = f.index
        color = np.repeat(f.grey[..., None], 3, axis=2)
        if self.solver is None:
            return color, None, None
        z, _score = self.solver.solve(f.grey)
        # The pinhole model the host uses measures from the lens centre, c_mm behind the front glass.
        z = np.where(z > 0, z + self.calib["c_mm"], 0)
        z = np.where((z >= self.near_m * 1000) & (z <= self.far_m * 1000), z, 0)
        depth = np.clip(np.rint(z / (self.depth_scale * 1000.0)), 0, 65535).astype(np.uint16)
        self.last_raw_depth = depth
        h, w = depth.shape
        return color, depth, _intrinsic(w, h, self.calib["focal_px"])

    def apply_settings(self, log=print):
        pass

    def stop(self):
        if self.lizard is not None:
            self.lizard.stop_all()


def make_camera(require_depth=True):
    """For Scantastic: a camera that refuses to start without a wall calibration, because its scan
    pipeline needs depth in every frame."""
    return LizardCamera(require_depth=require_depth)


def driver_status():
    """Like kinect_camera.driver_status(): {name: (ok, why)}."""
    out = {}
    if sys.platform.startswith("linux"):
        import shutil
        out["v4l2-ctl"] = (shutil.which("v4l2-ctl") is not None, "sudo apt install v4l-utils")
    else:
        try:
            import cv2  # noqa: F401
            out["opencv"] = (True, "")
        except ImportError:
            out["opencv"] = (False, "pip install opencv-python")
    return out


def diagnose_no_camera():
    d = detect()
    if d is None:
        return "Looking for the Lizard is only automatic on Linux; elsewhere the app tries each camera."
    if not d:
        return ("No CR-Scan Lizard on USB (looked for bcdc:000a / bcdc:000b). Plug it straight into the computer "
                "with its own cable; a hub can starve it of power.")
    return "Found: " + ", ".join(d)
