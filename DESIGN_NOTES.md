# Design notes and research

What was looked into while building Lizard Scanner, what was measured, what was decided and why,
and what is still unknown. Written 15 October 2026 for 0.1.0-wip, updated the same day for 0.2.0-wip.

**Read this first:** every measurement here comes from the app's **simulated scanner**, not a real
CR-Scan Lizard. The camera control (start, stop, settings, serial) comes from Who are yoUSB?, where
it ran on a real Lizard. Nothing else has.

## 1. Goals and fixed limits

- Use a Creality CR-Scan Lizard without Creality's app, on Linux first.
- One button for each thing a user wants; no sliders for the light (owner's request,
  15 October 2026).
- A rough depth calibration from a wall test (owner's request, same day), so the Lizard gives
  depth, not only pictures.
- Built on Scantastic's bones and shaped so the Lizard can move into Scantastic later.
- Run on a 7th-gen Core i3 with no graphics card: numpy and Pillow only, no OpenCV on Linux.
- PolyForm Noncommercial; say plainly what is untested.

## 2. What is known about the Lizard

From two USB recordings of Creality's app on Windows (see
[docs/research/lizard-engine](docs/research/lizard-engine/RESEARCH.md)):

- Two UVC cameras behind an internal USB 2 hub (`1a40:0101`). Only camera A streamed, in both
  recordings: MJPEG 1280x720 at about 9.6 pictures a second.
- No light command. The lights follow their camera's video; camera A's pictures alternated bright
  and dark in the first recording (about 85 KB and 40 KB as JPEG).
- **On the real Lizard (owner, 16 October 2026):** camera B is the one with the white ring; both
  cameras can run at once (seen earlier in Who are yoUSB?); with only camera A running the red
  infrared glow visibly pulses, yet Lizard Scanner 0.2.0's Diagnostics reported "the lights did not
  pulse" (see section 5). Why both USB recordings show only camera A streaming, even in CR Scan's
  texture mode when the ring lit, is not known: either the recordings missed camera B's video, or CR
  Scan lights the ring some other way.
- Creality's brightness / sensitivity controls change only exposure (10 then 5, in 100 us units)
  and gain (65, 40, 30). The camera's own starting values were exposure 60 and gain 16.
