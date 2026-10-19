# Finding Creality's calibration, and pinning down the lights

Lizard Scanner's wall test is a rough stand-in for the per-scanner calibration Creality's app uses.
This page is the plan for finding the real one. **None of it has been done yet.**

## What is known, and what is only assumed

- **Known:** CR Scan reads the scanner's serial at every connect, with what looks like a general
  "read memory" command (`02 c2 00 fe 14 00 00 00`: operation 02, an address, length 0x14 = 20 bytes)
  on extension unit 4. Its answer holds `CRSL...`.
- **Known:** in both USB recordings nothing calibration-sized came off the scanner.
- **Assumed, not proven:** that a calibration exists per scanner and that CR Scan gets it by serial.
  Structured-light scanners need one, which is why. The line "downloads a per-serial calibration file
  on first connect" in Who are yoUSB?'s reports is a hint written into that tool, not a finding.
- **The catch:** neither recording was a first-ever connect, so a calibration read once and cached
  would not show in them.

It can only be in three places: on the scanner (read over USB, maybe once), on Creality's server
(downloaded by serial), or cached on disk by one of those. It is not likely to be inside the app
itself, which cannot carry every scanner's numbers.

Who are yoUSB?'s `calibration/ctrl_in_dev3_req81.bin` is not a calibration: it is every answer the
scanner gave to CR Scan's read requests in that recording, 41 bytes (the serial record, plus the
exposure, gain and auto-exposure values read back).

## Step 1: search the disk (5 minutes, read-only)

On the Windows machine (or VM) where CR Scan is installed, copy `tools/find_cr_scan_calibration.ps1`
and `tools/Find-CR-Scan-Calibration.bat` into one folder and double-click the `.bat`. Type the serial
when asked (Lizard Scanner shows it in the badge). It writes `CRScanCalibHunt_<date>` on the Desktop:
a report of the app folders, files that look like calibration or mention the serial (copied), the
newest files the app wrote, and the web addresses inside the app's programs. **Untested:** written
without a Windows machine.

## Step 2: a first-connect recording (30 minutes)

Makes the app fetch its calibration again while three recorders watch.

1. Install, if not there: USBPcap (comes with Wireshark), Wireshark, and Process Monitor (Microsoft
   Sysinternals).
2. Close CR Scan. Make it forget the scanner: rename the app's folders under `AppData\Roaming`,
   `AppData\Local` and `ProgramData` that the step 1 report listed (add `.old` to the name; rename
   back afterwards). Or use a fresh Windows user account.
3. Start the three recorders:
   - Who are yoUSB? USB recording (or Wireshark on the USBPcap interface the scanner is on).
   - Wireshark on the network interface (only the server names and sizes matter; the content will be
     encrypted).
   - Process Monitor, filter: Process Name is (CR Scan's exe), Operation is WriteFile or CreateFile.
4. Start CR Scan, connect the scanner, wait until the live picture shows, then quit.
5. Stop the recorders and save: the USB capture, the network capture, and Process Monitor's log as CSV.

What each one answers:

| What you see | Means |
|---|---|
| A large read from the scanner over USB (kilobytes, many requests to extension unit 4) | Calibration lives **on the scanner**. The read command can then be replayed read-only. |
| A download from a Creality server, then a file written | Calibration comes **from the server** and is cached in that file. |
| Neither, but a file read at connect | It was there already: that file is the calibration. |

## Step 3: JM Studio, once

The community Wine guide for JM Studio (tested on a Lizard) needed a passthrough for vendor camera
commands and a raw-USB bridge. CR Scan used neither apart from the serial read. If JM Studio really
needs raw USB with the Lizard, it does something CR Scan does not: perhaps reading calibration from
the scanner, a light command, or camera B. One Who are yoUSB? recording of JM Studio connecting,
previewing and changing brightness, compared with the CR Scan recordings, settles it. If it sends
nothing new, JM Studio adds nothing.

## Step 4: careful reads of the scanner's memory (last)

If steps 1 to 3 point at the scanner: repeat CR Scan's read with other addresses, read-only, one at a
time. The command's meaning is a guess from one example, so this comes last.

## The lights (brightness)

- Known: CR Scan's brightness control changes only exposure and gain over USB.
- Seen by the owner (15 October 2026): with CR Scan's brightness at maximum the white ring was
  "unbearably bright"; low, it was dim.
- Seen by the owner (16 October 2026): in Lizard Scanner the white ring lights with **camera B**, and
  the red infrared glow pulses with camera A.
- **Confirmed (owner, 16 October 2026, Light test):** stepping a camera's exposure from 0.1 ms to 1 ms
  dims and brightens its light, for the white ring (camera B) and the infrared (camera A) alike.
- Gain amplifies the picture and cannot drive an LED, so the ring very likely flashes for as long as
  camera B exposes (and the projector for as long as camera A does). Lizard Scanner 0.3.0 starts
  both cameras at 1 ms. Lizard Scanner 0.2.0 therefore never flashes longer than CR Scan's 1 ms unless
  you allow longer flashes, and then never past 6 ms (the camera's own starting value).
- Lizard Scanner's **Gain test** settles the rest: it holds the flash at 1 ms and steps the gain. If
  the ring changes, the firmware ties the LEDs to gain as well. Run it in the A + B view, so camera B
  (the ring) runs and follows the steps.
