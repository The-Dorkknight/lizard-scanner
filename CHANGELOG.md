# Changelog

All versions are work in progress (`-wip`) and pre-releases.

## 0.4.0 - 2026-10-19

First published version.

## 0.5.2-wip (19 October 2026): paused

- **Paused for a month or two.** Calibration turned out to be more boring than cleaning.
- **The paper**: *Down the Lizard Hole*, the whole reverse-engineering story in plain English, attached
  as `docs/paper/down-the-lizard-hole.pdf` and in its own repository
  ([down-the-lizard-hole](https://github.com/The-Dorkknight/down-the-lizard-hole)).
- Artwork: the "Free Lizard" poster on the README and web page.
- The wall test says "measure from one fixed point on the scanner" instead of "front glass": any point
  works if it is the same every time, because the fitted offset absorbs it (the owner measured from a
  point near the recessed lens, which is fine).

## 0.5.1-wip (19 October 2026)

The first live test of 0.5.0 on the real scanner (diagnostics report, a wall at about 25 cm): the Depth
view was mostly green with red vertical streaks, objects showed as a few green specks, and the flat wall
check found depth in 0% of the view. The owner had recorded a new calibration that morning.

- **Wrong-stripe streaks removed.** A match on the neighbouring stripe is off by about 6 px and, with tall
  windows, shows as a thin vertical streak. Every point's shift is now compared with the median of its
  neighbours (15 columns either side, one row above and below); points more than 1.5 px off, or with
  too few neighbours, are dropped.
- **Depth check in the wall test.** After every fit, each wall's own picture is run through the depth
  solver exactly like a live picture, and the window and report say what comes back (distance and share
  of the view). A calibration that will give poor live depth now says so before you leave the window.
- **Flat wall check** keeps a pixel when most of its pictures have depth there (it needed all of them,
  so moving holes left almost nothing).
- Checked on the owner's live picture from the report: yesterday's calibration (refitted) gives the wall
  at 240.7 mm over 87% of the view (1st to 99th percentile 237 to 280 mm), 0.29 s per picture while
  tracking on the build machine. The owner's machine took 3.1 s for a full search and 0.49 s tracking.
- 80 tests.

## 0.5.0-wip (18 October 2026)

The first real wall test failed (red and green noise in the Depth view). The owner's photos, saved walls
and a snapshot showed why: the projector throws **vertical stripes with wobbling beads**, not dots.

- **Depth rebuilt for the stripes.** Pictures are turned into their *stripe code* (the pattern minus its
  vertical running mean, which leaves the beads' wobble) and matched in tall 49 x 21 windows along x,
  with a check that no other alignment comes close (a wrong stripe). Walls are matched to their
  neighbours and chained to the reference. The fit keeps a **K map** (the real shift changes by about 25%
  across the picture) and one c; a wall that disagrees with the others is left out; a scanner turned to
  the wall is spotted and its angle given. On the owner's five walls: every wall back within 2 mm.
- Live depth works on every 6th row and every column, then **tracks**: after a full search the next
  pictures only search near the shifts just seen (about four times quicker); every 6th picture searches
  everything again. The Depth tag shows the time per picture.
- **The wall test saves as it goes**: every wall is saved the moment it is recorded (its pattern and
  three of the camera's own full-size JPEGs), and from the third wall the calibration is fitted and
  saved by itself. Closing with walls but no fit asks first. FIT AND SAVE became FIT AND SAVE AGAIN.
- **One folder per scanner, one zip**: `calibration/<serial>/` holds the fit, wall patterns, K map,
  shift maps, report and `walls/`. ZIP CALIBRATION (wall test, or under Depth) packs it into one file,
  serial hidden in names and contents when "hide serial" is on.
- **Load saved walls** refits the walls of an older (0.4, dot-pattern) calibration; the Depth box says
  when a calibration needs it. Calibration format version 2.
- Diagnostics look for **stripes**: a picture tile is striped when it changes 1.6 times more along x than
  along y. The live tag says "stripes 56%" instead of "no pulse found"; the "Dots only" view is now
  "Pattern only"; room infrared is judged between the stripes when there are no dark pictures; the
  capture column `dots` is now `pattern`.
- The simulator projects stripes with wobbling beads, 60 mm baseline (K near the real scanner's), and
  lights every picture like the real one.
- Fixed: the pattern picture could hold NaN on flat patches (a rounding error under a square root).
- `docs/research/real-scanner/2026-10-18-stripes.md`: the findings, shift maps, the refit report and
  the depth of the owner's mug (his hand photo is not included).
- 79 tests (tests now cover real wall data from the owner's scanner, the stripe matcher, tracking, the one-folder zip, refitting an old calibration, and the wall test saving by itself).

## 0.4.0-wip (16 October 2026)

- **Frame capture with a data log**, shared with Who are yoUSB? (`lizard_framecap.py`, from its 0.5.0):
  every saved picture carries its time, camera, exposure, gain, auto-exposure, every other setting and
  a settings revision, plus (new extra columns) brightness, detail, dot-pattern share, sharpness, light
  rung, mode and the stream's picture number. Every setting change is logged by itself through a new
  `Lizard.on_set` hook; cameras started later join through `Lizard.on_start`.
- **Capture panel**: Save frame now, capture name, every Nth picture, notes stamped on the next picture,
  hide the serial (on by default, also in light, gain and sensor-map reports).
- RECORD records through the capture module into `captures/`. Playback reads captures and older sessions;
  with the serial hidden it uses the scanner's only calibration for that mode.
- `lizard_framecap` 0.1.0 -> 0.1.1: optional `extra_columns` (backwards compatible), 2 more tests.
- `docs/research/real-scanner/`: findings from the real scanner on 16 October and the six test reports
  (serial removed).
- 75 tests.

## 0.3.1-wip (16 October 2026)

- **Sensor map** (Diagnostics): camera A alone, B alone, both; each camera's rate, dot pattern,
  colour and pulse, plus which light you saw. Saves a report.
- Camera A check: no pulse is no longer a problem when the pictures all show dots (a projector that
  flashes once per exposure); it is a problem when there are no dots at all.
- The light test's answer now shows in the Diagnostics window (it was filed under the wrong name).
- DESIGN_NOTES: what Creality and reviews say about the hardware ("binocular", projector and a pair of
  cameras, greyscale texture), the owner's observations, and every earlier assumption with its status.
- 56 tests.
- First proven result from the real scanner: in the Light test both lights dimmed and brightened with
  their camera's exposure. Exposure is the brightness control for the white ring and the infrared.

## 0.3.0-wip (16 October 2026)

First fixes from the real scanner (the owner's Lizard, Linux):

- **Camera B has the white ring** (camera A the infrared projector). Every camera now starts at
  Creality's 1 ms flash and gain 65, never on its own automatic exposure, and camera B takes camera
  A's light level whenever it runs. In 0.2.0 camera B started on automatic exposure.
- **Light pulse detection rewritten.** 0.2.0 said "the lights did not pulse" while the infrared glow
  visibly pulsed. It used only mean brightness and only accepted strict every-other-picture
  alternation. Now: dot detail, JPEG size or brightness, whichever is clearest, and any pattern that
  switches often (1 on / 1 off, 1 in 3, 2 in 4...). The app and Diagnostics say which pattern.
- Light and gain tests: choose the light to watch, the white ring (camera B, the default) or the red
  glow (camera A); camera B is started for the test if needed. In 0.2.0 they stepped camera A only.
- Diagnostics report: every picture's brightness, detail, JPEG size and lit / dark mark as a CSV, to
  find the real pulse pattern.
- Recordings: `frames.csv` gains `detail` and `size` columns.
- 51 tests (simulated scanner, now including other pulse patterns and a bright room). One test
  failed once in 11 full runs and could not be reproduced; the AUTO-TUNE test's same-rung assertion
  was wrong in principle (two neighbouring rungs can both be right) and now allows one rung apart.

## 0.2.0-wip (15 October 2026)

- **The ring's flash is capped at 1 ms**, Creality's longest. The white ring and the projector flash
  while the camera exposes, so exposure is how bright the ring looks; 0.1.0's ladder went to 8 ms.
  The top rungs and the Dark / black preset now add gain instead.
- **Allow longer flashes**: an advanced switch with a warning, up to 6 ms (the camera's own starting
  value) and never more; off at every start. "Camera's own settings" only with it on.
- Every camera setting: setting a flash over 1 ms, or automatic exposure, asks first.
- **Diagnostics window**: quick checks (USB, camera A video, modes and settings, serial, projector and
  room infrared, depth speed), camera B, flat wall check, light test and a new **gain test**; one
  report zip with the serial hidden if wanted.
- `lizard_diag.py`: the checks as plain functions, for Scantastic too.
- `tools/Find-CR-Scan-Calibration.bat` / `.ps1` (Windows, read-only, untested) and
  `docs/research/calibration-hunt.md`: the plan for finding Creality's own calibration.
- 42 tests (simulated scanner).

## 0.1.0-wip (15 October 2026)

First version.

- Standalone app for the Creality CR-Scan Lizard, on Scantastic's bones (retro TV look, big buttons,
  sets itself up on first run, double-click launchers for Linux, Windows and macOS).
- Live infrared picture from camera A; "dots only" view; camera A + B side by side; starts by itself
  when the scanner is plugged in (Linux).
- Light without sliders: AUTO-TUNE, presets (Shiny / white, Normal, Dark / black), Dimmer / Brighter,
  camera defaults, and every raw setting under an advanced window.
- Wall test: a rough depth calibration from three to five walls at measured distances, saved per
  scanner serial and video mode, with a warning for walls at an angle.
- Live depth view with distance colours and relief shading; save the view as a .ply point cloud.
- Recording sessions (the camera's own JPEGs plus frames.csv and session.json) and playing them back.
- Light test (does exposure dim the ring?) and "What is camera B?" test, both saving reports.
- Simulated scanner (`--simulate`) for trying everything without the hardware.
- `lizard_camera.LizardCamera` with Scantastic's camera contract, and INTEGRATION.md.
- Tested on the simulated scanner only (29 tests, including the window under Xvfb).
