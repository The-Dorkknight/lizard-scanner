#!/usr/bin/env python3
"""
lizard_capture.py - read USB recordings of a scanner and rebuild its video frames.

Extracted from Who are yoUSB? 0.4.0-wip (https://github.com/The-Dorkknight/who-are-yousb). Standard library only.
Reads Linux usbmon captures (pcap link type 189/220) and Windows USBPcap captures (link type 249, converted on the fly),
pcap or pcapng. Rebuilds UVC video frames from per-packet headers (isochronous and bulk), so a scanner app can study a
recording of the vendor app frame by frame. WORK IN PROGRESS.

Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
Licensed under the PolyForm Noncommercial License 1.0.0. No warranty, no liability.

Quick use:
    from lizard_capture import video_frames
    for dev, ep, t, jpeg_or_raw in video_frames("usb_capture.pcap"):
        ...
"""
import heapq, os, re, struct, sys

USBMON = struct.Struct("<QcBBBHccqiiII8s")   # 48 bytes
USBMON_MM = struct.Struct("<iiII")           # +16 for linktype 220
def iter_raw_packets(path):
    """(linktype, time, bytes) exactly as stored in the file."""
    with open(path, "rb") as f:
        head = f.read(4)
        if head == b"\x0a\x0d\x0d\x0a":
            yield from _iter_pcapng(f, head)
        else:
            yield from _iter_pcap(f, head)


def _iter_pcap(f, magic):
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        e = "<"
    elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        e = ">"
    else:
        sys.exit("Not a pcap/pcapng file.")
    nsec = magic in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d")
    lt = struct.unpack(e + "HHiIII", f.read(20))[5]
    rec = struct.Struct(e + "IIII")
    while True:
        h = f.read(16)
        if len(h) < 16:
            return
        s, frac, incl, _ = rec.unpack(h)
        data = f.read(incl)
        yield lt, s + frac / (1e9 if nsec else 1e6), data


def _iter_pcapng(f, head):
    rest = f.read(8)
    e = "<" if rest[4:8] == b"\x4d\x3c\x2b\x1a" else ">"
    tl = struct.unpack(e + "I", rest[:4])[0]
    f.read(tl - 12)
    lts = []
    while True:
        h = f.read(8)
        if len(h) < 8:
            return
        bt, tl = struct.unpack(e + "II", h)
        body = f.read(tl - 8)
        if bt == 0x0A0D0D0A:
            lts = []
        elif bt == 1:
            lts.append(struct.unpack(e + "H", body[:2])[0])
        elif bt == 6 and lts:
            iid, hi, lo, cap, _ = struct.unpack(e + "IIIII", body[:20])
            yield lts[iid], ((hi << 32) | lo) / 1e6, body[20:20 + cap]
        elif bt == 3 and lts:
            yield lts[0], 0.0, body[4:]


def iter_packets(path):
    """Like iter_raw_packets, but Windows USBPcap captures (link type 249) are rewritten on the fly as Linux
    usbmon records (link type 220), so every analyser below reads captures from both systems."""
    conv = None
    for lt, ts, pkt in iter_raw_packets(path):
        if lt != LINKTYPE_USBPCAP:
            yield lt, ts, pkt
            continue
        if conv is None:
            conv = UsbPcapConverter()
        for rec in conv.feed(ts, pkt):
            yield 220, ts, rec


