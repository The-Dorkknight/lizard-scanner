#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# lizard_diag.py -- quick checks of a CR-Scan Lizard, its USB link and its calibration
# https://github.com/The-Dorkknight/lizard-scanner
#
# WORK IN PROGRESS: MANY FEATURES MAY BE BROKEN. Only run against the simulated scanner so far.
#
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
#
"""
Every check is a plain function that returns a Result and never touches a window, so the
app, Scantastic or a script can run it. None of them writes anything to the scanner except
standard camera settings (and the serial read Creality's app also sends), and every check
that changes a setting puts it back.

    from lizard_diag import run_quick_checks, save_report
    results = run_quick_checks(lizard, tuner, mode)
    save_report(results, "reports")
"""

import collections
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile

import numpy as np

import lizard_camera as lc

DIAG_VERSION = "0.5.2-wip"
EXPECTED_FPS = {"mjpg": 10.0, "yuyv": 5.0}


class Result(dict):
    """ok: True (fine), False (a problem), None (could not tell / skipped). summary: one line.
    advice: what to do about it (may be empty). details: anything else, saved in the report."""

    def __init__(self, name, ok, summary, advice="", **details):
        super().__init__(name=name, ok=ok, summary=summary, advice=advice, details=details,
                         when=time.strftime("%Y-%m-%d %H:%M:%S"))

    @property
    def mark(self):
        return {True: "OK", False: "PROBLEM", None: "--"}[self["ok"]]


def _skip(name, why):
    return Result(name, None, why)


# ---------------------------------------------------------------------------
# Computer and USB
# ---------------------------------------------------------------------------

def system_info(app_version=""):
    """What the report needs to know about this computer."""
    info = dict(app=app_version, lizard_camera=lc.MODULE_VERSION, diag=DIAG_VERSION,
                python=sys.version.split()[0], platform=platform.platform(), machine=platform.machine(),
                cpus=os.cpu_count(), numpy=np.__version__)
    try:
        import PIL
        info["pillow"] = PIL.__version__
    except Exception:
        pass
    if shutil.which("v4l2-ctl"):
        try:
            out = subprocess.run(["v4l2-ctl", "--version"], capture_output=True, text=True, timeout=5).stdout
            info["v4l2_ctl"] = out.strip().splitlines()[0] if out.strip() else "present"
        except Exception:
            info["v4l2_ctl"] = "present"
    try:
        with open("/proc/cpuinfo") as f:
            m = re.search(r"model name\s*:\s*(.+)", f.read())
            if m:
                info["cpu"] = m.group(1).strip()
    except OSError:
        pass
    return info


def _driver(node):
    try:
        name = os.path.basename(node)
        return os.path.basename(os.path.realpath(f"{lc.SYS_ROOT}/class/video4linux/{name}/device/driver"))
    except Exception:
        return ""


def usb_check(lizard):
    """Both cameras there, at USB 2 speed, on the uvcvideo driver, and not behind an extra hub."""
    name = "USB connection"
    kind = lizard.kind() if lizard else "v4l2"
    if kind in ("sim", "replay"):
        return _skip(name, f"Not a real scanner ({kind}): nothing to check.")
    if not sys.platform.startswith("linux"):
        return _skip(name, "Only checked on Linux.")
    cams, problems, advice = {}, [], []
    for letter, vid, pid in lc.LIZARD_CAMS:
        devs = lc.find_devices(vid, pid)
        if not devs:
            cams[letter] = None
            problems.append(f"camera {letter} missing")
            continue
        d = devs[0]
        node = lc.capture_node(d)
        depth = d["name"].split("-", 1)[-1].count(".") + 1 if "-" in d["name"] else 1
        cams[letter] = dict(port=d["name"], speed_mbps=d.get("speed"), node=node, driver=_driver(node) if node else "",
                            product=d.get("product"), hub_levels=depth)
        if str(d.get("speed")) not in ("480", "5000", "10000"):
            problems.append(f"camera {letter} runs at {d.get('speed')} Mbit/s, not USB 2's 480")
            advice.append("Use another USB port or cable: a slow link cannot carry the video.")
        if node is None:
            problems.append(f"camera {letter} has no /dev/video node")
        elif cams[letter]["driver"] and cams[letter]["driver"] != "uvcvideo":
            problems.append(f"camera {letter} is on driver {cams[letter]['driver']}, not uvcvideo")
        if depth > 2:
            problems.append(f"camera {letter} sits behind {depth - 1} hubs (the scanner has one inside)")
            advice.append("Plug the scanner straight into the computer; an extra hub can starve it of power.")
    hub = bool(lc.find_devices(0x1A40, 0x0101))
    if cams.get("A") is None and cams.get("B") is None:
        return Result(name, False, "No CR-Scan Lizard on USB (bcdc:000a / 000b).",
                      "Plug it straight into the computer with its own cable. `lsusb` should list both cameras.",
                      cameras=cams, internal_hub=hub)
    summary = ("Both cameras found at USB 2 speed on uvcvideo." if not problems else "; ".join(problems).capitalize() + ".")
    return Result(name, not problems, summary, " ".join(dict.fromkeys(advice)), cameras=cams, internal_hub=hub)


