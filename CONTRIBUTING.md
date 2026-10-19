# Contributing

Thank you for wanting to help. This is a work-in-progress hobby project.

- **Real-hardware reports are the most useful thing right now.** If you have a CR-Scan Lizard, run the
  wall test and open an issue with `calibration/<serial>/calibration_report.txt` (you may blank out
  the serial) and what the depth view looked like.
- Pull requests are welcome. Run `python3 -m pytest tests/` first; the window tests need a display
  (`xvfb-run -a python3 -m pytest tests/` on a headless Linux box).
- Keep it light: it must run on a 7th-generation Core i3 with no graphics card, and on Linux with
  numpy and Pillow only.
- By contributing you agree that your contribution is licensed under the same
  [PolyForm Noncommercial 1.0.0](LICENSE.md) licence as the rest of the project.
