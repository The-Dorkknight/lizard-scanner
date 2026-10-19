# Lizard engine: handoff to the multi-scanner app

> **Work in progress.** Taken from Who are yoUSB? 0.4.0-wip. The same code drives the Scanner tab, which has been
> used on the real CR-Scan Lizard (both cameras start and stop, the pictures are clear, and the lights follow the
> video). This module without the GUI has only been tested against simulated cameras.

## What you get

| File | What it is |
|---|---|
| `lizard_engine.py` | Find the scanner, start/stop camera A and B, live preview frames, "lights pulsing?" check, every camera setting, read the scanner serial. Linux, stdlib only, needs `v4l2-ctl` |
| `lizard_capture.py` | Read USB recordings (Linux usbmon or Windows USBPcap, pcap/pcapng) and rebuild every video frame byte-exact |
| `RESEARCH.md` | Everything learned about the Lizard's protocol, including both real recordings |
| `PLAN.md` | Open questions and next steps |
| `reports/` | The two real recording reports (old report format; serials removed) |

## How the Lizard works (short version)
- It is **two standard USB cameras** (UVC) behind an internal USB 2 hub: CameraA `bcdc:000a` ("ICT CameraA"), CameraB
  `bcdc:000b` ("ICT CameraB"). On Linux each appears as a `/dev/videoN` node. No vendor driver is needed.
- CameraA delivers the IR pictures CR Scan uses: MJPEG 1280x720 at about 10 fps (CR Scan's mode), or YUYV 640x480 at
  5 fps. The pictures are monochrome infrared with the projector's dot pattern.
- **The lights have no on/off command.** They run while a camera streams and stop when it stops. CR Scan's frames
  alternate bright and dark: the light pulses every other frame.
- **Light strength:** CR Scan's brightness/sensitivity controls only change standard **exposure** (100 µs units: CR Scan
  used 10 = 1 ms and 5 = 0.5 ms) and **gain** (65, 40, 30). In CR Scan this dimmed the ring until it barely lit, so
  the flash length most likely follows exposure. The `brightness` control is image processing only. See PLAN.md.
- **Serial:** CR Scan writes `02 c2 00 fe 14 00 00 00` to extension unit 4 selector 10, then reads selector 11 three
  times (8 bytes each). The answer holds a `CRSL…` serial, which is probably how it fetches the per-scanner
  calibration.

## API (lizard_engine.py)
```python
from lizard_engine import LizardScanner

lz = LizardScanner()              # finds the cameras via /sys (refresh() after plugging in)
lz.cameras()                      # {'A': '/dev/video2', 'B': '/dev/video4'} (None when missing)
lz.start("A")                     # 'yuyv' (640x480, preview frames) or lz.start("A", "mjpg") = CR Scan's mode
lz.latest("A")                    # (320, 240, grey bytes) newest preview frame, or None
lz.pulsing("A")                   # (is_pulsing, share_of_flips, high_low_ratio) over the last 20 frames
lz.fps("A")
lz.controls("A")                  # every setting: name, type, min, max, default, value, menu, flags
lz.set("A", "exposure_time_absolute", 5)   # returns '' or the camera's error; turns auto-exposure off first
lz.apply_cr_scan_defaults("A")    # manual exposure 1 ms, gain 65 (what CR Scan sets)
lz.defaults("A")                  # back to the camera's own defaults
lz.serial()                       # the CRSL… serial, read the way CR Scan does
lz.stop("A"); lz.stop_all()       # lights go off with the video
```
Lower level: `CamStream(node, mode)`, `v4l2_controls(node)`, `v4l2_set(node, name, value)`, `read_lizard_serial(node)`,
`lizard_cameras()`, `png_bytes(w, h, grey)` (for a Tk preview without Pillow).

## Fitting it next to RealSense (SR300 / D400)
A simple pattern is one adapter class per scanner family with the same small surface:

| Scanner app needs | RealSense (pyrealsense2) | Lizard (this engine) |
|---|---|---|
| detect | `rs.context().query_devices()` | `LizardScanner().connected()` |
| start / stop | `pipeline.start(config)` / `stop()` | `start("A", "mjpg")` / `stop("A")` |
| frames | `wait_for_frames()` → depth + colour | `latest("A")` preview, or read MJPEG frames yourself (see below) |
| emitter / laser | `depth_sensor.set_option(rs.option.emitter_enabled / laser_power)` | none: lights follow video; strength via exposure (suspected) |
| exposure / gain | `rs.option.exposure`, `rs.option.gain` | `set("A", "exposure_time_absolute", …)`, `set("A", "gain", …)` |
| depth | computed on the camera | **not on the camera**: IR dot pictures only; depth must be computed on the PC, and CR Scan's per-scanner calibration is not yet found |
| serial | `get_info(rs.camera_info.serial_number)` | `serial()` |

Full-resolution frames: the preview path keeps a 320x240 grey copy. For 1280x720 MJPEG frames, extend
`CamStream._read` (the MJPEG branch already finds each JPEG's start and end) to keep the bytes, then decode with
Pillow or OpenCV in the app.

## Known limits
- **Linux only** (v4l2-ctl). Windows would need DirectShow/Media Foundation, for example through OpenCV.
- **No depth yet:** the Lizard sends IR pictures, not depth. Turning them into 3D needs the calibration (cached by CR
  Scan per serial, not found yet) and a structured-light or stereo solver.
- Camera B's role is unknown. CR Scan never streamed it in either recording.
- `read_lizard_serial` writes one request to the camera, the same one CR Scan sends at every connect.
- Licence: PolyForm Noncommercial 1.0.0 (same as Who are yoUSB?).