def formats_and_settings(lizard):
    """Every video mode and camera setting, for the report. Never changes anything."""
    name = "Video modes and settings"
    if lizard is None:
        return _skip(name, "Start the scanner first.")
    text, counts = {}, {}
    for cam in ("A", "B"):
        ctrls = lizard.controls(cam)
        counts[cam] = len(ctrls)
        text[f"controls_{cam}"] = ctrls
        node = lizard.nodes.get(cam)
        if lizard.kind() == "v4l2" and node:
            try:
                r = subprocess.run(["v4l2-ctl", "-d", node, "--list-formats-ext"], capture_output=True, text=True,
                                   timeout=10)
                text[f"formats_{cam}"] = r.stdout
            except Exception as exc:
                text[f"formats_{cam}"] = f"failed: {exc}"
    exp = lc._control(text.get("controls_A", []), "exposure_time_absolute", "exposure_absolute")
    gain = lc._control(text.get("controls_A", []), "gain")
    rng = []
    if exp and "min" in exp:
        rng.append(f"exposure {exp['min']}..{exp['max']} (default {exp.get('default')})")
    if gain and "min" in gain:
        rng.append(f"gain {gain['min']}..{gain['max']} (default {gain.get('default')})")
    summary = f"Camera A: {counts['A']} settings, camera B: {counts['B']}." + (" " + ", ".join(rng) + "." if rng else "")
    return Result(name, True if counts["A"] else None, summary, **text)


def serial_check(lizard):
    name = "Serial number"
    if lizard is None:
        return _skip(name, "Start the scanner first.")
    try:
        s = lizard.serial()
    except Exception as exc:
        return Result(name, False, f"The serial read failed: {exc}", "Replug the scanner and try again.")
    if not s:
        return _skip(name, "No serial on this system (only read on Linux).")
    ok = s.startswith("CRSL") or s in ("SIMULATED",) or lizard.kind() == "replay"
    return Result(name, ok, f"Serial: {s}", "" if ok else "Unexpected answer; the calibration is filed under it anyway.",
                  serial=s)


# ---------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------

def _collect(stream, seconds):
    frames = []
    stream.listeners.append(frames.append)
    try:
        time.sleep(seconds)
    finally:
        stream.listeners.remove(frames.append)
    return frames