LINKTYPE_USBPCAP = 249
USBPCAP_HDR = struct.Struct("<HQIHBHHBBI")  # headerLen irpId status function info bus device endpoint transfer dataLen
class UsbPcapConverter:
    """USBPcap record -> usbmon record. Control transfers are stitched from their SETUP and COMPLETE packets
    (USBPcap 1.5 and newer) and from the older SETUP / DATA / STATUS layout. Isochronous OUT traffic is dropped."""

    def __init__(self):
        self.ctrl = {}
        self.devs = {}   # (bus, address) -> number used in the usbmon record

    def dev_id(self, bus, addr):
        key = (bus, addr)
        if key not in self.devs:
            used = set(self.devs.values())
            n = addr & 0xFF
            while n in used:
                n = 128 + (n + 1) % 127  # another root hub reused this address: give it a free number
            self.devs[key] = n
        return self.devs[key]

    @staticmethod
    def mon(uid, typ, xt, ep, dev, bus, setup, data, ts, status, length, ndesc=0, descs=b""):
        sec = int(ts)
        return (USBMON.pack(uid & 0xFFFFFFFFFFFFFFFF, typ, xt, ep, dev, bus & 0xFFFF,
                            b"\x00" if setup is not None else b"-", b"=" if data else b">", sec,
                            int((ts - sec) * 1e6), status, length, len(descs) + len(data), setup or b"\0" * 8)
                + USBMON_MM.pack(0, 0, 0, ndesc) + descs + data)

    @staticmethod
    def errno(status):
        if not status & 0x80000000:  # USBD_STATUS: top bit set = error
            return 0
        return -32 if status == 0xC0000004 else -71  # stall -> EPIPE, anything else -> EPROTO

    def feed(self, ts, pkt):
        if len(pkt) < USBPCAP_HDR.size:
            return
        hl, irp, status, _func, info, bus, addr, ep, xt, dlen = USBPCAP_HDR.unpack_from(pkt)
        if xt not in (0, 1, 2, 3) or hl < USBPCAP_HDR.size or hl > len(pkt):
            return  # IRP information (0xFE) or a request USBPcap couldn't decode (0xFF): no transfer data
        dev, body, done = self.dev_id(bus, addr), pkt[hl:], bool(info & 1)
        if xt == 2:
            stage = pkt[USBPCAP_HDR.size] if hl > USBPCAP_HDR.size else 0
            if not done:
                if stage == 0 and len(body) >= 8:
                    c = self.ctrl[irp] = dict(setup=bytes(body[:8]), out=bytes(body[8:]), inb=b"",
                                              sent=True, at=(irp, ep, dev, bus, ts))
                    wl = struct.unpack_from("<H", c["setup"], 6)[0]
                    if wl and not (c["setup"][0] & 0x80) and not c["out"]:
                        c["sent"] = False  # older USBPcap: the OUT data follows in its own packet; wait for it
                    else:
                        yield self.mon(irp, b"S", 2, ep, dev, bus, c["setup"], c["out"], ts, 0, wl)
                elif stage == 1 and irp in self.ctrl:
                    c = self.ctrl[irp]
                    c["out"] += bytes(body)
                    if not c["sent"]:
                        c["sent"] = True
                        i, e, d, b, t0 = c["at"]
                        yield self.mon(i, b"S", 2, e, d, b, c["setup"], c["out"], t0, 0, len(c["out"]))
                return
            c = self.ctrl.get(irp)
            if stage == 1:
                if c is not None:
                    c["inb"] += bytes(body)  # older layout: IN data arrives before the status stage
                return
            c = self.ctrl.pop(irp, None)
            if c is not None and not c["sent"]:
                i, e, d, b, t0 = c["at"]
                yield self.mon(i, b"S", 2, e, d, b, c["setup"], c["out"], t0, 0, len(c["out"]))
            data = (c["inb"] if c else b"") + (bytes(body) if stage in (2, 3, 0) else b"")
            yield self.mon(irp, b"C", 2, ep, dev, bus, None, data, ts, self.errno(status), len(data))
            return
        if xt == 0:
            if not done or not (ep & 0x80):
                return
            n = struct.unpack_from("<I", pkt, 31)[0] if hl >= 39 else 0
            descs = b""
            for k in range(n):
                if 39 + 12 * (k + 1) > hl:
                    break
                off, ln, st = struct.unpack_from("<III", pkt, 39 + 12 * k)
                descs += struct.pack("<iIII", self.errno(st), off, ln, 0)
            yield self.mon(irp, b"C", 0, ep, dev, bus, None, bytes(body), ts, self.errno(status), len(body),
                           len(descs) // 16, descs)
            return
        is_in = bool(ep & 0x80)
        if not done:
            data = b"" if is_in else bytes(body)
            yield self.mon(irp, b"S", xt, ep, dev, bus, None, data, ts, 0, 0 if is_in else dlen)
        else:
            data = bytes(body) if is_in else b""
            yield self.mon(irp, b"C", xt, ep, dev, bus, None, data, ts, self.errno(status),
                           dlen if is_in else 0)


def write_pcap(path, linktype, records):
    """records: iterable of (time, bytes)."""
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 262144, linktype))
        for ts, data in records:
            sec = int(ts)
            f.write(struct.pack("<IIII", sec, int((ts - sec) * 1e6), len(data), len(data)))
            f.write(data)


def merge_pcaps(paths, out):
    """Merge several USBPcap files (one per root hub) into one, ordered by time, streaming."""
    def gen(p):
        m = re.search(r"hub(\d+)\.pcap$", os.path.basename(p))
        hub = int(m.group(1)) if m else None
        for lt, ts, data in iter_raw_packets(p):
            if hub is not None and lt == LINKTYPE_USBPCAP and len(data) >= USBPCAP_HDR.size:
                # injected descriptors carry the hub number from the device name, live packets the driver's own
                # bus id; use one number for both so a device keeps one identity
                data = data[:17] + struct.pack("<H", hub) + data[19:]
            yield ts, lt, data
    n = 0

    def records():
        nonlocal n
        for ts, lt, data in heapq.merge(*[gen(p) for p in paths], key=lambda r: r[0]):
            n += 1
            yield ts, data
    write_pcap(out, LINKTYPE_USBPCAP, records())
    return n


