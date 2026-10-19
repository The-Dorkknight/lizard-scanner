# Research notes: how comparable cameras are controlled, and what is known about the Lizard

> **Work in progress.** Mostly desk research from public sources (October 2026), plus the first real recording of
> the vendor app (see "First real recording" below).

## The question
Can the Lizard's light / projector be switched on and off without the vendor app, and what might that look like on the
wire? The working assumption was: short, **unencrypted** messages on top of ordinary USB video.

## What is known about the Lizard
- **Two plain webcams.** Creality's own troubleshooting guide tells users to test the scanner by opening "ICT CameraA" and
  "ICT CameraB" in an ordinary video player. A real Lizard run through this toolkit's device list showed the same two
  generic cameras (USB `bcdc:000a` and `bcdc:000b`, USB 2). So starting and stopping video is almost certainly standard UVC.
- **Vendor commands on top.** The one public Linux effort is a community Wine guide for JM Studio, tested on a
  "CR-Scan Lizard ICT 100W". To get the app working, its author had to add two things to Wine:
  - a passthrough for UVC **extension unit** (vendor-specific camera command) calls, and
  - a bridge for raw USB access through libusb-win32.
- **Reading of that:** the LED / projector is driven through one of those two routes. The guide doesn't say which and
  lists no commands.
- Calibration is probably downloaded per serial number by the vendor app (unconfirmed); `find-calib` and
  `find_lizard_calib.ps1` look for a cached copy.

## How comparable cameras do it

| Device | Video | LED / projector control |
|---|---|---|
| Intel RealSense D400 | Standard UVC | Extension unit with numbered controls: emitter on/off is control 2, laser power is control 4, plus a general "hardware monitor" command channel (control 1) |
| Leap Motion | Standard UVC, left and right interleaved in one frame | Reuses ordinary webcam controls: the "contrast" setting carries LED on/off, "zoom" carries exposure |
| Oculus Rift sensor | Standard UVC | Extension unit that reads and writes registers on the camera chip and image sensor |
| Sonix-based cameras (very common in cheap modules) | Standard UVC | Extension unit offering GPIO, register, I2C and flash read/write |
| Windows-Hello-style laptop infrared cameras | Standard UVC | Extension unit; the Linux tool `linux-enable-ir-emitter` finds the right control by trial and error. Microsoft's own extension unit has an "IR torch" control (0x0A) |
| Revopoint POP 2 | Not USB video: a small Linux computer with a web server | HTTP requests over WiFi |

The pattern is consistent: a short `SET_CUR` message of a few bytes to an extension unit, or a repurposed standard
control. A cheap module like the Lizard's most likely toggles a GPIO pin or writes a register this way. That is what the
Commands tab looks for: a 0/1 switch, two forms of one message differing in a byte, a checksum, a level range, and
timing relative to video start and stop.

## Safety
- **Reading is safe; writing is where risk lives.** The laptop-infrared tool's authors warn that writing random values
  to unknown extension-unit controls can corrupt a camera's firmware. This project therefore never writes by
  guessing: *Read vendor controls* only issues GET requests, and *Replay* sends what the vendor app sent (editing the
  data is allowed but warned about).
- **Burning out the infrared emitter** by overdriving it is conceivable in principle on a badly designed device, but
  the usual design is a driver chip with a fixed or firmware-limited current, so a single replayed on/off command is
  unlikely to do it. That is a guess about the hardware, not something checked, and nobody can promise it for this
  particular scanner. Firmware-upload requests (for example
  the Cypress loader request `0xA0`) are labelled and never offered for replay.

## Next steps
1. Record the vendor app (Linux host with `usbmon`, or Windows with USBPcap, see WINDOWS.md in Who are yoUSB?) while switching
   the light on and off a few times.
2. Open the capture on the **Commands** tab; look at the highlighted "possible light / projector switch" rows.
3. Replay one on Linux with *Replay + watch picture* to confirm. Name it, and report the result.

## First real recording (2026-10-15, Windows, CR Scan app)
Recorded with this toolkit's Windows support (USBPcap) while the CR Scan app started and scanned. Findings:
- **Only ICT CameraA (`bcdc:000a`) sent video:** MJPEG, 1280x720, roughly 9 frames per second, monochrome infrared
  with a visible dot pattern on surfaces. ICT CameraB (`bcdc:000b`) was asked one question and never streamed in this
  session.
