# How Lizard Scanner works

Plain-English notes on what the app reads, what it changes, what it never does, and the maths
behind the wall test. For the measurements and decisions see [DESIGN_NOTES.md](DESIGN_NOTES.md).

## The files

| File | What it is |
|---|---|
| `lizard_scanner.py` | The window. Sets itself up on first run (numpy, Pillow into `lz_env/`). |
| `lizard_camera.py` | Everything about the scanner, no window code: cameras, light ladder, wall calibration, depth, recordings, and the camera object Scantastic can use. |
| `lizard_panel.py` | The Lizard's buttons and helper windows (light, wall test, eye tests, diagnostics, settings). Shared with Scantastic later. |
| `lizard_framecap.py` | Frame capture and data logger, shared with Who are yoUSB? (0.1.1: optional extra columns). Standard library only. |
| `lizard_recording.py` | Joins a running scanner to the capture module: every frame, every setting change (through `Lizard.set`), cameras that start later, snapshots, notes. |
| `lizard_diag.py` | The diagnostics: plain functions, no window code, each returning OK / PROBLEM / skipped with advice; and the report zip. |
| `retro_skin.py` | The wood-panelled TV look, the same file as Scantastic's. Optional: without it the app looks plain. |
| `tools/lizard_capture.py` | Reads USB recordings (Linux usbmon, Windows USBPcap) and rebuilds the video frames. Research tool from Who are yoUSB?. |
| `tools/calibration_study.py` | Measures the wall calibration on the simulated scanner (DESIGN_NOTES section 4). |
| `tools/find_cr_scan_calibration.ps1`, `tools/Find-CR-Scan-Calibration.bat` | Windows, read-only: looks for CR Scan's calibration files and copies them to the Desktop. Untested. |

## What the app reads and changes on the scanner

The CR-Scan Lizard is two ordinary USB cameras (UVC) behind a small USB hub inside it:
camera A (`bcdc:000a`, "ICT CameraA") and camera B (`bcdc:000b`). Linux sees each as a
`/dev/videoN` and needs no driver.

