# Third-party notices

The licence in [LICENSE.md](LICENSE.md) covers only Matthew Armstrong's own code in this repository.
Other software keeps its own licence:

| What | Used for | Licence |
|---|---|---|
| [Python](https://www.python.org/) and its tkinter | Runs the app and its window | PSF License |
| [NumPy](https://numpy.org/) | Picture maths, calibration, depth | BSD-3-Clause |
| [Pillow](https://python-pillow.org/) | JPEG decoding, pictures in the window | MIT-CMU (HPND) |
| [OpenCV](https://opencv.org/) (Windows and macOS only) | Camera access | Apache-2.0 |
| [v4l-utils](https://git.linuxtv.org/v4l-utils.git) (`v4l2-ctl`, Linux) | Camera access, run as a separate program | GPL-2.0 / LGPL-2.1 |

None of their code is copied into this repository; they are installed separately.

Creality and CR-Scan Lizard are names of Creality (Shenzhen Creality 3D Technology Co., Ltd.). This
project is not made by, endorsed by or affiliated with Creality. The scanner's USB behaviour was
learned by recording the traffic of Creality's own app on the owner's computer (see
[docs/research](docs/research/README.md)); none of Creality's software is included or copied.
The phase-correlation and block-matching methods are textbook techniques; the structured-light
idea is the one the original Microsoft Kinect used.
