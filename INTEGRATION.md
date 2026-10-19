# Putting the Lizard into Scantastic

Lizard Scanner is built so the CR-Scan Lizard can drop into
[Scantastic](https://github.com/The-Dorkknight/scantastic) (the "omni" scanner app) with
small, contained changes. This page lists them, against **Scantastic 0.7.1-wip**.
Line numbers are approximate; search for the quoted text.

> **Not done yet, and not tested inside Scantastic.** Everything below is a plan worked out
> from reading Scantastic 0.7.1's code. `lizard_camera.py` is tested against the *shape*
> Scantastic expects (`tests/test_lizard_engine.py::test_camera_contract_for_scantastic`),
> with the simulated scanner. Nobody has run the Lizard inside Scantastic.

## Why it fits

Scantastic's scan pipeline needs, from any camera, one thing per frame:

```python
color, depth, intrinsic = cam.get_frame()   # RGB uint8, uint16 depth lined up with it, pinhole model
```

`lizard_camera.LizardCamera` gives exactly that once the scanner has a **wall calibration**:

| Scantastic reads | Lizard gives |
|---|---|
| `color` | The infrared picture as grey RGB, 640x360 (half of the camera's 1280x720 MJPEG) |
| `depth` | uint16 in 0.1 mm units (`depth_scale = 0.0001`), from the lens centre, 0 = unknown |
| `intrinsic` | open3d `PinholeCameraIntrinsic`; focal length from the calibration (guessed unless measured) |
| `depth_scale`, `guide_range`, `near_m`/`far_m`, `set_range()` | 0.0001, (0.18, 0.35) m, 0.12 to 0.45 m |
| `family`, `device_info`, `serial` | `"Lizard"`, `"CR-Scan Lizard (CRSL...)"`, the scanner's own serial |
| `last_raw_depth`, `last_masked`, `native_K`, `want_ir`, `apply_settings()` | present (the last three unused) |

`lizard_camera.make_camera()` returns a camera that **refuses to start without a calibration**
(with a message saying to run WALL TEST in Lizard Scanner), because Scantastic's pipeline needs
depth in every frame.

## Step by step

### 1. Files

Copy `lizard_camera.py` and `lizard_panel.py` next to `scantastic.py`. Neither imports
anything Scantastic does not already have (numpy, Pillow; open3d is used when present).

Calibrations live in `calibration/<serial>/`. Either copy Lizard Scanner's `calibration`
folder next to `scantastic.py`, or start Scantastic with
`LIZARD_CALIBRATION_DIR=/path/to/lizard-scanner/calibration` so both apps share one.

### 2. The camera type box

```python
CAMERA_KINDS = [("RealSense (SR305 / D400)", "realsense"),
                ("Kinect v1 / v2 (experimental)", "kinect"),
                ("CR-Scan Lizard (experimental)", "lizard")]
```

### 3. Seeing it plugged in (`detect_cameras`)

On Linux, beside the Kinect line:

```python
LIZARD_CAMERA_PIDS = {"000a": "CR-Scan Lizard"}
...
vendor, table = {"kinect": ("045e", KINECT_CAMERA_PIDS),
                 "lizard": ("bcdc", LIZARD_CAMERA_PIDS)}.get(kind, ("8086", SUPPORTED_PIDS))
```

and in the non-Linux branch treat `"lizard"` like `"kinect"` (return `None`: cannot check cheaply).
`_watch_possible()` should then also return True for the Lizard on Linux only.

### 4. Its own settings set (`CAMERA_PROFILES`, `profile_key`)

```python
"Lizard": dict(name="CR-Scan Lizard", tech="structured light (rough wall calibration)", colour="#3f8f5a",
               defaults=dict(near=0.15, far=0.45, quality=60.0, detail="Standard (follows Quality)",
                             narrow_band=False, strict=False, smooth=5, fill_holes=0,
                             mask_gaps_only=False, isolate=True, average=True)),
```

No `confidence` or `laser` keys: `_activate_profile` then greys out those two boxes by itself.
In `profile_key()`: `if family == "Lizard": return "Lizard"`. In `_activate_profile`, the
`if key != "Kinect":` that remembers the RealSense profile should become
`if key not in ("Kinect", "Lizard"):`. `camera_model_name()` already falls back to the
profile name, so the badge reads "CR-Scan Lizard".

### 5. Choosing it (`_camera_kind_changed`, `_restore_camera_kind`)

Mirror the Kinect branch, without the camera bridge (the Lizard's video already runs in a
separate `v4l2-ctl` process, so a crash there cannot take the window down):

```python
def _make_lizard_camera(self):
    import lizard_camera
    return lizard_camera.make_camera(), lizard_camera
...
elif kind == "lizard":
    try:
        camera, module = self._make_lizard_camera()
        status = module.driver_status()
        missing = [n for n, (ok, _why) in status.items() if not ok]
        note = ("CR-Scan Lizard chosen (experimental). "
                + ("" if not missing else "Missing: " + ", ".join(missing) + ". "))
    except Exception as exc:
        ...same fallback as Kinect...
```

then `self._activate_profile("Lizard", announce=False)` for that kind, and in
`_restore_camera_kind` also restore `"lizard"` (it is `CAMERA_KINDS[2]`).

### 6. Optional: the automagic light buttons inside Scantastic

`lizard_panel.LightPanel(parent, host)` works in any Tk window. Scantastic needs a tiny host:

```python
class _LizardHost:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.muted = MUTED_FG
        self.reports_dir = os.path.join(SCRIPT_DIR, "reports")
        self.is_sim = os.environ.get("LIZARD_BACKEND") == "sim"
    lizard = property(lambda self: getattr(self.app.camera, "lizard", None))
    tuner = property(lambda self: getattr(self.app.camera, "tuner", None))
    serial = property(lambda self: getattr(self.app.camera, "serial", ""))
    def status(self, text): self.app.status_var.set(text)
    def calibration_changed(self, result, reference): pass   # restart the camera to pick it up
```

Pack `LightPanel(cam_frame, _LizardHost(self))` while the Lizard is chosen and
`pack_forget()` it otherwise. The wall test can also run from Scantastic with
`lizard_panel.WallTestWindow(host)`.

**Thread note:** Scantastic runs its camera in a background thread. Tk objects freed on a
background thread make Tcl abort the program; Lizard Scanner avoids it by running Python's
cycle collector only on the window thread (`gc.disable()` plus a `gc.collect()` timer).
`lizard_panel.Worker` already hands its last references back to the window thread. If
Scantastic ever shows "Tcl_AsyncDelete: async handler deleted by the wrong thread", that
is the cause.

### 7. What works on the Lizard in Scantastic, and what does not

- Scanning, mesh building, cutting, saving: should work, since they only see colour + depth.
- The **sensor check** (point at a wall) should work too, and is a good test of the wall
  calibration: a flat wall should come out flat.
- Colour: the Lizard's picture is infrared, so scans are grey.
- Laser power and depth confidence do not apply.
- Depth is **rough**: a K map fitted from five walls, no full lens model. On the owner's walls it is
  within 2 mm; expect holes where the stripes are not clear (edges, dark or shiny surfaces). One depth
  picture takes about 0.15 s while tracking and 0.5 s for a full search on a fast PC (more on an i3),
  so Scantastic will get depth a few times a second, not 10. See DESIGN_NOTES.md.
- Calibrations made before 0.5.0 (dot pattern) do not load; Lizard Scanner's wall test refits them
  from their saved walls ("Load saved walls").

## Trying it without the scanner

`LIZARD_BACKEND=sim` makes `lizard_camera` use the simulated Lizard (a ball in front of a
wall) in any app. Calibrate it once in Lizard Scanner with `--simulate` (serial `SIMULATED`),
then start Scantastic with `LIZARD_BACKEND=sim LIZARD_CALIBRATION_DIR=...` and choose the
Lizard.