- The serial is read through extension unit 4 (request on selector 10, answer on selector 11).
- Creality: working distance 150 to 400 mm, capture area 200 x 100 mm, up to 10 fps, LED and
  near-infrared light ([CNX Software, February 2022](https://www.cnx-software.com/2022/02/22/creality-cr-scan-lizard-3d-scanner-works-on-black-objects-offers-up-to-0-05-mm-accuracy/)).
  Whether the Lizard is one camera and a projector, or two cameras (stereo), is not stated there.

### What reviews and Creality say about the hardware (checked 16 October 2026)

- Creality's own page calls the Lizard "binocular", with light "LED+NIR (Near-infrared mode)", 10 fps,
  150-400 mm ([Creality](https://www.creality.com/blog/creality-cr-scan-lizard-features-professional-grade-accuracy-to-the-consumer-grade-market)).
- Hackster's review: it "scans objects using a projector and a pair of cameras"; texture scans are
  greyscale, and colour needs photos from a phone ([Hackster.io](https://www.hackster.io/news/review-cr-scan-lizard-3d-scanner-33961d45252d)).
- CNX Software: LEDs combined with near-infrared light, not lasers ([CNX Software](https://www.cnx-software.com/2022/02/22/creality-cr-scan-lizard-3d-scanner-works-on-black-objects-offers-up-to-0-05-mm-accuracy/)).
- None of them says which camera is which, or whether depth comes from stereo between the two cameras
  or from one camera and the projector. No teardown was found.

### The owner's observations on the real scanner (16 October 2026)

- Camera B lights the white ring; camera A the red infrared glow. In the camera B check the ring lit
  while B ran (infrared off), then the infrared came on when camera A restarted.
- With camera A running, the infrared glow visibly pulses, but 0.2.0 and 0.3.0 found no pulse in
  camera A's pictures.
- **Light test, run on both lights:** each started half bright (1 ms), dimmed low when the exposure
  dropped to 0.1 ms, then slowly ramped up again over the steps back to 1 ms (2.5 s each). The
  brightness of each light follows its own camera's exposure.
- Camera A's picture shows faint **zig-zag parallel lines**: possibly the projector's real pattern
  rather than random dots, which the depth matcher assumes. Camera B only shows a picture while the
  white flash fires; covering camera A darkened everything. Details and the six test reports:
  [docs/research/real-scanner](docs/research/real-scanner/README.md).
- Earlier, in Who are yoUSB?, both cameras ran at once, and with the white ring on, the video came at a
  low picture rate.

### The real pattern, and the first real wall test (18 October 2026)

- The owner ran the wall test (180, 230, 280, 330, 380 mm): afterwards the Depth view was mostly **red
  and green noise**. Both lights worked and the brightness setting was "perfect". Camera A was "iffy
  about seeing dots", and the owner said dots were probably a false assumption: **"the IR laser projects
  vertical lines with staggered dots."** Photos of the pattern and a zip of the calibration folder plus a
  snapshot (a mug held in front of the scanner) followed.
- The saved walls show it: vertical stripes 6.3 px apart at 640 wide (about 13 at 1280), each a string of
  beads whose width wobbles irregularly. 0.4's fit (one global shift by phase correlation) found
  correlation peaks of about 0.03 and an RMS of 23.6 px: the fit had failed, and so the depth was noise.
- Matching the stripe code (the pattern minus its vertical running mean) in tall windows gave clean,
  smooth shift maps between neighbouring walls (89, 56, 39 and 24 px at the left edge), changing
  smoothly across the picture (56 px at the left to 51 at the right between 230 and 280 mm). So K varies
  across the picture by about 25%.
- Refitted with 0.5's method, the same walls come back within 2 mm of the tape measure; every wall leans
  the same way (deeper on the left, more so further away), so the scanner was turned about 1.7 degrees.
- The calibration only saved on FIT AND SAVE; 0.5 saves every wall as it is recorded and fits from the
  third. And the owner asked for all calibration material in one folder, for easy zipping.

### Assumptions, and where they stand

| Assumption (from 0.1.0) | Status |
|---|---|
| The lights follow camera A | **Wrong.** The ring follows camera B, the infrared camera A. Fixed in 0.3.0 |
| Camera A's pictures alternate lit / dark | **Wrong.** Came from CR Scan's JPEG sizes alternating (85 / 40 KB). On the real scanner the projector is in every picture: it flashes once per exposure, which the eye sees as flicker. The simulator lights every picture since 0.5.0 |
| Camera B is a colour texture camera | **Probably wrong.** The Lizard's texture is greyscale (Hackster). Camera B may be the second camera of a stereo pair, lit by the white ring |
| Depth from one camera and the projector (wall calibration) | **Works on the real walls** (0.5.0 refit: within 2 mm), though probably not Creality's method ("binocular"). Camera A sees the pattern |
| The projector throws a random dot pattern | **Wrong.** Vertical stripes with beads whose width wobbles (owner, photos and saved frames, 18 October 2026). 0.5.0 matches the beads' wobble (the stripe code) in tall windows |
| The pattern slides as one piece (one K for the whole picture) | **Wrong.** The shift changes smoothly across the picture, by about 25% of K. 0.5.0 fits a K map |
| One picture from camera A is one camera's view | **Not checked.** A stereo camera can send both views in one picture (side by side). Look at the A + B view: if camera A's picture shows the same scene twice, it is a stereo pair in one stream |
| The ring's brightness is its camera's exposure | **Confirmed by eye** (owner, 16 October 2026, Light test): the white ring and the infrared both started half bright at 1 ms, dimmed at 0.1 ms and slowly brightened again as the exposure stepped back up to 1 ms. Exposure is the brightness control; no vendor light command is needed. Whether gain affects the LEDs too is still open (Gain test) |

## 3. Decisions

| Decision | Why | Rejected |
|---|---|---|
| Light as an 11-rung ladder with presets and AUTO-TUNE | Creality's app only moves exposure and gain together; one dimension is easier than two sliders | Two sliders (asked to replace them); a full auto-exposure loop (the camera's own does that, and it fights the pulsing lights) |
| Flash (exposure) capped at 1 ms; brighter rungs add gain only (0.2.0) | The ring flashes while the sensor exposes: the owner saw CR Scan's brightness control take it from "unbearably bright" to dim, and that control only changed exposure and gain. 0.1.0's ladder went to 8 ms, eight times CR Scan's longest flash: dazzling, and LED heat beyond what Creality chose | No cap (eye comfort, LED heat); a fixed flash with gain only (dark objects need more light) |
| Longer flashes only behind a switch with a warning, never past 6 ms, off at every start | 6 ms (exposure 60) is the camera's own starting value, so the firmware's own choice; any webcam app gets it | Remembering the switch between runs (too easy to forget it is on) |
| Diagnostics as plain functions returning OK / PROBLEM / skipped plus advice | Testable on the simulator, reusable in Scantastic, one zip for bug reports | Checks inside the window code |
| Depth by matching against a reference wall (projector + one camera) | Needs only camera A, which Creality's app uses; works whatever camera B turns out to be | Stereo from A and B (camera B's role unknown; Creality's app never streamed it) |
| A K map (smooth surface plus local corrections) and one c, fitted from wall shift maps (0.5.0) | Still only a tape measure and five walls; the real K varies across the picture | One global K (0.4.0: wrong on the real scanner); full camera + projector calibration with a printed board (more accurate, much more work for the user; worth doing later) |
| Match the stripe code (pattern minus its vertical running mean) in 49 x 21 windows (0.5.0) | The plain stripes repeat every 6.3 px; only the beads' wobble is unique, and it runs up and down the stripes | Square windows on the raw pattern (0.4.0: ambiguous, the red / green noise); phase correlation of the whole picture (peaks of 0.03 on the real walls) |
| Walls matched to their neighbours and chained to the reference (0.5.0) | Neighbouring walls move 24 to 89 px, little enough that a wrong stripe is not picked; the 180 mm wall against the 280 mm reference moves about 145 px and is only clear mid-picture | Every wall straight against the reference |
| A wall that disagrees with the others is left out (0.5.0) | One wall at an angle bent the whole fit in testing | Trusting every wall |
| Every shift searched on every 6th row, then tracking (0.5.0) | A full search is about 0.5 s on a fast PC (probably 1 to 1.5 s on the i3); tracking is about four times quicker | Coarse-to-fine at half width (the stripes alias at half width) |
| Work at half size (640x360) | Pillow decodes JPEG at half size almost for free; four times less work | Full 1280x720 (slower on the i3; maybe later as an option) |
| Picture from camera A as grey RGB in Scantastic | Scantastic's pipeline wants colour + depth | |
| Scantastic's Lizard camera refuses to start uncalibrated | Its pipeline needs depth in every frame | A pictures-only mode in Scantastic (more changes there) |
| Cycle collector on the window thread only | A Tk object freed on a camera thread aborts the program (found by the tests) | |

## 4. Measurements (simulated scanner)

All from `tools/calibration_study.py` and the tests. Since 0.5.0 the simulator projects stripes with
wobbling beads like the real scanner; simulated geometry: 760 px focal length at 640 wide, 60 mm
baseline, lens 12 mm behind the glass, K the same everywhere (the real scanner's is not). Numbers for
0.4 and earlier (dot pattern, one global shift) are in the git history.

**Fit with exact distances**, walls at 200 / 250 / 300 / 350 mm: K within 0.1% of the truth,
c = 12.0 mm (truth 12), every wall within 0.1 mm.

**Tape-measure errors.** Each wall's real distance off by a random amount (standard deviation
shown), 8 runs each, median depth error of a flat wall at each distance (0.5.0):

| Walls (mm) | Tape error | 180 | 230 | 280 | 330 | 380 |
|---|---|---|---|---|---|---|
| 200, 250, 300, 350 | none | 0.0 | 0.1 | 0.1 | 0.2 | 0.3 |
| 200, 250, 300, 350 | 1 mm | 0.4 | 0.7 | 0.8 | 0.8 | 2.3 |
| 200, 250, 300, 350 | 2 mm | 0.8 | 1.2 | 1.6 | 1.4 | 4.0 |
| 200, 300 | 1 mm | 0.5 | 0.5 | 0.7 | 1.2 | 2.8 |
| 180 to 380, six walls | 1 mm | 0.7 | 0.5 | 0.5 | 0.6 | 1.0 |
| 180 to 380, six walls | 2 mm | 1.1 | 1.1 | 0.8 | 0.9 | 2.1 |

One depth picture (640x360, ball in front of a wall): full search 332 ms, tracking 213 ms on the build
machine. On the owner's real mug snapshot: 0.55 s full, 0.14 s tracking.

**The real walls** (0.5.0 refit of the owner's 0.4 wall test): every wall within 2 mm of the tape, spread
0.7 to 2.3 mm, K 39,000 to 52,000 px·mm across the picture. See
[docs/research/real-scanner/2026-10-18-stripes.md](docs/research/real-scanner/2026-10-18-stripes.md).

So: depth is about as good as the tape measure inside the measured range, and gets worse beyond
the farthest wall. K and c trade off against each other (K can be 10% off while depth inside the
range stays right), so their values alone say little. **Decision:** suggest five walls spanning
the whole working range (18, 23, 28, 33, 38 cm).

**History: depth from one picture with the dot pattern** (0.2.0; five walls, exact distances). Flat walls every 10 mm from 170 to
400 mm: depth in 80 to 91% of the view; on average 0.7% of it stray (more than 3 mm off), at worst 5%.
A 40 mm ball 180 mm away in front of a wall at 330 mm: depth in 88% of the view, 90% of it within
1.0 mm, 1.4% wrong by more than 10 mm (mostly in the projector's shadow beside the ball).

0.1.0 had strays of up to 26% at some distances and none at others. Mapping them against distance
showed a pattern: they followed the dot pattern's sub-pixel position. The coarse search shrank the
dot picture by taking every other pixel, which aliases dots only about 2 pixels across. 0.2.0
averages 2x2 blocks first (worst wall 26% to 10%), refines the coarse answer to a fraction of a
pixel and searches 3 px either side instead of 2, and matches over 15x15 pixels instead of 11x11
(worst wall 10% to 5%). The flat wall check found it.

**Speed:** about 0.2 s per depth picture on a 2.3 GHz server processor (2 cores). Not measured on
a 7th-gen i3; expect it to be slower.

**Auto-tune:** from the darkest and from the brightest rung it settles on the same rung, in 4 to
9 steps of about a second each at 10 pictures a second. On a far, dim wall (400 mm) it now stays at
a 1 ms flash and raises gain (0.1.0 went to 3 ms there).

**Flat wall check** (5-wall calibration, exact distances): a wall at 270 mm reads 270.1 mm in the
middle and comes out flat to 0.1 mm RMS once about 4% stray pixels are left out. Its first version
reported 8.9 mm, because a plain RMS let the strays dominate; it now refits without them and reports
their share. A wall turned 10 degrees reads as 13 degrees, because the focal length is guessed: angles
and sideways sizes need the measured picture width.

## 5. Tried and dropped

- Rounding the simulator's dot shifts to whole pixels: shifts between walls came out as whole
  numbers and the fit was 20% off. Real lenses blur, so the simulator now interpolates.
- A lower match threshold (0.30): about 9% of pixels wrong by more than 10 mm, most of them at the
  far end of the picture, where the right partner lies outside the reference wall. Now 0.45, and
  0.68 near the ends.
- Shrinking the dot picture by taking every other pixel for the coarse search: aliasing, see section 4.
- An 11x11 matching window: about five dots per window, so neighbouring alignments looked alike.
  15x15 (now) cut strays by two thirds on the simulator; larger still helped a little more there, but
  would blur fine detail on real objects, which the simulator's smooth ball cannot show.
- A plain RMS in the flat wall check: a few stray pixels made a flat wall look 9 mm rough. It now
  starts from points near the middle distance, refits without strays (3 mm or more off), and reports
  their share.
- Pulse detection from mean brightness with strict every-other-picture alternation (0.1.0 and 0.2.0):
  on the real Lizard it reported no pulse while the owner could see one. Mean brightness can hardly
  change when the dots are sparse or the room is bright (the recording's JPEG sizes, a detail measure,
  did alternate), and any pattern other than 1 on / 1 off failed the alternation test. 0.3.0 uses
  dot detail, JPEG size or brightness, whichever is clearest, and accepts any pattern that switches
  often. On the simulator: found in every pattern tried and in a room bright enough to leave only
  19% between lit and dark brightness, with every picture marked right. The real pattern is not known
  yet: the Diagnostics report now saves every picture's numbers to find it.
- Only camera A under the 1 ms cap (0.2.0): camera B, the white ring's camera, started on its own
  automatic exposure in the A + B view and the camera B check. 0.3.0 starts every camera at 1 ms and
  gives camera B camera A's light level.
- Trusting the lit / dark marking during AUTO-TUNE: it lags behind a change of setting, so the
  first measurements after a change mixed lit and dark pictures. Now each pair is judged by itself.

## 6. What is untested

- Everything new in this app on a real Lizard: auto-tune, wall test, depth, recording, playback.
- Depth from live pictures on the real scanner: only tested on the owner's saved walls and one saved
  snapshot (0.5.0). The 640x360 working size is enough for the stripe code there; the full 1280x720
  JPEGs of each wall are now kept so a full-size fit can be tried later.
- Whether the wall-to-wall chain holds at the near walls (the 180 mm wall matched only 35% of the
  picture: it moves far, and the left of the picture falls outside the reference).
- Lens distortion: not modelled directly; the K map soaks up some of it along x.
- Windows and macOS camera paths (OpenCV): never run.
- The light, gain and camera B tests on a real Lizard; whether the ring follows gain.
- Every diagnostic on a real Lizard (the USB check has nothing to look at on the simulator).
- The Windows calibration-hunt script (no Windows machine to run it on).
- Speed on a 7th-gen i3 (a full depth search is about 0.5 s on the build machine).

## 7. What to build next

1. Run the wall test on the real Lizard; compare a flat wall's depth with the tape measure.
2. If edges are worse than the middle: per-tile corrections from the recorded walls (they are kept
   in `reference.npz` for this).
3. Camera B has the white ring. Find out whether its pictures are colour (texture for scans) and
   whether CR Scan uses it at all.
4. Integrate into Scantastic ([INTEGRATION.md](INTEGRATION.md)) and run its sensor check on the
   Lizard: a flat wall should come out flat.
5. Look for Creality's per-serial calibration (assumed, not proven to exist; the serial read
   suggests it). The plan is in [docs/research/calibration-hunt.md](docs/research/calibration-hunt.md).
   With it, the wall test becomes a check.
6. Run the Gain test on the real Lizard: if the ring follows gain, the cap needs to cover gain too.
7. Once tested on the real scanner, write it all up as a research paper: *Down the Lizard Hole: a
   deep dive into CR-Scan Lizard USB protocol reverse engineering* (illustrated with a lizard take on
   Alice in Wonderland's White Rabbit, after Tenniel's 1865 drawings, which are public domain).

## 8. How to test a change

- `python3 -m pytest tests/` runs everything (the window tests need a display; in CI they run under
  `xvfb-run`).
- `python3 tools/calibration_study.py` reprints the table in section 4.
- `python3 lizard_scanner.py --simulate` and press WALL TEST to try the whole flow by hand.

## 9. Code shared with the other projects

- `retro_skin.py` is Scantastic's, unchanged (skin 0.5.0-wip). The `BigButton` widget is
  Scantastic's too, with a font-size option added.
- Camera discovery, `v4l2-ctl` control and the serial read come from Who are yoUSB? 0.4.0-wip
  (`lizard_engine.py`), extended with full-size MJPEG frames, lit / dark marking and the OpenCV,
  simulated and playback backends.
- `tools/lizard_capture.py` is Who are yoUSB?'s USB recording reader, unchanged.
