"""Tests for lizard_framecap (standard library unittest; run: python3 -m unittest discover tests)."""
import json, os, struct, sys, tempfile, threading, unittest, zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import lizard_framecap as fc  # noqa: E402


def fake_jpeg(w=1280, h=720, body=200):
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"\x00\x00" + sof + bytes(body) + b"\xff\xd9"


class Clock:
    def __init__(self):
        self.t = 1_760_000_000.0

    def __call__(self):
        self.t += 0.1
        return self.t


def png_pixels(blob):
    """Decode our own 8-bit grey PNG back to (w, h, bytes)."""
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", blob[16:24])
    i, data = 8, b""
    while i < len(blob):
        ln = struct.unpack(">I", blob[i:i + 4])[0]
        t = blob[i + 4:i + 8]
        if t == b"IDAT":
            data += blob[i + 8:i + 8 + ln]
        i += 12 + ln
    raw = zlib.decompress(data)
    rows = [raw[r * (w + 1) + 1:(r + 1) * (w + 1)] for r in range(h)]
    return w, h, b"".join(rows)


class FrameLoggerTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_frames_carry_settings_in_force(self):
        log = fc.FrameLogger(self.root, label="ring test", app="test", clock=Clock())
        log.snapshot_settings("A", [{"name": "exposure_time_absolute", "value": 10}, {"name": "gain", "value": 65},
                                    {"name": "auto_exposure", "value": 1}])
        log.add_frame("A", fake_jpeg(), "jpeg", level=40, lit=True, fps=9.8)
        log.setting_changed("A", "gain", 40)
        log.setting_changed("A", "exposure_time_absolute", 5)
        log.add_frame("A", fake_jpeg(), "jpeg", level=20)
        folder = log.close()
        session, rows, events = fc.read_capture(folder)
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]["exposure_100us"], rows[0]["exposure_ms"], rows[0]["gain"]), ("10", "1.0", "65"))
        self.assertEqual((rows[1]["exposure_ms"], rows[1]["gain"]), ("0.5", "40"))
        self.assertEqual((rows[0]["width"], rows[0]["height"]), ("1280", "720"))
        self.assertEqual(rows[0]["lit"], "1")
        self.assertTrue(int(rows[1]["settings_rev"]) > int(rows[0]["settings_rev"]))
        self.assertTrue(os.path.exists(os.path.join(folder, rows[0]["file"])))
        self.assertIn("ring-test", os.path.basename(folder))
        kinds = [e["event"] for e in events]
        self.assertEqual(kinds[0], "capture_started")
        self.assertEqual(kinds[-1], "capture_stopped")
        self.assertEqual(kinds.count("setting_changed"), 2)
        self.assertEqual(session["frames"], {"A": 2})

    def test_grey_frames_saved_as_png_and_level_computed(self):
        log = fc.FrameLogger(self.root, clock=Clock())
        pix = bytes((x * 3) % 256 for x in range(64 * 48))
        name = log.add_frame("B", pix, "grey", 64, 48)
        folder = log.close()
        with open(os.path.join(folder, "frames", name), "rb") as f:
            w, h, back = png_pixels(f.read())
        self.assertEqual((w, h, back), (64, 48, pix))
        _, rows, _ = fc.read_capture(folder)
        self.assertAlmostEqual(float(rows[0]["level"]), fc.grey_level(pix), places=1)

    def test_every_nth_and_max_frames(self):
        log = fc.FrameLogger(self.root, every=3, max_frames=4, clock=Clock())
        saved = [log.add_frame("A", fake_jpeg(), "jpeg") for _ in range(20)]
        self.assertEqual([i for i, s in enumerate(saved) if s], [0, 3, 6, 9])
        self.assertTrue(log.full())
        log.close()

    def test_marker_stamps_next_frame_only(self):
        log = fc.FrameLogger(self.root, clock=Clock())
        log.mark("covered camera A")
        log.add_frame("A", fake_jpeg(), "jpeg")
        log.add_frame("A", fake_jpeg(), "jpeg")
        _, rows, events = fc.read_capture(log.close())
        self.assertEqual([r["marker"] for r in rows], ["covered camera A", ""])
        self.assertIn("marker", [e["event"] for e in events])

    def test_serial_hidden_by_default(self):
        log = fc.FrameLogger(self.root, clock=Clock())
        log.camera_info("A", node="/dev/video2", vid=0xBCDC, pid=0x000A, serial="CRSL1234567")
        folder = log.close()
        text = ""
        for name in ("session.json", "events.jsonl"):
            with open(os.path.join(folder, name)) as f:
                text += f.read()
        self.assertNotIn("1234567", text)
        self.assertIn("CRSL*******", text)
        self.assertIn('"bcdc"', text)

    def test_refused_setting_is_logged_but_not_applied(self):
        log = fc.FrameLogger(self.root, clock=Clock())
        log.snapshot_settings("A", {"gain": 65})
        log.setting_changed("A", "gain", 999, ok=False, error="out of range")
        log.add_frame("A", fake_jpeg(), "jpeg")
        _, rows, events = fc.read_capture(log.close())
        self.assertEqual(rows[0]["gain"], "65")
        self.assertEqual([e for e in events if e["event"] == "setting_changed"][0]["error"], "out of range")

    def test_threads_and_closed_logger(self):
        log = fc.FrameLogger(self.root)
        ts = [threading.Thread(target=lambda c=c: [log.add_frame(c, fake_jpeg(), "jpeg") for _ in range(50)])
              for c in "AB"]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        folder = log.close()
        self.assertIsNone(log.add_frame("A", fake_jpeg(), "jpeg"))
        _, rows, _ = fc.read_capture(folder)
        self.assertEqual(len(rows), 100)
        self.assertEqual(sorted({r["camera"] for r in rows}), ["A", "B"])
        self.assertEqual(len(os.listdir(os.path.join(folder, "frames"))), 100)

    def test_summary_groups_by_settings(self):
        log = fc.FrameLogger(self.root, clock=Clock())
        log.snapshot_settings("B", {"exposure_time_absolute": 1, "gain": 65})
        log.add_frame("B", fake_jpeg(), "jpeg", level=5)
        log.setting_changed("B", "exposure_time_absolute", 10)
        log.add_frame("B", fake_jpeg(), "jpeg", level=17)
        log.mark("ring clearly brighter")
        text = fc.summarise(log.close())
        self.assertIn("exposure 0.1 ms  gain 65: 1 frames, brightness 5.0", text)
        self.assertIn("exposure 1.0 ms  gain 65: 1 frames, brightness 17.0", text)
        self.assertIn("ring clearly brighter", text)

    def test_two_captures_same_second_get_separate_folders(self):
        c = Clock()
        fixed = lambda: c.t  # noqa: E731
        a = fc.FrameLogger(self.root, clock=fixed)
        b = fc.FrameLogger(self.root, clock=fixed)
        self.assertNotEqual(a.folder, b.folder)
        a.close()
        b.close()

    def test_events_are_valid_json_lines(self):
        log = fc.FrameLogger(self.root, clock=Clock())
        log.event("stream_started", camera="A", mode="mjpg")
        folder = log.close()
        with open(os.path.join(folder, "events.jsonl")) as f:
            for line in f:
                json.loads(line)