class UvcFrames:
    """Rebuild video frames from UVC payloads. Every payload (each isochronous packet, or each bulk transfer)
    starts with its own 2-12 byte header: bit 0 = frame toggle (FID), bit 1 = end of frame, bit 6 = error."""

    def __init__(self, path):
        self.f = open(path, "wb")
        self.cur, self.fid, self.err, self.t = bytearray(), None, False, None
        self.frames = []  # (time, offset, size, error)

    @staticmethod
    def is_payload(u):
        return len(u) >= 2 and 2 <= u[0] <= 12 and u[1] & 0x80 and len(u) >= u[0]

    def feed(self, t, u):
        info = u[1]
        if self.fid is not None and (info & 1) != self.fid:
            self.close_frame()
        self.fid = info & 1
        if info & 0x40:
            self.err = True
        if len(u) > u[0]:
            if self.t is None:
                self.t = t
            self.cur += u[u[0]:]
        if info & 0x02:
            self.close_frame()

    def close_frame(self):
        if self.cur:
            self.frames.append((self.t, self.f.tell(), len(self.cur), self.err, bytes(self.cur[:2])))
            self.f.write(self.cur)
        self.cur, self.err, self.t = bytearray(), False, None

    def close(self):
        self.close_frame()
        self.f.close()


def jpeg_size(b):
    """(width, height) from a JPEG's start-of-frame marker, or None."""
    i = 2
    while i + 9 < len(b) and b[i] == 0xFF:
        m, ln = b[i + 1], struct.unpack_from(">H", b, i + 2)[0]
        if m in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack_from(">HH", b, i + 5)
            return w, h
        i += 2 + ln
    return None


def alternation(values):
    """Share of neighbouring frames that flip between a high and a low group, and the high/low ratio.
    A light pulsing every other frame gives a share near 1."""
    if len(values) < 6:
        return 0.0, 1.0
    srt = sorted(values)
    mid = (srt[len(srt) // 2 - 1] + srt[len(srt) // 2]) / 2
    hi = [v for v in values if v > mid] or [mid]
    lo = [v for v in values if v <= mid] or [mid]
    flips = sum(1 for x, y in zip(values, values[1:]) if (x > mid) != (y > mid))
    return flips / (len(values) - 1), (sum(hi) / len(hi)) / max(sum(lo) / len(lo), 1e-9)



def video_frames(path, keep_dir=None):
    """Yield (device number, endpoint, time in s, frame bytes) for every UVC video frame in a recording.
    Frames are rebuilt from the per-packet UVC headers (frame-toggle / end-of-frame bits)."""
    import tempfile
    tmp = keep_dir or tempfile.mkdtemp(prefix="lizard_frames_")
    builders, t0 = {}, None
    for lt, ts, pkt in iter_packets(path):
        if lt not in (189, 220):
            raise ValueError(f"link type {lt} is not a USB capture")
        hl = 64 if lt == 220 else 48
        if len(pkt) < hl:
            continue
        (uid, typ, xt, epn, dev, bus, fsetup, fdata, s, us, status, length, lcap, setup) = USBMON.unpack_from(pkt)
        t = s + us / 1e6
        t0 = t if t0 is None else t0
        if typ != b"C" or not (epn & 0x80) or xt not in (0, 3):
            continue
        data = pkt[hl:hl + lcap]
        if xt == 0:
            nd = USBMON_MM.unpack_from(pkt, 48)[3] if lt == 220 else 0
            descs, body = data[:nd * 16], data[nd * 16:]
            units = [body[o:o + ln] for st, o, ln, _ in (struct.unpack_from("<iIII", descs, k * 16) for k in range(nd))
                     if st == 0 and ln]
        else:
            units = [data]
        for u in units:
            if not UvcFrames.is_payload(u):
                continue
            key = (dev, epn)
            if key not in builders:
                builders[key] = UvcFrames(os.path.join(tmp, f"dev{dev}_ep{epn:02x}.frames"))
            fr = builders[key]
            before = len(fr.frames)
            fr.feed(t - t0, u)
            if len(fr.frames) > before:
                fr.f.flush()
                yield from _read_new(key, fr, before)
    for key, fr in builders.items():
        before = len(fr.frames)
        fr.close_frame()
        fr.f.flush()
        yield from _read_new(key, fr, before)
        fr.f.close()


def _read_new(key, fr, before):
    with open(fr.f.name, "rb") as fh:
        for t, off, size, err, _head in fr.frames[before:]:
            fh.seek(off)
            yield key[0], key[1], t, fh.read(size)


if __name__ == "__main__":
    import collections
    counts = collections.Counter()
    for dev, ep, t, frame in video_frames(sys.argv[1]):
        counts[(dev, ep, "jpeg" if frame[:2] == b"\xff\xd8" else "raw")] += 1
    for (dev, ep, kind), n in sorted(counts.items()):
        print(f"device {dev} endpoint 0x{ep:02x}: {n} {kind} frames")
