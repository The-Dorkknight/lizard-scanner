# Frame capture in Lizard Scanner (for Who are yoUSB?)

`lizard_framecap.py` came from Who are yoUSB? 0.5.0-wip (Frame capture workbench). This is how Lizard
Scanner 0.5.0-wip uses it, and the one change made to the module, so the two copies stay the same.

## The one change: lizard_framecap 0.1.0 -> 0.1.1 (backwards compatible)

`FrameLogger(..., extra_columns=("detail", "dots"))` adds those columns to the end of `frames.csv`, filled
from `add_frame(..., extra={"detail": 3.2, "dots": 0.07})`. Without `extra_columns` the files are exactly
as in 0.1.0, so Who are yoUSB? can take 0.1.1 as it is. Two tests were added
(`ExtraColumnsTest` in `tests/test_framecap.py`); all 12 pass.

## How Lizard Scanner joins it to the cameras: lizard_recording.py

`CaptureRun(lizard, root, label=..., app=..., every=1, snapshot=False, hide_serial=True, rung=fn)`:

- Saves every frame of every running camera through `FrameLogger.add_frame`, with the stream's own
  timestamp. MJPEG frames are saved as the camera's own bytes (full 1280x720); grey frames as PNG.
- Extra columns: `stream_index` (the stream's own picture number, shows dropped pictures),
  `detail` (fine detail), `pattern` (share of the picture showing the projector's stripes; called `dots`
  before 0.5.0, when the pattern was thought to be dots),
  `rung` (light ladder rung), `mode`, `sharpness` (focus / pattern fineness).
- **Settings changes are logged by themselves.** `lizard_camera.Lizard.set` now calls the functions in
  `lizard.on_set(cam, name, value, error)` after every change, accepted or refused. A run hooks in there,
  so the light buttons, AUTO-TUNE, the eye tests and the diagnostics all stamp the frames that follow,
  with no extra line at each call.
- **Cameras started later join in.** `Lizard.start` calls `lizard.on_start(cam, stream)`; a run attaches
  to camera B when the A + B view starts it.
- `snapshot=True`: one frame from each camera running at the start, then it stops by itself (4 s timeout).
- `mark(note)`, `event(kind, ...)`, `count()`, `stop()`, `summary()`.

Who are yoUSB?'s Scanner tab has its own streams; the two hooks (`on_set`, `on_start`) are the pattern to
copy if it wants settings logged without touching every place that changes one.

## In the window

- **RECORD** now records through the capture module into `captures/capture_<date>_<time>_<name>/`.
- **Capture** panel: Save frame now (each running camera), a name for the capture, "every Nth picture",
  a note box (stamped on the next picture; kept for the next capture when nothing is recording), and
  "Hide the scanner's serial in saved files" (on by default; also used by the light, gain and sensor-map
  reports now).
- **Source > Play back a recording** plays captures as well as the older Lizard Scanner sessions. With the
  serial hidden, playback uses the scanner's calibration when there is exactly one for that video mode.

## Tested

On the simulated scanner only: 6 tests in `tests/test_recording.py` (settings and notes on the frames,
camera B joining, snapshot, hooks removed on stop, serial hidden, playback), one window test, and the
module's own 12.
