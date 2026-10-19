#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# retro_skin.py -- optional "wood-panelled television" look for Scantastic
# https://github.com/The-Dorkknight/scantastic
#
# Everything here is drawn by this file (wood grain, bezel, screen static). No
# picture files are used and nothing is copied from any real product.
#
# Licensed under the PolyForm Noncommercial License 1.0.0
# https://polyformproject.org/licenses/noncommercial/1.0.0/
#
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# No warranty. See LICENSE.md.
#
"""
The scanner looks for this module when it starts. If it is missing, or
anything in it fails, the app falls back to its plain look, so the skin can
never stop the scanner working. Start the app with --plain to switch it off.

Cost: the wood is drawn once per window size. The static is a handful of
pictures made once per screen size and then just swapped on a timer, which
costs next to nothing; the timer stops the moment a camera picture or the 3D
view is shown.
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

SKIN_VERSION = "0.5.0-wip"

# Walnut cabinet, black plastic, cream lettering, one amber lamp.
PALETTE = dict(
    wood_dark=(58, 33, 17), wood_mid=(104, 63, 33), wood_light=(146, 95, 53),
    bezel="#191817", bezel_edge="#3b3835",
    panel="#262220", panel_raised="#38322d", panel_line="#4d453e", well="#141211",
    text="#ece2cc", muted="#a79d89", disabled="#6c6458",
    amber="#e5a531", live="#57c46a",
    screen=(9, 12, 11),
)

STATIC_FRAMES = 8          # pictures in the static loop
STATIC_INTERVAL_MS = 90    # about 11 changes a second
STATIC_GRAIN = 3           # one speck of static covers this many screen pixels
CORNER_RADIUS = 26         # rounded screen corners, in pixels


def _rgb(hex_colour):
    hex_colour = hex_colour.lstrip("#")
    return tuple(int(hex_colour[i:i + 2], 16) for i in (0, 2, 4))


def _font(px, bold=True):
    names = (["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf", "segoeuib.ttf"]
             if bold else ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf", "arial.ttf", "segoeui.ttf"])
    for name in names:
        try:
            return ImageFont.truetype(name, px)
        except (OSError, ValueError):
            continue
    try:
        return ImageFont.load_default(size=px)
    except TypeError:
        return ImageFont.load_default()


# ===========================================================================
# Wood
# ===========================================================================

def _stretch(rng, cols, rows, size):
    """Random values on a coarse grid, smoothly stretched to `size` (w, h)."""
    grid = (rng.random((max(2, rows), max(2, cols))) * 255).astype(np.uint8)
    return np.asarray(Image.fromarray(grid).resize(size, Image.BICUBIC), np.float32) / 255.0


def wood_image(width, height, seed=7):
    """A walnut panel with the grain running left to right, as a PIL image.
    Worked out at a third of the size and stretched, so it is quick."""
    width, height = max(8, int(width)), max(8, int(height))
    rng = np.random.default_rng(seed)
    w, h = max(8, width // 3), max(8, height // 3)
    # Long streaks: very coarse along the grain, fine across it.
    streaks = (0.50 * _stretch(rng, w // 70, h // 2, (w, h))
               + 0.32 * _stretch(rng, w // 28, h, (w, h))
               + 0.18 * _stretch(rng, w // 9, h, (w, h)))
    # Growth rings: bands across the grain, bent by a slow wobble.
    y = np.arange(h, dtype=np.float32)[:, None]
    wobble = _stretch(rng, w // 110, h // 30, (w, h))
    rings = 0.5 + 0.5 * np.sin(y / 5.5 + wobble * 9.0)
    tone = np.clip(0.68 * streaks + 0.32 * rings, 0, 1) ** 1.15
    dark, mid, light = (np.float32(PALETTE[k]) for k in ("wood_dark", "wood_mid", "wood_light"))
    lower = dark + (mid - dark) * np.clip(tone * 2, 0, 1)[..., None]
    upper = (light - mid) * np.clip(tone * 2 - 1, 0, 1)[..., None]
    rgb = lower + upper
    # The cabinet's edges fall away into shadow.
    xx = np.minimum(np.arange(w), np.arange(w)[::-1]).astype(np.float32)[None, :]
    yy = np.minimum(np.arange(h), np.arange(h)[::-1]).astype(np.float32)[:, None]
    edge = np.clip(np.minimum(xx, yy) / 5.0, 0, 1)
    rgb *= (0.72 + 0.28 * edge)[..., None]
    small = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    return small.resize((width, height), Image.BICUBIC)


class Backdrop:
    """Keeps a wood picture behind everything packed into a frame, redrawn
    when the frame changes size. (A label placed to fill the frame and pushed
    to the back; the frame's other children are laid out over it as usual.)"""

    def __init__(self, frame):
        import tkinter as tk
        self.frame = frame
        self.label = tk.Label(frame, borderwidth=0, highlightthickness=0, bg=frame.cget("bg"))
        self.label.place(x=0, y=0, relwidth=1, relheight=1)
        self.label.lower()
        self._photo = None
        self._size = None
        self._pending = None
        frame.bind("<Configure>", self._changed, add="+")

    def _changed(self, event=None):
        if event is not None and event.widget is not self.frame:
            return
        if self._pending is None:
            self._pending = self.frame.after(60, self.refresh)

    def refresh(self):
        self._pending = None
        size = (self.frame.winfo_width(), self.frame.winfo_height())
        if size == self._size or min(size) < 8:
            return
        self._size = size
        self._photo = ImageTk.PhotoImage(wood_image(*size))
        self.label.configure(image=self._photo)


# ===========================================================================
# Screen: rounded corners and static
# ===========================================================================

def corner_masks(radius=CORNER_RADIUS):
    """Four small see-through pictures (top-left, top-right, bottom-left,
    bottom-right) that round off the screen's corners in the bezel colour."""
    r = int(radius)
    big = Image.new("L", (r * 4, r * 4), 255)
    ImageDraw.Draw(big).ellipse((0, 0, r * 8 - 1, r * 8 - 1), fill=0)
    alpha = big.resize((r, r), Image.LANCZOS)            # opaque outside the curve
    tile = Image.new("RGBA", (r, r), _rgb(PALETTE["bezel"]) + (255,))
    tile.putalpha(alpha)
    return [tile, tile.transpose(Image.FLIP_LEFT_RIGHT), tile.transpose(Image.FLIP_TOP_BOTTOM),
            tile.transpose(Image.ROTATE_180)]


def static_frames(width, height, lines=(), count=STATIC_FRAMES, seed=3):
    """`count` pictures of television static for a screen of this size, with
    faint scan lines, darker edges, rounded corners and (optionally) a small
    plate of text in the middle. Returns PIL images."""
    width, height = max(16, int(width)), max(16, int(height))
    rng = np.random.default_rng(seed)
    gw, gh = max(4, width // STATIC_GRAIN), max(4, height // STATIC_GRAIN)
    # Shading shared by every frame: scan lines and a darker rim.
    rows = np.where(np.arange(height) % 3 == 2, 0.72, 1.0).astype(np.float32)[:, None]
    yy = np.linspace(-1, 1, height, dtype=np.float32)[:, None]
    xx = np.linspace(-1, 1, width, dtype=np.float32)[None, :]
    rim = np.clip(1.08 - 0.42 * (xx * xx * xx * xx + yy * yy * yy * yy), 0.35, 1.0)
    shade = (rows * rim)[..., None] * np.float32([0.92, 1.0, 0.97])      # a slightly green-grey tube
    corners = corner_masks(min(CORNER_RADIUS, width // 4, height // 4))
    plate = _text_plate(lines) if lines else None
    frames = []
    for _ in range(count):
        specks = rng.integers(18, 215, (gh, gw), dtype=np.uint8)
        specks[rng.random((gh, gw)) < 0.06] = 245                       # a few bright sparks
        band = int(rng.integers(0, gh))
        specks[band:band + max(1, gh // 28)] //= 2                      # one rolling dark band
        big = np.asarray(Image.fromarray(specks).resize((width, height), Image.NEAREST), np.float32)
        img = Image.fromarray(np.clip(big[..., None] * shade, 0, 255).astype(np.uint8))
        if plate is not None:
            img.paste(plate, ((width - plate.width) // 2, (height - plate.height) // 2), plate)
        r = corners[0].width
        for tile, pos in zip(corners, ((0, 0), (width - r, 0), (0, height - r), (width - r, height - r))):
            img.paste(tile, pos, tile)
        frames.append(img)
    return frames


def _text_plate(lines):
    """A dark rounded plate holding the screen's message, so it stays readable over the static."""
    big, small = _font(22, True), _font(14, False)
    fonts = [big] + [small] * (len(lines) - 1)
    probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    sizes = [probe.textbbox((0, 0), text, font=font) for text, font in zip(lines, fonts)]
    widths = [b[2] - b[0] for b in sizes]
    heights = [b[3] - b[1] for b in sizes]
    pad, gap = 20, 10
    w = max(widths) + 2 * pad
    h = sum(heights) + gap * (len(lines) - 1) + 2 * pad
    plate = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(plate)
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=12, fill=(10, 11, 10, 225),
                           outline=_rgb(PALETTE["panel_line"]) + (255,))
    y = pad
    for text, font, box, tw, th in zip(lines, fonts, sizes, widths, heights):
        colour = _rgb(PALETTE["text"] if font is big else PALETTE["muted"]) + (255,)
        draw.text(((w - tw) // 2 - box[0], y - box[1]), text, font=font, fill=colour)
        y += th + gap
    return plate


class Static:
    """Shows static on a canvas while nothing else is on the screen.

    start() draws it and (unless animate is False) starts a timer that swaps
    between pictures made in advance. stop() ends the timer. Whoever draws
    something else on the canvas just calls stop() first."""

    def __init__(self, canvas, lines=(), animate=True):
        self.canvas = canvas
        self.lines = tuple(lines)
        self.animate = bool(animate)
        self.running = False
        self.ticks = 0
        self._photos = []
        self._size = None
        self._item = None
        self._timer = None
        self._index = 0
        canvas.bind("<Configure>", self._resized, add="+")

    def _build(self):
        size = (max(self.canvas.winfo_width(), 16), max(self.canvas.winfo_height(), 16))
        if size != self._size or not self._photos:
            self._size = size
            count = STATIC_FRAMES if self.animate else 1
            self._photos = [ImageTk.PhotoImage(img) for img in static_frames(*size, lines=self.lines, count=count)]

    def set_lines(self, lines):
        """Change the text shown on the static; the pictures are made again when it changes."""
        lines = tuple(lines)
        if lines != self.lines:
            self.lines = lines
            self._photos = []
            if self.running:
                self.start()

    def start(self):
        self._build()
        self.canvas.delete("all")
        self._item = self.canvas.create_image(0, 0, anchor="nw", image=self._photos[self._index % len(self._photos)])
        if not self.running:
            self.running = True
            if self.animate:
                self._timer = self.canvas.after(STATIC_INTERVAL_MS, self._tick)

    def _tick(self):
        self._timer = None
        if not self.running:
            return
        self.ticks += 1
        self._index = (self._index + 1) % len(self._photos)
        try:
            self.canvas.itemconfigure(self._item, image=self._photos[self._index])
        except Exception:                 # the canvas was cleared by someone else: stop quietly
            self.running = False
            return
        self._timer = self.canvas.after(STATIC_INTERVAL_MS, self._tick)

    def stop(self):
        self.running = False
        if self._timer is not None:
            try:
                self.canvas.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None

    def _resized(self, _event=None):
        if self.running:
            self.canvas.after_idle(self._redraw_if_running)

    def _redraw_if_running(self):
        if self.running and (self.canvas.winfo_width(), self.canvas.winfo_height()) != self._size:
            self.start()


# ===========================================================================
# Widget colours
# ===========================================================================

def apply_style(root):
    """Dress the standard controls as a television's control panel: charcoal
    plastic, cream lettering. Returns the colours the app needs to know."""
    from tkinter import ttk
    p = PALETTE
    style = ttk.Style(root)
    style.theme_use("clam")
    panel, raised, line, well = p["panel"], p["panel_raised"], p["panel_line"], p["well"]
    text, muted, disabled = p["text"], p["muted"], p["disabled"]
    style.configure(".", background=panel, foreground=text, fieldbackground=well, bordercolor=line,
                    darkcolor=panel, lightcolor=panel, troughcolor=well, focuscolor=p["amber"],
                    selectbackground=p["amber"], selectforeground="#1a1408", insertcolor=text)
    style.configure("TFrame", background=panel)
    style.configure("TLabel", background=panel, foreground=text)
    style.configure("TLabelframe", background=panel, bordercolor=line, darkcolor=line, lightcolor=line)
    style.configure("TLabelframe.Label", background=panel, foreground=p["amber"])
    style.configure("TButton", background=raised, foreground=text, bordercolor=line, darkcolor=raised,
                    lightcolor="#4a433c", padding=(8, 4))
    style.map("TButton", background=[("disabled", panel), ("pressed", well), ("active", "#4a433c")],
              foreground=[("disabled", disabled)], bordercolor=[("focus", p["amber"])])
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=panel, foreground=text, indicatorbackground=well,
                        indicatorforeground=p["amber"], upperbordercolor=line, lowerbordercolor=line)
        style.map(name, background=[("active", panel)], foreground=[("disabled", disabled)],
                  indicatorbackground=[("selected", p["amber"]), ("disabled", panel)],
                  indicatorforeground=[("selected", "#1a1408")])
    for name in ("TEntry", "TSpinbox", "TCombobox"):
        style.configure(name, fieldbackground=well, foreground=text, background=raised, arrowcolor=text,
                        bordercolor=line, darkcolor=well, lightcolor=well, insertcolor=text)
        style.map(name, fieldbackground=[("readonly", well), ("disabled", panel)],
                  foreground=[("disabled", disabled), ("readonly", text)],
                  selectbackground=[("readonly", well)], selectforeground=[("readonly", text)],
                  arrowcolor=[("disabled", disabled)], bordercolor=[("focus", p["amber"])])
    style.configure("TScale", background=raised, troughcolor=well, bordercolor=line,
                    darkcolor=raised, lightcolor="#4a433c")
    style.configure("Horizontal.TProgressbar", background=p["amber"], troughcolor=well, bordercolor=line,
                    darkcolor=p["amber"], lightcolor=p["amber"])
    for name in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(name, background=raised, troughcolor=well, bordercolor=line, arrowcolor=text,
                        darkcolor=raised, lightcolor=raised)
    # Drop-down lists and pop-up windows are classic Tk widgets: colour them through the option database.
    root.option_add("*TCombobox*Listbox.background", well)
    root.option_add("*TCombobox*Listbox.foreground", text)
    root.option_add("*TCombobox*Listbox.selectBackground", p["amber"])
    root.option_add("*TCombobox*Listbox.selectForeground", "#1a1408")
    root.option_add("*Toplevel.background", panel)
    root.configure(background=panel)
    return dict(panel=panel, muted=muted, text=text, bezel=p["bezel"], bezel_edge=p["bezel_edge"],
                amber=p["amber"], live=p["live"], well=well)