- **Video:** started and stopped with `v4l2-ctl` (from v4l-utils), in MJPEG 1280x720 at about
  10 pictures a second (what Creality's app uses) or uncompressed 640x480. The app decodes every
  picture at half size (640x360), which is cheap enough for a 7th-gen Core i3.
- **Lights:** there is no light command. The infrared projector belongs to camera A and the white
  ring to camera B (seen on the owner's Lizard, 16 October 2026): each runs while its camera streams,
  pulses, and stops when the camera stops. Both cameras can run at once.
- **Settings:** only standard UVC controls: auto-exposure mode, exposure time and gain, the same
  three Creality's app changes. Everything else is reachable under *Every camera setting*.
- **Serial number:** read once at start the way Creality's app does it: one 8-byte request
  (`02 c2 00 fe 14 00 00 00`) to the camera's extension unit 4, selector 10, then three reads of
  selector 11. This is the only non-standard request the app sends. It is what Creality's app sends
  at every connect, so it is not expected to change anything on the scanner.

**What it never does:** write firmware, write calibration to the scanner, send any request it
has not seen Creality's app send, or connect to the internet (apart from the first-run package
install, which is pip's doing).

## What it saves, and where

All inside the app's folder:

| Folder / file | What |
|---|---|
| `calibration/<serial>/` | **Everything for one scanner's calibration, in one folder** (ZIP CALIBRATION packs it into one file, serial hidden): |
| `  calibration.json` | The fitted numbers (readable) |
| `  reference.npz` | The reference wall's pattern, every wall's pattern and the K map |
| `  shift_maps.npz` | How far the stripes moved, wall by wall, at every point measured |
| `  calibration_report.txt` | The fit, wall by wall |
| `  walls/` | Saved the moment each wall is recorded: `wall_patterns.npz` and the camera's own full-size pictures (`wall_280mm_1.jpg`...), so the calibration can be refitted later without new walls |
| `captures/capture_<date>_<time>_<name>/` | Captures and recordings (lizard_framecap): `frames/A_000001.jpg`... (the camera's own JPEG bytes), `frames.csv` (time, camera, exposure, gain, auto-exposure, settings revision, note, brightness, detail, pattern (share of the view showing the stripes), sharpness, rung, stream picture number), `events.jsonl` (settings snapshots and every change), `session.json` (serial hidden by default) |
| `scans/lizard_<date>_<time>/` | Recordings from 0.1 to 0.3 (still play back) |
| `reports/` | Light and gain test answers, diagnostics report zips |
| `settings.json` | Look, source, video mode, view, scanner on/off |
| `lz_env/`, `bootstrap.log` | The private Python packages and their install log |

## Lit and dark pictures

**On the real scanner the projector is lit in every picture** (it flashes once per exposure; 16 October
2026). The pulse finder below is kept because other scanners, and other settings, may alternate. In
Creality's recording the JPEG sizes alternated every other picture, which first suggested a pulse.

For each picture the app measures three things: **fine detail** (the mean difference between
neighbouring pixels along every fourth row), **JPEG size** (detail compresses badly, so lit JPEGs are
bigger) and **mean brightness**. Over the last 20 pictures it splits each of the three into a high
and a low group at the point that separates them best. A signal counts as a pulse when:

- the groups are at least 4 spreads apart, and the high one at least 10% above the low one;
- each group holds at least 15% of the pictures;
- the pictures switch between the groups in at least a quarter of the steps (so a hand moving
  in and out of view does not count).

The clearest signal decides, and the app reports the pattern it found (every other picture,
1 in 3, 2 in 4...). Detail usually wins: the projector's pattern adds a lot of it while changing the overall
brightness only a little, especially in a room with daylight. Without a pulse every picture counts
as lit.

A projector that flashes once per exposure puts its pattern in every picture: there is nothing to take
away, and the eye still sees it flicker. Diagnostics reports that as "stripe pattern in every picture",
not as a problem; every picture is then used as lit. Whether the stripes are there at all is judged
per tile (9 x 16 tiles): a tile is striped when the picture changes at least 1.6 times as much along x
as along y. The live tag shows the share of striped tiles.

Before 0.3.0 only the brightness was used, and only strict every-other-picture alternation counted.
On the owner's real Lizard (16 October 2026) that missed a pulse he could see by eye.

## The light ladder and AUTO-TUNE

The white ring and the projector flash while their camera's sensor exposes, so **exposure is how
long the lights flash**. Every camera, A and B, starts at Creality's own 1 ms and gain 65, never on
its own automatic exposure, and when camera B runs it gets the same light level as camera A. (Over USB, Creality's brightness control only changes exposure and gain, and the
owner saw it dim the ring from unbearably bright to dim; gain cannot drive an LED.)

The ladder is 11 pairs of (exposure, gain), from (0.2 ms, 16) to (1 ms, 160), including the three
pairs Creality's app was seen to use: (1 ms, 65), (0.5 ms, 40), (0.5 ms, 30). It never goes past
1 ms, Creality's longest flash; the top rungs only add gain. With *Allow longer flashes* five more
rungs go up to 6 ms (exposure 60), the camera's own starting value and the hard ceiling. Presets are
rungs of it. The rungs are clamped to the limits the camera reports.

AUTO-TUNE sets a rung, waits three pictures for it to take, then looks at three pairs of
pictures (the brighter of each pair is the lit one, since the lit / dark marking lags behind a
change). It wants the brightest 0.5% of the picture between 170 and 240 out of 255, with no more
than 0.4% blown out. Too dark: one rung up. Too bright: one rung down. When it would go back to a
rung it already tried, it keeps the brightest rung that was not blown out.

## The wall test

### The real pattern (18 October 2026)

The owner's photos and saved frames showed what the projector really throws: **dense vertical
stripes**, about 13 px apart at 1280 wide (6.3 px at the 640 working size), each stripe a string of
beads whose width and sideways position wobble irregularly along it, differently on every stripe.
Version 0.4 assumed random dots and matched small square windows; on stripes that repeat every
6.3 px nearly every alignment looks alike, so its depth was mostly noise (the red and green speckle
after the first real wall test).

### The idea

The camera sits a little to one side of the projector (the *baseline*, along the picture's x axis,
across the stripes). A stripe that lands on a nearer surface appears shifted along x compared with the
same stripe on a farther surface. For an ideal camera and projector the shift between a wall at
distance Z and a reference wall at Z_ref is

    shift = K * (1 / (Z + c)  -  1 / (Z_ref + c))

where K is the focal length in pixels times the baseline in mm, and c is where the lens centre sits
relative to the front glass (you measure Z from the glass). On the real scanner K is not the same
everywhere: it changes smoothly across the picture (lens distortion, the projector not quite square),
so the calibration keeps a **K map** rather than one number.

### What gets matched: the stripe code

The plain stripes say nothing about which stripe is which. The beads' wobble does. So every picture is
turned into its *stripe code*: the pattern (background light removed, contrast evened out) minus its
own running mean over 17 rows straight up and down. The running mean is the plain stripe; what is
left is the wobble. Matching then uses tall, narrow windows (49 rows x 21 columns at 640 x 360: many
beads along a few stripes) and searches far along x. On the owner's walls this turned an ambiguous
match (best and second-best alignments within 0.03 of each other) into a clear one (0.25 apart).

### Recording a wall

For each wall the app takes six lit pictures, removes the background light with a local average,
evens out the contrast, and averages them into one *pattern picture*. It keeps three of the camera's
own full-size JPEGs too, and saves both to `calibration/<serial>/walls/` straight away, so a closed
window or a crash loses nothing. From the third wall on, it fits and saves the calibration by itself
after every wall; FIT AND SAVE AGAIN is only needed after typing a picture width.

### Fitting

1. The wall nearest the middle distance becomes the reference.
2. Walls are matched to their neighbour, one step at a time outwards from the reference (180 to 230,
   230 to 280...), on every 8th row and every column, searching up to 45% of the picture width. A match
   counts when its score is at least 0.25 and beats every alignment more than 3 px away by 0.10.
   Neighbouring walls are close enough that the right stripe cannot be mistaken; the steps are then
   chained, so every wall gets a shift map against the reference.
3. c is searched from -80 to +120 mm with K as one smooth surface over the picture (a quadratic in x
   and y), all walls together, strays left out. A wall that does not agree with the others (at an angle,
   not flat, distance typed wrong) is left out, one at a time, while three walls remain, and the report
   says so.
4. With c fixed, K is worked out for every 8 x 8 cell, and the K map is the smooth surface plus each
   cell's own difference (median of its 5 x 5 neighbourhood), held within the range actually measured.
5. Each wall's depth is read back through the K map: its median error against the tape measure, its
   spread over the picture, and right third minus left third. When every wall leans the same way and
   more so further away, the scanner itself was turned to the wall, and the window gives the angle.
6. The focal length (for sideways sizes only) comes from the picture's measured width on the wall if
   you gave it, otherwise from Creality's 200 mm capture width, assumed at 300 mm.

On the owner's five walls (180 to 380 mm, recorded with 0.4) the refit gives each wall back within
2 mm of the tape measure, spread 0.6 to 2.3 mm, with the scanner turned about 1.7 degrees to the wall.

### From a picture to depth

1. The picture's stripe code is matched against the reference wall's, on every 6th row and every
   column, along x only, over every shift the working range (150 to 400 mm) allows with the K map.
2. A parabola through the best score and its neighbours gives a fraction of a pixel.
3. A point is dropped when its score is under 0.30, when another alignment more than 3 px away comes
   within 0.06 of it (it could be the wrong stripe), or when the best match sits at the edge of the search.
4. The shift becomes a distance through the K map, a 3x3 median removes speckle, and the rows in
   between take the nearest solved row.
5. **Tracking.** A full search takes about half a second on a fast PC. After one, the next pictures
   only search the shifts that were just seen, plus 12 px either side (about a quarter of the time);
   every 6th picture, or when much of the view stops matching, searches everything again.

On the owner's snapshot (a mug held about 30 cm away) the mug comes out as a curved surface from about
29 cm in the middle to 31 cm at its sides; the hand, closer than the working range, has no depth.

## The simulated scanner

`SimStream` renders what a Lizard would see: vertical stripes 6.34 px apart, each made of beads of
random length whose width, sideways position and brightness wobble (like the real pattern), projected
from 60 mm beside a camera with a 760 px focal length (at 640 wide; K = 45,600 px·mm, near the real
scanner's), onto a wall (optionally tilted) and optionally a ball. Brightness follows exposure x gain,
the projector is lit in every picture like the real one (tests also try alternating patterns and a
bright room), camera B's pictures are evenly lit with no stripes, bright settings blow out, and there
is picture noise. It exists so everything can be tried and tested without the scanner. Its K is uniform
and its lens perfect, so it is easier than the real thing.
