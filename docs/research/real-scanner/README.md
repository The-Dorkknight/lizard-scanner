# The real scanner: findings, 16 October 2026

First tests of Lizard Scanner on the owner's CR-Scan Lizard (Linux Mint). What was seen, what the six
saved test reports show, and what is still open. The serial number is removed from every file here.

## What was seen by eye

| When | Seen |
|---|---|
| Camera B check | The white ring lit while camera B ran alone, with the infrared off; the infrared came on when camera A restarted |
| Camera A running | The red infrared glow visibly flickers |
| Light test, white ring (camera B), four runs | The ring started half bright (1 ms), dimmed low at 0.1 ms, then slowly brightened as the exposure stepped back up; "Yes, clearly" every time |
| Light test, infrared (camera A) | The same behaviour |
| Gain test, both lights | "Couldn't tell" whether the light changed |
| Camera A's picture | Faint zig-zag parallel lines |
| Camera B's picture | Only shows a picture while the white flash fires |
| Covering camera A | Everything went dark (both pictures, as remembered) |
| Earlier, in Who are yoUSB? | Two camera windows at once (possibly both camera A's stream); with the white ring on, the video came at a low picture rate |

## What the six reports show

[reports/](reports/) holds them as saved. Each step of a test splits its pictures into the brighter and
the darker half.

| Report | Light | Seen | Picture at 0.1 / 1 ms (or gain 16 / 128) | Bright vs dark half |
|---|---|---|---|---|
| exposure 17:23 | White ring | Yes, clearly | 0.2 / 17.3 | within 3% at every step |
| exposure 17:24 | White ring | Yes, clearly | 0.0 / 17.2 | within 3% |
| exposure 17:26 | White ring | Yes, clearly | 1.1 / 79.7 | 79.7 vs 18.7 at 1 ms: brightness changed during the step |
| exposure 17:31 | White ring | Yes, clearly | 48.9 / 252.5 | 48.9 vs 17.5 at 0.1 ms; everything 10-15x brighter than the other runs (cause not known) |
| gain 17:32 | White ring | Couldn't tell | 1.5 / 62.4 | within 3% |
| gain 17:33 | Infrared (A) | Couldn't tell | 11.8 / 80.7 | within 3% except the first step |

## What that means

1. **Each light's brightness is its own camera's exposure.** Proven by eye, four times for the ring. No
   vendor light command is needed: exposure is the brightness control.
2. **The pictures do not alternate lit / dark.** The light flashes once per exposure, so it is in every
   picture. The "pulses every other frame" reading of CR Scan's recording (JPEG sizes 85 / 40 KB) was
   wrong, or belongs to something else (two kinds of picture interleaved). The eye's flicker is the
   flash rate, 10 a second.
3. **Brightness drifts after a change.** Twice, a step's pictures changed brightness within 2.5 s: the
   scanner eases something up slowly. AUTO-TUNE and the wall test wait only about three pictures.
4. **Camera B's picture is dark at CR Scan's settings:** about 17 out of 255 at 1 ms, gain 65. Gain
   brightens it steadily without lengthening the flash.
5. **The projector's pattern may be lines, not dots.** The depth code assumes a random dot pattern; a
   pattern of parallel zig-zag lines would need a different matcher (and is how many coded-light
   scanners work). Not confirmed: the lines were faint. **Confirmed on 18 October:** vertical stripes
   with wobbling beads; see [2026-10-18-stripes.md](2026-10-18-stripes.md).
6. **Camera B only shows a picture during the white flash**, and covering camera A darkened everything:
   so Who are yoUSB?'s two windows may both have been camera A. Not confirmed.

## Still open

- Does gain change either light? (Gain test, in a dim room, watching only the light.)
- Which camera sees the projector's pattern, and is camera B a stereo partner? (Sensor map.)
- What the 17:31 run did differently.
- ~~The real pattern: dots or zig-zag lines?~~ Stripes with beads (18 October, [2026-10-18-stripes.md](2026-10-18-stripes.md)).
- The slow brightness drift: a recording with notes shows how long it takes.

Every one of these is answered by a capture: each saved picture carries its time, camera, exposure,
gain and the rest, and notes ("covered camera A") are stamped on the next picture.