- **The IR projector pulses every other frame.** Frames alternate between about 85 KB and 40 KB (bright and dark),
  matching the faint red flicker seen on the scanner. **No command switched it**: the app sent no vendor write that
  isn't a read request. The camera seems to drive the projector itself while video runs.
- **Identity read through extension unit 4:** the app writes `02 c2 00 fe 14 00 00 00` to selector 10, then reads
  selector 11 three times (8 bytes each). The answer is `00 00 00 10` + `CRSL0000000` + filler: a 16-byte record holding
  the scanner's real serial (the USB serial is a generic `<usb serial>`). This looks like a general "read memory"
  command (operation `02`, an address, length `0x14` = 20 bytes), which is probably how the app finds the per-serial
  calibration. CameraB answered all `ff`.
- **Standard settings only:** auto-exposure mode set to manual, exposure time 10 (1 ms), gain 65. On Linux these are
  plain `v4l2-ctl` controls.
- The white light ring did not come on. Untested idea: it may belong to CameraB or to a texture/colour mode.
- The Lizard has an internal USB 2 hub (`1a40:0101`); the two cameras sit behind it.

Next tests: open ICT CameraB alone (Windows Camera app or PotPlayer) and see whether the ring lights; open ICT CameraA
alone and see whether the red flicker starts without the vendor app.

## Second recording (2026-10-15): default mode, brightness and focus, then texture mode
The user previewed in default mode, moved the app's brightness and sensitivity controls, switched to texture mode (the white
light ring pulsed) and moved brightness and focus again. Findings:
- **The app's brightness control is standard exposure + gain** on CameraA: exposure 1 ms -> 0.5 ms, gain 65 -> 40 -> 30.
  Nothing else changes.
- **Switching to texture mode only restarted CameraA's video** with the same sequence as the first start (a brief
  640x480 uncompressed mode, then MJPEG 1280x720 at 10 fps) and the same exposure and gain. **No vendor command, and
  CameraB was not used.**
- So the white ring, like the IR projector, is not switched by any USB command seen so far. Best explanation: CameraA's
  firmware drives both lights itself while video runs, possibly alternating them frame by frame (the first recording's
  frames alternated bright/dark). This is a hypothesis; the per-frame timeline (`*_frames.csv`) now in the share zip,
  or watching the scanner while only CameraA is open in an ordinary camera app, will confirm or rule it out.
- **When CR Scan quit, the light turned off** (user observation). Together with "no light commands", this points to
  the lights following CameraA's video: pulsing while it streams, off when the stream stops. Hypothesis; the Scanner
  tab (start/stop camera A and B) tests it in seconds.
- The control the user moved was the app's **sensitivity**, not focus: it shows up as gain (and exposure) changes.
- Practical upshot for an open app: start CameraA's video (standard UVC), set exposure and gain with standard
  controls, and read the serial with the extension-unit read (selector 10 request, selector 11 answer).

## Sources
- [Running Creality 3D Scanners (JM Studio) on Linux with Wine](https://gist.github.com/poita66/cab19abacae3011454f1183d0e85aaeb)
- [Creality CR-Scan Lizard troubleshooting guide](https://cdn.robotshop.com/media/C/C3d/RB-C3d-40/pdf/creality3d-cr-scan-lizard-luxury-3d-scanner-troublshooting.pdf)
- [librealsense `ds-private.h`](https://github.com/IntelRealSense/librealsense/blob/master/src/ds/ds-private.h)
- [leapuvc.py (Leap Motion)](https://github.com/leapmotion/leapuvc/blob/master/Python/leapuvc.py)
- [OpenHMD Rift sensor commit](https://github.com/thaytan/OpenHMD/commit/b6a2ec2b2b9a62cd4e8cc04c9559f402a7d68d04)
- [Kurokesu Sonix test tool](https://github.com/Kurokesu/C1_SONIX_Test_AP/blob/master/README.md)
- [linux-enable-ir-emitter](https://github.com/EmixamPP/linux-enable-ir-emitter)
- [Revopoint POP 2 Linux info](https://github.com/frostworx/revopoint-pop2-linux-info)
- [Linux UVC driver documentation](https://www.kernel.org/doc/Documentation/userspace-api/media/drivers/uvcvideo.rst)
- [USBPcap capture format](https://desowin.org/usbpcap/captureformat.html) (for Windows captures)