if __name__ == "__main__":
    unittest.main()


class ExtraColumnsTest(unittest.TestCase):
    """0.1.1: optional extra columns at the end of frames.csv (used by Lizard Scanner)."""

    def test_extra_columns_are_appended_and_filled(self):
        root = tempfile.mkdtemp()
        log = fc.FrameLogger(root, clock=Clock(), extra_columns=("detail", "dots", "gain"))
        log.add_frame("A", fake_jpeg(), "jpeg", extra={"detail": 3.25, "dots": 0.071})
        log.add_frame("A", fake_jpeg(), "jpeg")
        folder = log.close()
        with open(os.path.join(folder, "frames.csv")) as f:
            header = f.readline().strip().split(",")
        self.assertEqual(header[:len(fc.CSV_COLUMNS)], fc.CSV_COLUMNS)
        self.assertEqual(header[len(fc.CSV_COLUMNS):], ["detail", "dots"])   # "gain" is already a column
        _, rows, _ = fc.read_capture(folder)
        self.assertEqual((rows[0]["detail"], rows[0]["dots"]), ("3.25", "0.071"))
        self.assertEqual((rows[1]["detail"], rows[1]["dots"]), ("", ""))

    def test_without_extra_columns_the_file_is_unchanged(self):
        log = fc.FrameLogger(tempfile.mkdtemp(), clock=Clock())
        folder = log.close()
        with open(os.path.join(folder, "frames.csv")) as f:
            self.assertEqual(f.readline().strip().split(","), fc.CSV_COLUMNS)
