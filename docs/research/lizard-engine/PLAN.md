# Lizard: open questions and next steps (2026-10-15)

## 1. What dims the light ring?
- **Seen:** in CR Scan (Windows) the brightness control dimmed the ring until it barely lit. Over USB, CR Scan only sent
  exposure (10 → 5, i.e. 1 ms → 0.5 ms) and gain (65 → 40 → 30). No vendor command.
- **Seen on Linux:** moving "brightness" in the Scanner tab only changed the picture. That control is image processing
  (offset), so this was expected.
- **Hypothesis:** the ring flashes only while the sensor exposes, so **exposure = flash length**. At 10 fps a 0.5 ms
  flash is on 0.5% of the time and looks very dim.
- **Test:** Scanner tab, camera A running, move the ★ *exposure time absolute* slider from 1 up to about 50. The
  first move switches auto-exposure to manual. The ring should go from barely lit to bright.
- If exposure does nothing: check whether CR Scan's mode (1280x720 MJPEG) is needed, then read the vendor controls
  (Commands tab, read-only) for an unknown extension-unit selector.

## 2. Camera B
- Never streamed by CR Scan in either recording (default and texture mode). Both cameras stream fine in the Scanner tab.
- **Test:** start B alone and note which lights run, then compare its picture with A's (second IR view for stereo, or
  a texture camera?).

## 3. Calibration
- CR Scan reads the scanner serial at connect, probably to download or open a per-serial calibration file.
- **Next:** `find_lizard_calib.ps1` on the Windows PC (no install) or `whoareyousb.py find-calib` on its files.
  Without calibration, depth from the IR dot pictures needs a self-calibration step.

## 4. Engine work for the multi-scanner app
- Add full-resolution MJPEG frame delivery (keep bytes in `CamStream`, decode in the app).
- Adapter class next to the RealSense one (see HANDOFF.md table).
- Optional: Windows support through OpenCV (`cv2.VideoCapture` with DirectShow, exposure/gain properties).
- Optional: careful read-only "memory peek" around the address CR Scan reads (`02 c2 00 fe 14 …`).