def video_check(lizard, cam="A", mode=lc.DEFAULT_MODE, seconds=3.0):
    """Picture rate, gaps, pulsing lights, and frame sizes over a few seconds."""
    name = f"Camera {cam} video"
    if lizard is None or not lizard.connected(cam):
        return _skip(name, f"Camera {cam} not found.")
    started = False
    s = lizard.stream(cam)
    if s is None:
        try:
            s = lizard.start(cam, mode)
            started = True
            time.sleep(0.8)
        except Exception as exc:
            return Result(name, False, f"Camera {cam} would not start: {exc}")
    try:
        frames = _collect(s, seconds)
    finally:
        if started:
            lizard.stop(cam)
    if len(frames) < 3:
        return Result(name, False, f"Only {len(frames)} pictures in {seconds:.0f} s. {s.error}".strip(),
                      "Another program may be using the camera, or the USB link is struggling.")
    t = np.array([f.t for f in frames])
    gaps = np.diff(t)
    fps = (len(frames) - 1) / max(t[-1] - t[0], 1e-6)
    med = float(np.median(gaps))
    late = int((gaps > 1.8 * med).sum())
    pt = lc.PulseTracker(history=len(frames))
    for f in frames:
        pt.add(f)
    pi = pt.info()
    pulsing, ratio = bool(pi["pulsing"]), float(pi["ratio"])
    sizes = [len(f.jpeg) for f in frames if f.jpeg]
    want = EXPECTED_FPS.get(s.mode, 10.0)
    problems, advice = [], []
    if fps < 0.7 * want:
        problems.append(f"{fps:.1f} pictures a second (expected about {want:.0f})")
        advice.append("Close other camera programs; try another USB port.")
    if late:
        problems.append(f"{late} late pictures")
    dots = [pattern_score(f.grey) for f in frames[::3]]
    dots_always = bool(dots and min(dots) > PATTERN_MIN)
    unsure = False
    if cam == "A" and not pulsing:
        if dots_always:
            pass                     # lit in every picture: the projector flashes once per exposure
        elif dots and max(dots) > PATTERN_MIN:
            unsure = True
            advice.append("The stripe pattern is in some pictures but not others. Save the report and send it: "
                          "it holds every picture's numbers.")
        else:
            problems.append("no stripe pattern in the pictures")
            advice.append("Point the scanner at a plain wall 20-30 cm away and press Brighter. If there are still no "
                          "stripes, run the Sensor map: camera A may not be the camera that sees the projector.")
    signal = {"detail": "fine detail", "size": "picture size", "level": "brightness"}.get(pi["signal"], pi["signal"])
    summary = (f"{fps:.1f} pictures a second, {late} late, "
               + (f"lights pulsing, {pi['pattern']} (seen in the {signal}, lit {ratio:.1f}x dark)" if pulsing else
                  "stripe pattern in every picture (the projector flashes once per exposure, no dark pictures)" if dots_always
                  else "no regular pulse")
               + (f", pictures {min(sizes) // 1024}-{max(sizes) // 1024} KB" if sizes else "") + ".")
    if problems:
        summary = "; ".join(problems).capitalize() + ". " + summary
    t0 = frames[0].t
    rows = [(f.index, round(f.t - t0, 4), round(f.level, 2), round(f.detail, 3), f.size,
             int(bool(pt.classify(f)))) for f in frames]
    return Result(name, (None if unsure else not problems) if cam == "A" else (fps >= 0.7 * want),
                  summary, " ".join(advice), fps=round(fps, 2), late=late, pulsing=pulsing,
                  pulse={k: (round(float(v), 3) if isinstance(v, (float, np.floating)) else
                             (bool(v) if isinstance(v, np.bool_) else v)) for k, v in pi.items()},
                  bright_dark_ratio=round(ratio, 2),
                  frame_kb=[min(sizes) // 1024, max(sizes) // 1024] if sizes else None,
                  started_for_check=started, frames_table=rows, dots_every_picture=dots_always,
                  pattern_score=round(float(np.median(dots)), 4) if dots else None)


def camera_b_check(lizard, mode=lc.DEFAULT_MODE, seconds=4.0):
    """Camera B alone: does it stream, is it colour or grey, do its lights pulse."""
    name = "What is camera B?"
    if lizard is None or not lizard.connected("B"):
        return _skip(name, "Camera B not found.")
    a_was_on = lizard.stream("A") is not None
    lizard.stop("A")
    colour = None
    try:
        s = lizard.start("B", mode)
        time.sleep(seconds)
        f = s.latest
        fps, pulse, binfo = s.fps(), s.pulse.state(), s.pulse.info()
        if f is not None and f.jpeg:
            from PIL import Image
            img = Image.open(io.BytesIO(f.jpeg))
            img.draft("RGB", (160, 90))
            rgb = np.asarray(img.convert("RGB"), np.int16)
            colour = float(np.abs(rgb[..., 0] - rgb[..., 2]).mean() + np.abs(rgb[..., 1] - rgb[..., 2]).mean())
        err = s.error
    finally:
        lizard.stop("B")
        if a_was_on:
            lizard.start("A", mode)
    if f is None:
        return Result(name, False, f"Camera B sent no pictures in {seconds:.0f} s. {err}".strip())
    kind = ("colour" if colour is not None and colour > 6 else
            "grey (infrared-looking)" if colour is not None else "grey (this video mode carries no colour)")
    guess = ("probably the texture camera" if kind == "colour" else
             "a grey camera with pulsing light" if pulse[0] else "an unknown grey camera")
    return Result(name, True, f"Camera B: {fps:.1f} pictures a second, {kind}, lights "
                  + (f"pulsing ({binfo['pattern']})" if pulse[0] else "steady") + f": {guess}. Started at a 1 ms "
                  "flash. Did the white ring light while this ran?", fps=round(fps, 2), colour_score=colour,
                  pulsing=pulse[0], mean_level=round(f.level, 1), picture=f.grey)


def pattern_tiles(grey, tiles=(16, 9)):
    """Per tile (9 rows x 16 columns): does it show the projector's vertical stripes? The stripes make
    the picture change much more along x than along y. Returns (bool tiles, x detail, y detail)."""
    g = np.asarray(grey, np.float32)
    hp = g - lc.box_filter(g, 6)
    gx = np.abs(np.diff(hp, axis=1))[:-1]
    gy = np.abs(np.diff(hp, axis=0))[:, :-1]
    h, w = gx.shape
    tx, ty = tiles
    th, tw = h // ty, w // tx
    X = gx[:th * ty, :tw * tx].reshape(ty, th, tx, tw).mean((1, 3))
    Y = gy[:th * ty, :tw * tx].reshape(ty, th, tx, tw).mean((1, 3))
    return (X > STRIPE_MIN_DETAIL) & (X > STRIPE_RATIO * Y), X, Y


def pattern_score(grey):
    """Share of the picture (0..1) showing the projector's stripe pattern: near 1 on a wall at a good
    distance, 0 for an evenly lit (white ring) or dark picture. Real scanner, hand and mug: 0.56."""
    return float(pattern_tiles(grey)[0].mean())


STRIPE_MIN_DETAIL = 2.0         # a tile needs this much detail along x ...
STRIPE_RATIO = 1.6              # ... and this many times more than along y, to count as striped
PATTERN_MIN = 0.25              # pattern_score above this: the picture shows the projector's pattern
dot_score = pattern_score       # older names (the pattern was first thought to be dots)
DOTS_MIN = PATTERN_MIN


def colour_score(frame):
    """How colourful a picture is (0 = grey), from its JPEG; None without one."""
    if not frame.jpeg:
        return None
    from PIL import Image
    img = Image.open(io.BytesIO(frame.jpeg))
    img.draft("RGB", (160, 90))
    rgb = np.asarray(img.convert("RGB"), np.int16)
    return float(np.abs(rgb[..., 0] - rgb[..., 2]).mean() + np.abs(rgb[..., 1] - rgb[..., 2]).mean())


def measure_cameras(lizard, cams, mode=lc.DEFAULT_MODE, seconds=3.0):
    """Run exactly the cameras in `cams` (each starts at a 1 ms flash), collect pictures for a few
    seconds, and describe each: rate, brightness, stripe pattern, colour, pulse. Stops them again afterwards."""
    for c in ("A", "B"):
        lizard.stop(c)
    streams = {}
    try:
        for c in cams:
            streams[c] = lizard.start(c, mode)
        time.sleep(0.8)
        got = {c: [] for c in cams}
        for c, s in streams.items():
            s.listeners.append(got[c].append)
        time.sleep(seconds)
        for c, s in streams.items():
            s.listeners.remove(got[c].append)
        out = {}
        for c, frames in got.items():
            s = streams[c]
            if len(frames) < 2:
                out[c] = dict(fps=0.0, frames=len(frames), error=s.error)
                continue
            t = [f.t for f in frames]
            pt = lc.PulseTracker(history=len(frames))
            for f in frames:
                pt.add(f)
            pi = pt.info()
            dots = [pattern_score(f.grey) for f in frames[::2]]
            lit_dots = [pattern_score(f.grey) for f in frames if pt.classify(f)] or dots
            cs = colour_score(frames[-1])
            out[c] = dict(fps=round((len(frames) - 1) / max(t[-1] - t[0], 1e-6), 2), frames=len(frames),
                          level=round(float(np.mean([f.level for f in frames])), 1),
                          detail=round(float(np.mean([f.detail for f in frames])), 2),
                          dots=round(float(np.max(lit_dots)), 4), dots_every_picture=bool(min(dots) > PATTERN_MIN),
                          colour=None if cs is None else round(cs, 1), pulsing=bool(pi["pulsing"]),
                          pattern=pi["pattern"], picture=frames[-1].grey,
                          sizes_kb=[min(f.size for f in frames) // 1024, max(f.size for f in frames) // 1024]
                          if frames[0].size else None)
        return out
    finally:
        for c in list(streams):
            lizard.stop(c)


def sensor_map_result(phases, answers):
    """phases: {"A": stats, "B": stats, "AB": stats} from measure_cameras; answers: {phase: what the
    person saw} ("White ring", "Red glow", "Both", "Neither", "Couldn't tell")."""
    lines, findings = [], {}
    for key, label in (("A", "Camera A alone"), ("B", "Camera B alone"), ("AB", "Both together")):
        st = phases.get(key)
        if not st:
            continue
        bits = []
        for cam, m in st.items():
            if not m.get("fps"):
                bits.append(f"{cam}: no pictures {m.get('error', '')}".strip())
                continue
            kind = ("colour" if m["colour"] is not None and m["colour"] > 6 else "grey")
            bits.append(f"{cam}: {m['fps']:.1f}/s, {kind}, "
                        + (f"stripes ({m['dots'] * 100:.0f}% of the view)" if m["dots"] > PATTERN_MIN else "no stripes")
                        + (f", pulsing {m['pattern']}" if m["pulsing"] else ""))
        seen = answers.get(key, "not asked")
        lines.append(f"{label}: {'; '.join(bits)}. Light seen: {seen}.")
    a, b, ab = phases.get("A", {}).get("A", {}), phases.get("B", {}).get("B", {}), phases.get("AB", {})
    if answers.get("A") in ("Red glow",) and answers.get("B") in ("White ring",):
        findings["lights"] = "Camera A switches the infrared projector, camera B the white ring."
    if a.get("dots", 0) > PATTERN_MIN and b.get("dots", 0) <= PATTERN_MIN:
        findings["dots"] = "Only camera A sees the stripe pattern: camera A is the depth camera."
    elif a.get("dots", 0) > PATTERN_MIN and ab.get("B", {}).get("dots", 0) > PATTERN_MIN:
        findings["dots"] = ("Camera B sees the stripes too when both run: the two cameras may be a stereo pair "
                            "(the way Creality calls the Lizard 'binocular').")
    elif a.get("dots", 0) <= PATTERN_MIN:
        findings["dots"] = ("Camera A shows NO stripe pattern on its own. Either it does not see the projector "
                            "(wrong filter for it, or the projector needs camera B running), or the picture is too "
                            "dark: try again pointed at a plain wall 20-30 cm away.")
    if a.get("fps") and ab.get("A", {}).get("fps") and ab["A"]["fps"] < 0.7 * a["fps"]:
        findings["rate"] = f"Camera A slows from {a['fps']:.1f} to {ab['A']['fps']:.1f} pictures a second when B runs too."
    if b.get("fps") and a.get("fps") and b["fps"] < 0.7 * a["fps"]:
        findings["b_rate"] = f"Camera B runs slower than A ({b['fps']:.1f} against {a['fps']:.1f} a second)."
    if a.get("fps") and not a.get("pulsing") and a.get("dots_every_picture"):
        findings["pulse"] = ("Camera A's projector is lit in every picture: it flashes once per exposure. The eye "
                             "sees that as flicker, but there are no dark pictures to take away.")
    summary = " ".join(findings.values()) or "Nothing clear; see the table in the report."
    return Result("Sensor map", True if findings else None, summary, "", phases=_strip_pictures(phases),
                  answers=answers, lines=lines, findings=findings,
                  **{f"picture_{k}_{c}": m.get("picture") for k, st in phases.items() for c, m in st.items()
                     if m.get("picture") is not None})


def _strip_pictures(phases):
    return {k: {c: {kk: vv for kk, vv in m.items() if kk != "picture"} for c, m in st.items()}
            for k, st in phases.items()}


# ---------------------------------------------------------------------------
# Light and projector
# ---------------------------------------------------------------------------

def projector_check(lizard, seconds=2.0):
    """Point the scanner at a wall or object. How much of the view shows the stripe pattern, how strong it
    is, how much infrared is in the room, and is anything blown out. The real scanner lights every picture,
    so the room's own infrared is judged from the dark gaps between the stripes (or from dark pictures,
    when a scanner gives some)."""
    name = "Projector and room light"
    s = lizard.stream("A") if lizard else None
    if s is None:
        return _skip(name, "Start the scanner first.")
    frames = _collect(s, seconds)
    lit = [f.grey for f in frames if f.lit]
    dark = [f.grey for f in frames if f.lit is False]
    if len(lit) < 2:
        return Result(name, False, "Not enough pictures arrived.")
    L = np.mean(lit, axis=0)
    D = np.mean(dark, axis=0) if dark else None
    striped, X, _Y = pattern_tiles(L)
    coverage = float(striped.mean())
    contrast = float(np.median(X[striped])) if striped.any() else 0.0
    if D is not None:
        room, room_how = float(D.mean()), "dark pictures"
    else:
        # the floor between stripes: darkest pixel in every 9-pixel run along x, averaged
        h, w = L.shape
        w9 = (w // 9) * 9
        room, room_how = float(L[:, :w9].reshape(h, -1, 9).min(2).mean()), "between the stripes"
    sat = float((np.asarray(lit[-1]) >= 250).mean())
    problems, advice = [], []
    if room > 60 and (D is not None or contrast < 0.15 * room):
        problems.append(f"lots of infrared in the room ({room_how} average {room:.0f})")
        advice.append("Sunlight or a halogen lamp swamps the stripes: close the curtains or move away from the window.")
    if coverage < 0.5:
        problems.append(f"stripe pattern seen in only {coverage * 100:.0f}% of the view")
        advice.append("Move to 18-35 cm from the surface, or press Brighter / AUTO-TUNE.")
    if sat > lc.TUNE_MAX_SATURATED * 3:
        problems.append(f"{sat * 100:.1f}% of the picture blown out")
        advice.append("Press Dimmer, or Shiny / white.")
    summary = (f"Stripe pattern in {coverage * 100:.0f}% of the view, stripe detail {contrast:.1f}, "
               f"room infrared {room:.0f} of 255 ({room_how}), {sat * 100:.1f}% blown out.")
    if problems:
        summary = "; ".join(problems).capitalize() + ". " + summary
    return Result(name, not problems, summary, " ".join(advice), coverage=round(coverage, 3),
                  dot_contrast=round(contrast, 2), room_ir=round(room, 1), room_from=room_how,
                  saturated=round(sat, 4), tiles=X.round(2).tolist(), lit_picture=np.asarray(lit[-1], np.uint8),
                  dark_picture=None if D is None else np.asarray(D, np.uint8))


def sweep(lizard, control, values, hold_s=2.0, cam="A", fixed=None, progress=lambda v: None, cancel=None):
    """Step one setting through `values`, holding each, and measure lit / dark picture brightness.
    fixed: other settings to hold meanwhile. Puts every setting back afterwards. Returns rows
    [(value, lit level, dark level)]."""
    s = lizard.stream(cam)
    if s is None:
        raise lc.CameraError("start the scanner first")
    ctrls = lizard.controls(cam)
    names = {c["name"]: c for c in ctrls}
    keep = {n: c.get("value") for n, c in names.items()
            if n in ("auto_exposure", "exposure_auto", "exposure_time_absolute", "exposure_absolute", "gain")}
    auto = lc._control(ctrls, "auto_exposure", "exposure_auto")
    rows = []
    try:
        if auto is not None:
            lizard.set(cam, auto["name"], 1)
        for n, v in (fixed or {}).items():
            lizard.set(cam, n, v)
        for v in values:
            if cancel is not None and cancel.is_set():
                break
            progress(v)
            lizard.set(cam, control, v)
            idx = s.latest.index if s.latest else -1
            for _ in range(2):                      # let it take
                f = s.wait_frame(idx, timeout=1.0)
                idx = f.index if f else idx
            levels = []
            t_end = time.time() + hold_s
            while time.time() < t_end:
                f = s.wait_frame(idx, timeout=1.0)
                if f is None:
                    break
                idx = f.index
                levels.append(f.level)
            if levels:
                srt = sorted(levels)
                mid = srt[len(srt) // 2]
                hi = [x for x in levels if x >= mid] or [mid]
                lo = [x for x in levels if x < mid] or [mid]
                rows.append((v, float(np.mean(hi)), float(np.mean(lo))))
    finally:
        for n, v in keep.items():
            if v is not None:
                lizard.set(cam, n, v)
    return rows


def exposure_test_values(allow_long=False):
    vals = [1, 2, 3, 5, 7, 10]
    if allow_long:
        vals += [20, 40, lc.LONG_EXPOSURE_MAX]
    return vals


def gain_test_values(controls):
    g = lc._control(controls, "gain")
    lo, hi = (g.get("min", 0), g.get("max", 128)) if g else (0, 128)
    out = []
    for v in (16, 40, 65, 96, 128):
        v = min(max(v, lo), hi)
        if v not in out:
            out.append(v)
    return out


def sweep_summary(rows):
    """Does the picture follow the setting? (slope of lit brightness against the setting, normalised)"""
    if len(rows) < 2:
        return "too few steps"
    v = np.array([r[0] for r in rows], float)
    lit = np.array([r[1] for r in rows], float)
    gain = (lit[-1] - lit[0]) / max(lit[0], 1.0)
    return f"lit pictures went from {lit[0]:.0f} to {lit[-1]:.0f} ({gain * 100:+.0f}%) as it went {v[0]:g} to {v[-1]:g}"


EYE_VERDICTS = {
    "exposure": {"Yes, clearly": "The light follows exposure: exposure is the flash length, as suspected.",
                 "A little": "Exposure affects the light a little.",
                 "No change": "The light does not follow exposure: CR Scan dims it some other way.",
                 "Couldn't tell": "No answer this time; try in a darker room."},
    "gain": {"Yes, clearly": "The light changed with GAIN: the firmware ties the LEDs to gain as well. The 1 ms cap "
                             "alone does not limit brightness; keep gain modest too.",
             "A little": "The light may follow gain a little: treat gain as part of its brightness.",
             "No change": "The light ignores gain: exposure alone sets its brightness, so the 1 ms cap limits it.",
             "Couldn't tell": "No answer this time; try in a darker room."},
}


# ---------------------------------------------------------------------------
# Calibration and depth
# ---------------------------------------------------------------------------

STRAY_MM = 3.0                  # a depth pixel this far off the wall (or more) counts as stray


def fit_plane(points, rounds=3):
    """Plane through N x 3 points, ignoring strays: fit, drop points more than 3.5 robust deviations
    (and at least STRAY_MM) off,
    fit again. Returns (normal, distance along it, RMS of the kept points, residuals of all, share dropped)."""
    # start from the points near the middle distance, so a crowd of strays cannot tip the first plane
    z = points[:, 2]
    keep = np.abs(z - np.median(z)) < max(5 * STRAY_MM, 1.5 * float(np.percentile(np.abs(z - np.median(z)), 75)))
    if keep.sum() < 50:
        keep = np.ones(len(points), bool)
    for _ in range(rounds + 2):
        c = points[keep].mean(axis=0)
        _u, _s, vt = np.linalg.svd(points[keep] - c, full_matrices=False)
        n = vt[2] if vt[2][2] >= 0 else -vt[2]
        res = (points - c) @ n
        mad = float(np.median(np.abs(res[keep] - np.median(res[keep])))) * 1.4826 + 1e-6
        new = np.abs(res - np.median(res[keep])) <= max(3.5 * mad, STRAY_MM)
        if new.sum() < 50 or (new == keep).all():
            keep = new if new.sum() >= 50 else keep
            break
        keep = new
    rms = float(np.sqrt((res[keep] ** 2).mean()))
    return n, float(c @ n), rms, res, float(1 - keep.mean())


def flat_wall_check(lizard, solver, calib, tape_mm=None, pictures=3):
    """Point the scanner straight at a flat wall. How flat does it come out, how far away, and (with a
    tape-measured distance) how far off. The best single test of a calibration."""
    name = "Flat wall check"
    s = lizard.stream("A") if lizard else None
    if s is None:
        return _skip(name, "Start the scanner first.")
    if solver is None or calib is None:
        return _skip(name, "Needs a wall calibration (WALL TEST) first.")
    zs, idx = [], -1
    for _ in range(pictures):
        f = s.wait_frame(idx, lit=True, timeout=2.0)
        if f is None:
            break
        idx = f.index
        z, _score = solver.solve(f.grey)
        zs.append(z)
    if not zs:
        return Result(name, False, "No pictures arrived.")
    stack = np.stack(zs)
    with np.errstate(invalid="ignore"):
        # a pixel counts when most pictures have depth there (holes move between pictures)
        have = (stack > 0).sum(0)
        med = np.nanmedian(np.where(stack > 0, stack, np.nan), axis=0) if have.any() else np.zeros(stack.shape[1:])
        z = np.where(have * 2 > len(zs), np.nan_to_num(med), 0).astype(np.float32)
    ok = z > 0
    cover = float(ok.mean())
    if ok.sum() < 500:
        return Result(name, False, f"Depth in only {cover * 100:.0f}% of the view.",
                      "Check the distance (18-35 cm), the light (AUTO-TUNE) and that a plain wall fills the view.")
    pts = lc.depth_to_points(z, calib["focal_px"], calib["c_mm"], step=2)
    n, _d, rms, res, strays = fit_plane(pts.astype(np.float64))
    tilt = float(np.degrees(np.arccos(min(1.0, abs(n[2])))))
    h, w = z.shape
    centre = z[h // 2 - 20:h // 2 + 20, w // 2 - 20:w // 2 + 20]
    centre_mm = float(np.median(centre[centre > 0])) if (centre > 0).any() else float("nan")
    # bowl / dome: residual against distance from the picture centre (a lens-distortion symptom)
    v, u = np.mgrid[0:h:2, 0:w:2]
    r = np.hypot(u - w / 2, v - h / 2)[ok[::2, ::2]]
    good = np.abs(res - np.median(res)) < 4 * max(rms, 0.2)
    bowl = (float(np.polyfit(r[good] / (w / 2), res[good], 2)[0])
            if len(r) == len(res) and good.sum() > 100 else 0.0)
    problems, advice = [], []
    if rms > 2.0:
        problems.append(f"the wall comes out {rms:.1f} mm rough")
    if strays > 0.1:
        problems.append(f"{strays * 100:.0f}% of the depth is stray (wrong by a lot)")
        advice.append("Try AUTO-TUNE, a plainer wall, or less room light.")
    if abs(bowl) > 1.5:
        problems.append(f"the wall bends {'away' if bowl > 0 else 'towards you'} at the edges by about {abs(bowl):.1f} mm")
        advice.append("That is lens distortion, which the rough calibration does not model; trust the middle of the view more.")
    err = None
    if tape_mm:
        err = centre_mm - float(tape_mm)
        if abs(err) > 3.0:
            problems.append(f"the middle reads {centre_mm:.1f} mm against your {float(tape_mm):.0f} mm")
            advice.append("Record the wall test again, measuring carefully, with five walls over the whole range.")
    summary = (f"Middle of the view at {centre_mm:.1f} mm" + (f" (tape {float(tape_mm):.0f} mm, {err:+.1f} mm)" if err is not None else "")
               + f", wall flat to {rms:.2f} mm RMS ({strays * 100:.0f}% strays left out), tilted {tilt:.1f} degrees, "
               f"depth in {cover * 100:.0f}% of the view.")
    if problems:
        summary = "; ".join(problems).capitalize() + ". " + summary
    return Result(name, not problems, summary, " ".join(advice), centre_mm=round(centre_mm, 2), rms_mm=round(rms, 3),
                  tilt_deg=round(tilt, 2), coverage=round(cover, 3), bowl_mm=round(bowl, 2), strays=round(strays, 3),
                  tape_mm=tape_mm, error_mm=None if err is None else round(err, 2))


def depth_speed_check(lizard, solver, repeats=3):
    name = "Depth speed"
    s = lizard.stream("A") if lizard else None
    if s is None or solver is None:
        return _skip(name, "Needs the scanner running and a wall calibration.")
    f = s.wait_frame(-1, lit=True, timeout=2.0)
    if f is None:
        return Result(name, False, "No picture arrived.")
    full, tracked = [], []
    for _ in range(max(repeats, 1) + 3):
        solver.solve(f.grey)
        (full if getattr(solver, "last_full", True) else tracked).append(getattr(solver, "last_ms", 0.0))
    ms_full = float(np.median(full)) if full else 0.0
    ms_track = float(np.median(tracked)) if tracked else ms_full
    ms = ms_track
    return Result(name, ms < 1000,
                  f"{ms_track:.0f} ms per depth picture while tracking ({1000 / max(ms_track, 1):.1f} a second), "
                  f"{ms_full:.0f} ms for a full search, on this computer.",
                  "" if ms < 1000 else "Slow: the Depth view will lag; scanning in Scantastic will be slow.",
                  ms=round(ms, 1), ms_full=round(ms_full, 1), ms_tracking=round(ms_track, 1))


# ---------------------------------------------------------------------------
# All together
# ---------------------------------------------------------------------------

QUICK = ("usb", "video_a", "settings", "serial", "projector", "depth_speed")


def run_quick_checks(lizard, mode=lc.DEFAULT_MODE, solver=None, progress=lambda name: None):
    """The checks that need no help from you. Point the scanner at a plain surface 20-30 cm away first."""
    out = {}
    for key, title, fn in (("usb", "USB connection", lambda: usb_check(lizard)),
                           ("video_a", "Camera A video", lambda: video_check(lizard, "A", mode)),
                           ("settings", "Video modes and settings", lambda: formats_and_settings(lizard)),
                           ("serial", "Serial number", lambda: serial_check(lizard)),
                           ("projector", "Projector and room light", lambda: projector_check(lizard)),
                           ("depth_speed", "Depth speed", lambda: depth_speed_check(lizard, solver))):
        progress(title)
        try:
            out[key] = fn()
        except Exception as exc:
            out[key] = Result(title, False, f"The check itself failed: {exc}")
    return out


def _jsonable(x):
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.ndarray):
        return f"<picture {x.shape[1]}x{x.shape[0]}>" if x.ndim == 2 else x.tolist()
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def report_text(results, info):
    lines = [f"Lizard Scanner diagnostics, {time.strftime('%Y-%m-%d %H:%M')}", ""]
    for k, v in info.items():
        lines.append(f"{k}: {v}")
    lines.append("")
    for r in results.values():
        lines.append(f"[{r.mark:7s}] {r['name']}: {r['summary']}")
        for extra in r["details"].get("lines", []) or []:
            lines.append(f"           {extra}")
        if r["advice"]:
            lines.append(f"           What to do: {r['advice']}")
    return "\n".join(lines) + "\n"


def save_report(results, folder, info=None, extra_files=(), hide_serial=False):
    """Write report.txt, results.json, the settings dumps and any pictures into one zip in `folder`.
    Returns the zip's path. hide_serial replaces the scanner's serial with CRSL0000000."""
    os.makedirs(folder, exist_ok=True)
    info = info or system_info()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(folder, f"lizard_diagnostics_{stamp}.zip")

    def scrub(text):
        return re.sub(r"CRSL[0-9A-Z]{4,}", "CRSL0000000", text) if hide_serial else text

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.txt", scrub(report_text(results, info)))
        z.writestr("results.json", scrub(json.dumps(_jsonable(dict(info=info, results=results)), indent=2)))
        from PIL import Image
        for key, r in results.items():
            for dk, dv in r["details"].items():
                if isinstance(dv, np.ndarray) and dv.ndim == 2:
                    buf = io.BytesIO()
                    Image.fromarray(np.asarray(dv, np.uint8)).save(buf, "PNG")
                    z.writestr(f"pictures/{key}_{dk}.png", buf.getvalue())
                elif isinstance(dv, str) and dk.startswith("formats_"):
                    z.writestr(f"settings/{dk}.txt", dv)
                elif dk == "frames_table" and dv:
                    z.writestr(f"frames/{key}.csv", "index,t,level,detail,jpeg_bytes,lit\n"
                               + "".join(",".join(str(x) for x in row) + "\n" for row in dv))
        for f in extra_files:
            if f and os.path.isfile(f):
                if f.endswith((".txt", ".json", ".csv")):
                    with open(f, encoding="utf-8", errors="replace") as fh:
                        z.writestr("files/" + os.path.basename(f), scrub(fh.read()))
                else:
                    z.write(f, "files/" + os.path.basename(f))
    return path


def summary_counts(results):
    c = collections.Counter(r["ok"] for r in results.values())
    return c[True], c[False], c[None]
