#!/usr/bin/env python3
"""Four round world-time faces on the desktop.

A transparent row. Each face is a 12-hour clock about 2 cm across, with the
city name underneath. Drag the row to move it. Scroll one face to resize that
face. Drag an edge to resize all four together. Right-click a face for its
city, colour, and opacity.

Sits above the desktop icons and behind ordinary windows, same layer as the
other desklets. Open through XWayland so it can remember its place.
"""
from __future__ import annotations

import json
import math
import os
import signal
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

warnings.filterwarnings("ignore", category=DeprecationWarning)

sys.path[:0] = [
    str(Path(__file__).resolve().parent),
    str(Path.home() / ".local" / "lib"),
]
from desklet_geometry import (  # noqa: E402
    DeskletChrome,
    DeskletPlacement,
    prefer_xwayland,
    prepare_desktop_window,
)

prefer_xwayland()

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gio", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

HOME = Path.home()
CFG_DIR = HOME / ".config" / "lee-worldclock"
CFG = CFG_DIR / "clocks.json"
PID = HOME / ".cache" / "lee-worldclock.pid"
LOG = Path("/tmp/worldclock-widget.log")
ICON = HOME / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "lee-worldclock.png"
AUTOSTART = HOME / ".config" / "autostart" / "lee-worldclock.desktop"
APP_DESKTOP = HOME / ".local" / "share" / "applications" / "lee-worldclock.desktop"

APP_ID = "uk.lee.worldclock"
PRGNAME = "lee-worldclock"
SHADOW = 60

# Gap between the circles, in centimetres. The city name sits under its face.
GAP_CM = 0.50
MARGIN_PX = 10
LABEL_GAP_PX = 6
MIN_CM = 1.0
MAX_CM = 8.0
DEFAULT_CM = 2.0
DEFAULT_OPACITY = 0.86

COLOURS = (
    ("Ivory", "#F4F0E6"),
    ("Cream", "#FFF3C4"),
    ("Gold", "#E6C36A"),
    ("Amber", "#E07A4C"),
    ("Red", "#E15B5B"),
    ("Pink", "#F0A0C8"),
    ("Lilac", "#C9B6F2"),
    ("Sky", "#7EB6E6"),
    ("Mint", "#8ED9C4"),
    ("White", "#FFFFFF"),
    ("Black", "#1A1A1A"),
)

REGIONS = (
    (
        "Europe",
        (
            ("London", "Europe/London"),
            ("Dublin", "Europe/Dublin"),
            ("Lisbon", "Europe/Lisbon"),
            ("Paris", "Europe/Paris"),
            ("Madrid", "Europe/Madrid"),
            ("Rome", "Europe/Rome"),
            ("Berlin", "Europe/Berlin"),
            ("Amsterdam", "Europe/Amsterdam"),
            ("Stockholm", "Europe/Stockholm"),
            ("Athens", "Europe/Athens"),
            ("Istanbul", "Europe/Istanbul"),
            ("Moscow", "Europe/Moscow"),
        ),
    ),
    (
        "Americas",
        (
            ("New York", "America/New_York"),
            ("Toronto", "America/Toronto"),
            ("Chicago", "America/Chicago"),
            ("Denver", "America/Denver"),
            ("Los Angeles", "America/Los_Angeles"),
            ("Vancouver", "America/Vancouver"),
            ("Mexico City", "America/Mexico_City"),
            ("São Paulo", "America/Sao_Paulo"),
            ("Buenos Aires", "America/Argentina/Buenos_Aires"),
        ),
    ),
    (
        "Africa & Middle East",
        (
            ("Cairo", "Africa/Cairo"),
            ("Lagos", "Africa/Lagos"),
            ("Johannesburg", "Africa/Johannesburg"),
            ("Dubai", "Asia/Dubai"),
            ("Riyadh", "Asia/Riyadh"),
            ("Tel Aviv", "Asia/Jerusalem"),
        ),
    ),
    (
        "Asia & Pacific",
        (
            ("Mumbai", "Asia/Kolkata"),
            ("Bangkok", "Asia/Bangkok"),
            ("Singapore", "Asia/Singapore"),
            ("Hong Kong", "Asia/Hong_Kong"),
            ("Shanghai", "Asia/Shanghai"),
            ("Seoul", "Asia/Seoul"),
            ("Tokyo", "Asia/Tokyo"),
            ("Sydney", "Australia/Sydney"),
            ("Melbourne", "Australia/Melbourne"),
            ("Auckland", "Pacific/Auckland"),
        ),
    ),
)

DEFAULT_CLOCKS = (
    {"city": "London", "tz": "Europe/London", "colour": "#F4F0E6", "opacity": DEFAULT_OPACITY, "cm": DEFAULT_CM},
    {"city": "New York", "tz": "America/New_York", "colour": "#E6C36A", "opacity": DEFAULT_OPACITY, "cm": DEFAULT_CM},
    {"city": "Dubai", "tz": "Asia/Dubai", "colour": "#E07A4C", "opacity": DEFAULT_OPACITY, "cm": DEFAULT_CM},
    {"city": "Tokyo", "tz": "Asia/Tokyo", "colour": "#7EB6E6", "opacity": DEFAULT_OPACITY, "cm": DEFAULT_CM},
)

TRANS = Gdk.RGBA(red=0, green=0, blue=0, alpha=0)
CLEAR_STATES = (
    Gtk.StateFlags.NORMAL,
    Gtk.StateFlags.ACTIVE,
    Gtk.StateFlags.PRELIGHT,
    Gtk.StateFlags.SELECTED,
    Gtk.StateFlags.INSENSITIVE,
    Gtk.StateFlags.FOCUSED,
    Gtk.StateFlags.BACKDROP,
)

_ZONE_ROWS: list[tuple[str, str]] | None = None


def log(message: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(time.strftime("%H:%M:%S ") + message + "\n")
    except OSError:
        pass


def parse_colour(value: object) -> str:
    text = str(value or "").strip().upper()
    if len(text) == 7 and text.startswith("#") and all(c in "0123456789ABCDEF" for c in text[1:]):
        return text
    return "#F4F0E6"


def rgb_of(colour: str) -> tuple[float, float, float]:
    hex_colour = parse_colour(colour)
    return (
        int(hex_colour[1:3], 16) / 255.0,
        int(hex_colour[3:5], 16) / 255.0,
        int(hex_colour[5:7], 16) / 255.0,
    )


def luminance(colour: str) -> float:
    red, green, blue = rgb_of(colour)
    return 0.3 * red + 0.59 * green + 0.11 * blue


def hex_of(rgba: Gdk.RGBA) -> str:
    return "#{:02X}{:02X}{:02X}".format(
        int(round(rgba.red * 255)),
        int(round(rgba.green * 255)),
        int(round(rgba.blue * 255)),
    )


def gdk_rgba(colour: str, alpha: float = 1.0) -> Gdk.RGBA:
    red, green, blue = rgb_of(colour)
    return Gdk.RGBA(red=red, green=green, blue=blue, alpha=alpha)


def clamp_cm(value: object) -> float:
    try:
        cm = float(value)
    except (TypeError, ValueError):
        cm = DEFAULT_CM
    return max(MIN_CM, min(MAX_CM, cm))


def clamp_opacity(value: object) -> float:
    try:
        opacity = float(value)
    except (TypeError, ValueError):
        opacity = DEFAULT_OPACITY
    return max(0.08, min(1.0, opacity))


def zone_ok(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def fresh_clocks() -> list[dict]:
    return [dict(item) for item in DEFAULT_CLOCKS]


def normalise_clock(raw: object, fallback: dict) -> dict:
    source = raw if isinstance(raw, dict) else {}
    tz = str(source.get("tz") or fallback["tz"])
    if not zone_ok(tz):
        tz = fallback["tz"]
    city = str(source.get("city") or fallback["city"]).strip() or fallback["city"]
    return {
        "city": city[:32],
        "tz": tz,
        "colour": parse_colour(source.get("colour") or fallback["colour"]),
        "opacity": clamp_opacity(source.get("opacity", fallback["opacity"])),
        "cm": clamp_cm(source.get("cm", fallback["cm"])),
    }


def load_cfg() -> dict:
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    data: dict = {"x": None, "y": None, "seconds": True, "clocks": fresh_clocks()}
    if CFG.exists():
        try:
            loaded = json.loads(CFG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            loaded = None
        if isinstance(loaded, dict):
            clocks = loaded.get("clocks")
            if isinstance(clocks, list) and clocks:
                merged = []
                for index, fallback in enumerate(DEFAULT_CLOCKS):
                    raw = clocks[index] if index < len(clocks) else fallback
                    merged.append(normalise_clock(raw, fallback))
                # Keep exactly four faces. Extra saved faces are ignored.
                data["clocks"] = merged[:4]
            x, y = loaded.get("x"), loaded.get("y")
            if isinstance(x, int) and isinstance(y, int) and not (x == 0 and y == 0):
                data["x"] = x
                data["y"] = y
            data["seconds"] = bool(loaded.get("seconds", True))
    return data


def save_cfg(data: dict) -> None:
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "x": data.get("x"),
        "y": data.get("y"),
        "seconds": bool(data.get("seconds", True)),
        "clocks": data.get("clocks") or fresh_clocks(),
    }
    tmp = CFG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CFG)


def px_per_cm(window=None) -> float:
    """Logical pixels in one centimetre on the monitor under this window."""
    display = Gdk.Display.get_default()
    monitor = None
    if display is not None:
        gdk_window = window.get_window() if window is not None else None
        if gdk_window is not None:
            try:
                monitor = display.get_monitor_at_window(gdk_window)
            except Exception:
                monitor = None
        if monitor is None:
            monitor = display.get_primary_monitor() or (
                display.get_monitor(0) if display.get_n_monitors() else None
            )
    if monitor is None:
        return 96.0 / 2.54
    geo = monitor.get_geometry()
    millimetres = monitor.get_width_mm()
    if not millimetres or geo.width < 2:
        return 96.0 / 2.54
    return float(geo.width) / (float(millimetres) / 10.0)


def work_area() -> tuple[int, int, int, int]:
    display = Gdk.Display.get_default()
    if display is None or display.get_n_monitors() < 1:
        return 0, 0, 1920, 1080
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    if monitor is None:
        return 0, 0, 1920, 1080
    box = monitor.get_workarea()
    return int(box.x), int(box.y), int(box.width), int(box.height)


@dataclass
class Slot:
    index: int
    cx: float
    cy: float
    r: float
    x0: float
    x1: float
    label_cx: float
    label_top: float
    diameter: float


@dataclass
class Layout:
    width: int
    height: int
    slots: list[Slot]
    label_band: float


def layout_for(clocks: list[dict], px: float) -> Layout:
    diameters = [max(8.0, float(clock["cm"]) * px) for clock in clocks]
    max_d = max(diameters) if diameters else 8.0
    label_band = max(16.0, max_d * 0.20)
    gap = GAP_CM * px
    width_f = MARGIN_PX * 2 + sum(diameters) + gap * max(0, len(diameters) - 1)
    height_f = MARGIN_PX + max_d + LABEL_GAP_PX + label_band + MARGIN_PX
    slots: list[Slot] = []
    x = float(MARGIN_PX)
    baseline = MARGIN_PX + max_d
    for index, diameter in enumerate(diameters):
        cx = x + diameter / 2.0
        slots.append(
            Slot(
                index=index,
                cx=cx,
                cy=baseline - diameter / 2.0,
                r=diameter / 2.0,
                x0=x,
                x1=x + diameter,
                label_cx=cx,
                label_top=baseline + LABEL_GAP_PX,
                diameter=diameter,
            )
        )
        x += diameter + gap
    return Layout(
        width=max(1, int(math.ceil(width_f))),
        height=max(1, int(math.ceil(height_f))),
        slots=slots,
        label_band=label_band,
    )


def _font(size_px: float, weight: int) -> Pango.FontDescription:
    desc = Pango.FontDescription()
    desc.set_family("Cantarell")
    desc.set_absolute_size(max(6.0, size_px) * Pango.SCALE)
    desc.set_weight(weight)
    return desc


def _layout(cr: cairo.Context, text: str, size_px: float, weight: int) -> Pango.Layout:
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(_font(size_px, weight))
    layout.set_text(text, -1)
    return layout


def _show(
    cr: cairo.Context,
    layout: Pango.Layout,
    cx: float,
    cy: float,
    rgba: tuple[float, float, float, float],
    halo: bool,
) -> None:
    tw, th = layout.get_pixel_size()
    x = cx - tw / 2.0
    y = cy - th / 2.0
    if halo:
        # One-pixel shadow so the name reads on a light or dark wallpaper.
        cr.set_source_rgba(0, 0, 0, 0.78 * rgba[3])
        cr.move_to(x + 1, y + 1)
        PangoCairo.show_layout(cr, layout)
    cr.set_source_rgba(*rgba)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)


def _fit_label(cr: cairo.Context, text: str, max_width: float, start: float) -> Pango.Layout:
    size = start
    layout = _layout(cr, text, size, Pango.Weight.NORMAL)
    while size > 7.5:
        tw, _th = layout.get_pixel_size()
        if tw <= max_width:
            return layout
        size -= 0.5
        layout = _layout(cr, text, size, Pango.Weight.NORMAL)
    return layout


def paint_face(
    cr: cairo.Context,
    slot: Slot,
    clock: dict,
    moment: datetime,
    *,
    show_seconds: bool,
    numerals: bool = True,
) -> None:
    red, green, blue = rgb_of(clock["colour"])
    opacity = float(clock["opacity"])
    cx, cy, r = slot.cx, slot.cy, slot.r
    if r < 4:
        return
    dark_marks = luminance(clock["colour"]) > 0.55 and opacity >= 0.5
    if dark_marks:
        ink = (0.11, 0.11, 0.12)
        halo = False
    else:
        ink = (0.98, 0.97, 0.95)
        halo = True
    mark_alpha = 0.96

    cr.save()
    cr.arc(cx, cy, r, 0, math.tau)
    cr.clip()
    cr.set_source_rgba(red, green, blue, opacity)
    cr.paint()
    cr.restore()

    cr.set_source_rgba(*ink, 0.90 if dark_marks else 0.82)
    cr.set_line_width(max(1.15, r * 0.028))
    cr.arc(cx, cy, r - cr.get_line_width() / 2.0, 0, math.tau)
    cr.stroke()

    if r >= 28:
        cr.set_source_rgba(*ink, 0.38 if dark_marks else 0.48)
        cr.set_line_width(max(0.7, r * 0.012))
        cr.set_line_cap(cairo.LINE_CAP_BUTT)
        for step in range(60):
            if step % 5 == 0:
                continue
            ang = math.radians(step * 6 - 90)
            inner = r * 0.90
            outer = r * 0.955
            cr.move_to(cx + inner * math.cos(ang), cy + inner * math.sin(ang))
            cr.line_to(cx + outer * math.cos(ang), cy + outer * math.sin(ang))
            cr.stroke()

    if numerals and r >= 22:
        num_size = max(7.0, min(r * 0.30, 28.0))
        for hour in range(1, 13):
            ang = math.radians(hour * 30 - 90)
            radius = r * 0.74
            layout = _layout(cr, str(hour), num_size, Pango.Weight.BOLD)
            _show(
                cr,
                layout,
                cx + radius * math.cos(ang),
                cy + radius * math.sin(ang),
                (*ink, mark_alpha),
                halo,
            )

    second = moment.second + moment.microsecond / 1_000_000
    minute = moment.minute + second / 60.0
    hour = (moment.hour % 12) + minute / 60.0

    def arm(frac_of_circle: float, length: float, width: float, tail: float) -> None:
        ang = math.radians(frac_of_circle * 360.0 - 90.0)
        cr.save()
        cr.translate(cx, cy)
        cr.rotate(ang)
        cr.set_source_rgba(*ink, mark_alpha)
        cr.new_path()
        cr.move_to(-tail, -width * 0.28)
        cr.line_to(length * 0.62, -width * 0.50)
        cr.line_to(length, 0)
        cr.line_to(length * 0.62, width * 0.50)
        cr.line_to(-tail, width * 0.28)
        cr.close_path()
        cr.fill()
        cr.restore()

    arm(hour / 12.0, r * 0.50, max(2.4, r * 0.085), r * 0.12)
    arm(minute / 60.0, r * 0.70, max(1.8, r * 0.055), r * 0.14)
    if show_seconds and r >= 24:
        ang = math.radians(second / 60.0 * 360.0 - 90.0)
        cr.set_source_rgba(0.86, 0.22, 0.20, 0.92)
        cr.set_line_width(max(1.0, r * 0.018))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.move_to(cx + r * -0.16 * math.cos(ang), cy + r * -0.16 * math.sin(ang))
        cr.line_to(cx + r * 0.78 * math.cos(ang), cy + r * 0.78 * math.sin(ang))
        cr.stroke()

    cr.set_source_rgba(*ink, mark_alpha)
    cr.arc(cx, cy, max(1.8, r * 0.045), 0, math.tau)
    cr.fill()
    cr.set_source_rgba(red, green, blue, 1.0)
    cr.arc(cx, cy, max(1.0, r * 0.020), 0, math.tau)
    cr.fill()


def paint_row(
    cr: cairo.Context,
    clocks: list[dict],
    arranged: Layout,
    moments: list[datetime],
    *,
    show_seconds: bool,
    labels: bool = True,
) -> None:
    for slot, clock, moment in zip(arranged.slots, clocks, moments):
        paint_face(cr, slot, clock, moment, show_seconds=show_seconds)
        if not labels:
            continue
        start = max(9.0, min(slot.diameter * 0.15, 22.0))
        layout = _fit_label(cr, clock["city"], slot.diameter * 1.02, start)
        _tw, th = layout.get_pixel_size()
        red, green, blue = rgb_of(clock["colour"])
        # Keep the name light, tinted by the face, so it reads on the wallpaper.
        tint = (0.72 + 0.28 * red, 0.72 + 0.28 * green, 0.72 + 0.28 * blue)
        _show(cr, layout, slot.label_cx, slot.label_top + th / 2.0 + 1, (*tint, 0.96), True)


def moments_for(clocks: list[dict], now: datetime | None = None) -> list[datetime]:
    base = now or datetime.now().astimezone()
    found = []
    for clock in clocks:
        try:
            found.append(base.astimezone(ZoneInfo(clock["tz"])))
        except (ZoneInfoNotFoundError, ValueError):
            found.append(base)
    return found


def render_surface(clocks: list[dict], px: float, *, seconds: bool, now: datetime | None = None) -> cairo.ImageSurface:
    arranged = layout_for(clocks, px)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, arranged.width, arranged.height)
    cr = cairo.Context(surface)
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    paint_row(cr, clocks, arranged, moments_for(clocks, now), show_seconds=seconds)
    surface.flush()
    return surface


def composite_on(surface: cairo.ImageSurface, rgb: tuple[float, float, float]) -> cairo.ImageSurface:
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, surface.get_width(), surface.get_height())
    cr = cairo.Context(out)
    cr.set_source_rgb(*rgb)
    cr.paint()
    cr.set_source_surface(surface, 0, 0)
    cr.paint()
    return out


def self_test() -> int:
    px = 60.23529411764706
    clocks = fresh_clocks()
    arranged = layout_for(clocks, px)
    diameters = [slot.diameter for slot in arranged.slots]
    if any(abs(d - 2.0 * px) > 0.6 for d in diameters):
        print("diameter is not 2 cm", diameters)
        return 1
    if arranged.slots[0].label_top <= arranged.slots[0].cy + arranged.slots[0].r:
        print("label overlaps the face")
        return 1
    gaps = [
        arranged.slots[i + 1].x0 - arranged.slots[i].x1
        for i in range(3)
    ]
    if any(abs(gap - GAP_CM * px) > 0.6 for gap in gaps):
        print("gap is wrong", gaps)
        return 1
    # Bottom edges share one line, so a larger face still sits in the row.
    clocks[1]["cm"] = 3.0
    mixed = layout_for(clocks, px)
    bottoms = [round(slot.cy + slot.r, 2) for slot in mixed.slots]
    if len(set(bottoms)) != 1:
        print("faces are not bottom-aligned", bottoms)
        return 1
    clocks = fresh_clocks()
    sample = datetime(2026, 10, 20, 10, 10, 30, tzinfo=ZoneInfo("Europe/London"))
    surface = render_surface(clocks, px, seconds=True, now=sample)
    # Corners stay clear. The middle of each face is painted.
    stride = surface.get_stride()
    data = surface.get_data()

    def pixel(x: int, y: int) -> tuple[int, int, int, int]:
        # Cairo ARGB32 is native-endian premultiplied. On little-endian: B,G,R,A.
        offset = y * stride + x * 4
        blue, green, red, alpha = data[offset : offset + 4]
        return red, green, blue, alpha

    if pixel(0, 0)[3] != 0 or pixel(surface.get_width() - 1, 0)[3] != 0:
        print("background is not transparent", pixel(0, 0))
        return 1
    for slot in arranged.slots:
        _r, _g, _b, alpha = pixel(int(slot.cx), int(slot.cy))
        if alpha < 180:
            print("face centre is too transparent", slot.index, alpha)
            return 1
        # The 12 sits above the centre. It should differ from the flat fill.
        num_y = int(slot.cy - slot.r * 0.74)
        num_x = int(slot.cx)
        if pixel(num_x, num_y) == pixel(int(slot.cx), int(slot.cy)):
            print("numeral was not painted", slot.index)
            return 1
    out = Path("/tmp/worldclock-preview.png")
    surface.write_to_png(str(out))
    composite_on(surface, (0.16, 0.16, 0.18)).write_to_png("/tmp/worldclock-on-dark.png")
    composite_on(surface, (0.91, 0.88, 0.82)).write_to_png("/tmp/worldclock-on-light.png")
    print(f"ok {surface.get_width()}x{surface.get_height()} faces={len(arranged.slots)}")
    return 0


def list_zones() -> list[tuple[str, str]]:
    global _ZONE_ROWS
    if _ZONE_ROWS is not None:
        return _ZONE_ROWS
    rows = []
    for name in sorted(available_timezones()):
        if "/" not in name or name.startswith(("Etc/", "SystemV/", "posix/", "right/")):
            continue
        city = name.split("/")[-1].replace("_", " ")
        rows.append((city, name))
    _ZONE_ROWS = rows
    return rows


def desktop_file(exec_path: str) -> str:
    return "\n".join(
        (
            "[Desktop Entry]",
            "Version=1.0",
            "Type=Application",
            "Name=World Clocks",
            "Comment=Four round world-time faces on the desktop",
            f"Exec={exec_path}",
            f"Icon={ICON}",
            "Terminal=false",
            "Categories=Utility;Clock;",
            "StartupNotify=false",
            "StartupWMClass=lee-worldclock",
            "Keywords=clock;world;time;timezone;desktop;transparent;",
            "",
        )
    )


def install_launchers() -> None:
    body = desktop_file(str(Path(__file__).resolve()))
    for path in (APP_DESKTOP,):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != body:
            path.write_text(body, encoding="utf-8")
    if not AUTOSTART.exists():
        text = body + "X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=5\n"
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text(text, encoding="utf-8")


def autostart_enabled() -> bool:
    if not AUTOSTART.exists():
        return False
    hidden = False
    enabled = True
    for line in AUTOSTART.read_text(encoding="utf-8").splitlines():
        if line.startswith("Hidden="):
            hidden = line.split("=", 1)[1].strip().lower() == "true"
        elif line.startswith("X-GNOME-Autostart-enabled="):
            enabled = line.split("=", 1)[1].strip().lower() == "true"
    return enabled and not hidden


def set_autostart(enabled: bool) -> None:
    install_launchers()
    lines = AUTOSTART.read_text(encoding="utf-8").splitlines()
    found_enabled = False
    found_hidden = False
    rewritten = []
    for line in lines:
        if line.startswith("X-GNOME-Autostart-enabled="):
            rewritten.append("X-GNOME-Autostart-enabled=" + ("true" if enabled else "false"))
            found_enabled = True
        elif line.startswith("Hidden="):
            rewritten.append("Hidden=" + ("false" if enabled else "true"))
            found_hidden = True
        else:
            rewritten.append(line)
    if not found_enabled:
        rewritten.append("X-GNOME-Autostart-enabled=" + ("true" if enabled else "false"))
    if not found_hidden:
        rewritten.append("Hidden=" + ("false" if enabled else "true"))
    AUTOSTART.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def write_icon() -> None:
    if ICON.exists():
        return
    clocks = fresh_clocks()
    for clock in clocks:
        clock["cm"] = 1.15
    when = datetime(2026, 1, 1, 10, 10, 0, tzinfo=ZoneInfo("Europe/London"))
    surface = render_surface(clocks, 46.0, seconds=False, now=when)
    # Square icon, faces centred, transparent around them.
    side = 256
    icon = cairo.ImageSurface(cairo.FORMAT_ARGB32, side, side)
    cr = cairo.Context(icon)
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    scale = (side - 28) / max(surface.get_width(), surface.get_height())
    cr.translate(
        (side - surface.get_width() * scale) / 2.0,
        (side - surface.get_height() * scale) / 2.0,
    )
    cr.scale(scale, scale)
    cr.set_source_surface(surface, 0, 0)
    cr.paint()
    ICON.parent.mkdir(parents=True, exist_ok=True)
    icon.write_to_png(str(ICON))


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        text = cmdline.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return False
    return "lee_worldclock_widget.py" in text or PRGNAME in text


def claim_pid(replace: bool) -> None:
    PID.parent.mkdir(parents=True, exist_ok=True)
    if PID.exists():
        text = PID.read_text(encoding="utf-8").strip()
        if text.isdigit():
            old = int(text)
            if old != os.getpid() and pid_alive(old):
                if not replace:
                    log(f"already running as {old}")
                    print("World clocks are already on the desktop.", file=sys.stderr)
                    raise SystemExit(0)
                os.kill(old, signal.SIGTERM)
                for _ in range(20):
                    if not pid_alive(old):
                        break
                    time.sleep(0.05)
    PID.write_text(str(os.getpid()) + "\n", encoding="utf-8")


def release_pid() -> None:
    try:
        if PID.exists() and PID.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID.unlink()
    except OSError:
        pass


def paint_clear(widget: Gtk.Widget) -> None:
    context = widget.get_style_context()
    context.remove_class("background")
    for state in CLEAR_STATES:
        try:
            widget.override_background_color(state, TRANS)
        except Exception:
            pass


class WorldClockWindow(Gtk.Window):
    def __init__(self, cfg: dict):
        super().__init__(title="World Clocks")
        self.cfg = cfg
        self.px = px_per_cm(None)
        self._resize_key = None
        self._resize_base: list[float] | None = None
        self._scroll_at = 0.0
        self._screening = False
        self.set_name("worldclock")
        self.set_role("worldclock")
        self.set_app_paintable(True)
        self.set_resizable(True)
        self.connect("screen-changed", self._on_screen)
        self._on_screen()
        self.connect("draw", self._clear_window)
        prepare_desktop_window(self)
        paint_clear(self)

        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"""
            window, window.background, #worldclock, drawingarea {
              background-color: transparent;
              background-image: none;
              border: none;
              box-shadow: none;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.area = Gtk.DrawingArea()
        self.area.set_app_paintable(True)
        self.area.set_hexpand(True)
        self.area.set_vexpand(True)
        paint_clear(self.area)
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.area.connect("draw", self._on_draw)
        self.area.connect("button-press-event", self._on_button)
        self.area.connect("scroll-event", self._on_scroll)
        self.add(self.area)

        arranged = self.arranged()
        if not isinstance(self.cfg.get("x"), int) or not isinstance(self.cfg.get("y"), int):
            wx, wy, ww, wh = work_area()
            self.cfg["x"] = int(wx + max(16, ww - arranged.width - 16))
            self.cfg["y"] = int(wy + 12)
            save_cfg(self.cfg)
        self.set_default_size(arranged.width, arranged.height)
        self.area.set_size_request(arranged.width, arranged.height)

        self._place = DeskletPlacement(
            self,
            self._load_geo,
            self._save_geo,
            min_w=240,
            min_h=72,
            on_quit=self._quit,
        )
        self._place.watch(self.area)
        self._chrome = DeskletChrome(
            self,
            min_w=240,
            min_h=72,
            grip=14,
            placement=self._place,
            constrain=self._constrain_size,
            on_done=self._after_gesture,
        )
        self._chrome.watch(self.area)
        self.connect("realize", self._on_realize)
        self.connect("delete-event", self._on_delete)
        self._place.apply_before_show()
        GLib.timeout_add(100 if self.cfg.get("seconds", True) else 1000, self._tick)

    def arranged(self) -> Layout:
        return layout_for(self.cfg["clocks"], self.px)

    def _on_screen(self, *_args) -> None:
        if self._screening:
            return
        self._screening = True
        try:
            screen = self.get_screen()
            if screen is None:
                return
            visual = screen.get_rgba_visual()
            if visual is not None and self.get_visual() != visual:
                self.set_visual(visual)
            elif visual is None:
                log("no rgba visual; the background may be opaque")
        finally:
            self._screening = False

    def _clear_window(self, _widget, cr: cairo.Context) -> bool:
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        return False

    def _on_realize(self, *_args) -> bool:
        gdk = self.get_window()
        if gdk is not None:
            gdk.set_shadow_width(SHADOW, 0, SHADOW, 0)
        self.px = px_per_cm(self)
        self._apply_size()
        return False

    def _tick(self) -> bool:
        self.area.queue_draw()
        return True

    def _on_draw(self, _widget, cr: cairo.Context) -> bool:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        paint_row(
            cr,
            self.cfg["clocks"],
            self.arranged(),
            moments_for(self.cfg["clocks"]),
            show_seconds=bool(self.cfg.get("seconds", True)),
        )
        return True

    def _index_at(self, x: float, y: float) -> int | None:
        arranged = self.arranged()
        for slot in arranged.slots:
            if slot.x0 - 6 <= x <= slot.x1 + 6 and 0 <= y <= arranged.height:
                return slot.index
        best = None
        best_dist = 1e9
        for slot in arranged.slots:
            dist = abs(x - slot.cx)
            if dist < best_dist:
                best = slot.index
                best_dist = dist
        return best

    def _on_button(self, _widget, event) -> bool:
        if int(event.button) == 3 and event.type == Gdk.EventType.BUTTON_PRESS:
            index = self._index_at(float(event.x), float(event.y))
            if index is not None:
                self._menu(event, index)
            return True
        return False

    def _on_scroll(self, _widget, event) -> bool:
        now = time.monotonic()
        if now - self._scroll_at < 0.06:
            return True
        direction = 0
        if event.direction == Gdk.ScrollDirection.UP:
            direction = 1
        elif event.direction == Gdk.ScrollDirection.DOWN:
            direction = -1
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            if event.delta_y < -0.15:
                direction = 1
            elif event.delta_y > 0.15:
                direction = -1
        if direction == 0:
            return False
        index = self._index_at(float(event.x), float(event.y))
        if index is None:
            return False
        self._scroll_at = now
        self._set_cm(index, self.cfg["clocks"][index]["cm"] + 0.10 * direction)
        return True

    def _max_box(self) -> tuple[int, int]:
        _x, _y, w, h = work_area()
        return max(280, w - 24), max(100, h - 24)

    def _clamp_all(self, cms: list[float]) -> list[float]:
        values = [clamp_cm(cm) for cm in cms]
        max_w, max_h = self._max_box()
        for _ in range(12):
            trial = [dict(clock, cm=cm) for clock, cm in zip(self.cfg["clocks"], values)]
            arranged = layout_for(trial, self.px)
            if arranged.width <= max_w and arranged.height <= max_h:
                break
            values = [max(MIN_CM, cm * 0.94) for cm in values]
        return [round(cm, 3) for cm in values]

    def _set_cms(self, cms: list[float], *, save: bool, resize: bool = True) -> None:
        values = self._clamp_all(cms)
        for clock, cm in zip(self.cfg["clocks"], values):
            clock["cm"] = cm
        if resize:
            self._apply_size()
        else:
            self.area.queue_draw()
        if save:
            save_cfg(self.cfg)
            self._place.note_geometry_change()

    def _set_cm(self, index: int, cm: float) -> None:
        cms = [float(clock["cm"]) for clock in self.cfg["clocks"]]
        cms[index] = cm
        self._set_cms(cms, save=True)

    def _apply_size(self) -> None:
        arranged = self.arranged()
        self.area.set_size_request(arranged.width, arranged.height)
        self.resize(arranged.width, arranged.height)
        self.area.queue_draw()

    def _load_geo(self):
        arranged = self.arranged()
        x, y = self.cfg.get("x"), self.cfg.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            x = y = None
        return x, y, arranged.width, arranged.height

    def _save_geo(self, x: int, y: int, w: int, h: int) -> None:
        self.cfg["x"] = int(x)
        self.cfg["y"] = int(y)
        save_cfg(self.cfg)
        log(f"place {x},{y} {w}x{h}")

    def _constrain_size(self, x, y, w, h, edge, ox, oy, ow, oh):
        key = (int(ox), int(oy), int(ow), int(oh))
        if self._resize_key != key or self._resize_base is None:
            self._resize_key = key
            self._resize_base = [float(clock["cm"]) for clock in self.cfg["clocks"]]
        base = self._resize_base
        base_layout = layout_for(
            [dict(clock, cm=cm) for clock, cm in zip(self.cfg["clocks"], base)],
            self.px,
        )
        vertical = edge in (Gdk.WindowEdge.NORTH, Gdk.WindowEdge.SOUTH)
        if vertical and base_layout.height > 0:
            scale = float(h) / float(base_layout.height)
        elif base_layout.width > 0:
            scale = float(w) / float(base_layout.width)
        else:
            scale = 1.0
        scale = max(0.35, min(6.0, scale))
        self._set_cms([cm * scale for cm in base], save=False, resize=False)
        arranged = self.arranged()
        nx, ny = int(ox), int(oy)
        west = edge in (
            Gdk.WindowEdge.WEST,
            Gdk.WindowEdge.NORTH_WEST,
            Gdk.WindowEdge.SOUTH_WEST,
        )
        north = edge in (
            Gdk.WindowEdge.NORTH,
            Gdk.WindowEdge.NORTH_WEST,
            Gdk.WindowEdge.NORTH_EAST,
        )
        if west:
            nx = int(ox) + int(ow) - arranged.width
        if north:
            ny = int(oy) + int(oh) - arranged.height
        return nx, ny, arranged.width, arranged.height

    def _after_gesture(self) -> None:
        save_cfg(self.cfg)
        self._place.note_geometry_change()

    def _set_colour(self, index: int, colour: str) -> None:
        self.cfg["clocks"][index]["colour"] = parse_colour(colour)
        save_cfg(self.cfg)
        self.area.queue_draw()

    def _set_opacity(self, index: int, opacity: float) -> None:
        self.cfg["clocks"][index]["opacity"] = clamp_opacity(opacity)
        save_cfg(self.cfg)
        self.area.queue_draw()

    def _set_city(self, index: int, city: str, tz: str) -> None:
        if not zone_ok(tz):
            return
        self.cfg["clocks"][index]["city"] = city[:32]
        self.cfg["clocks"][index]["tz"] = tz
        save_cfg(self.cfg)
        self.area.queue_draw()

    def _choose_colour(self, index: int) -> None:
        clock = self.cfg["clocks"][index]
        previous = clock["colour"]
        dialog = Gtk.ColorChooserDialog(title=f"{clock['city']} face colour", parent=self)
        dialog.set_rgba(gdk_rgba(previous, 1.0))
        try:
            dialog.set_use_alpha(False)
        except Exception:
            pass

        def preview(chooser, *_args):
            clock["colour"] = hex_of(chooser.get_rgba())
            self.area.queue_draw()

        dialog.connect("notify::rgba", preview)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            clock["colour"] = hex_of(dialog.get_rgba())
            save_cfg(self.cfg)
        else:
            clock["colour"] = previous
        dialog.destroy()
        self.area.queue_draw()

    def _choose_opacity(self, index: int) -> None:
        clock = self.cfg["clocks"][index]
        previous = float(clock["opacity"])
        dialog = Gtk.Dialog(title=f"{clock['city']} face opacity", parent=self, flags=0)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Use this", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 8, 100, 1)
        scale.set_value(previous * 100.0)
        scale.set_digits(0)
        scale.set_hexpand(True)
        scale.set_size_request(280, -1)
        scale.set_draw_value(True)
        label = Gtk.Label(label="How solid the face is. Numbers stay readable.")
        label.set_xalign(0)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(8)
        box.pack_start(label, False, False, 0)
        box.pack_start(scale, True, True, 0)

        def preview(*_args):
            clock["opacity"] = clamp_opacity(scale.get_value() / 100.0)
            self.area.queue_draw()

        scale.connect("value-changed", preview)
        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            clock["opacity"] = clamp_opacity(scale.get_value() / 100.0)
            save_cfg(self.cfg)
        else:
            clock["opacity"] = previous
        dialog.destroy()
        self.area.queue_draw()

    def _choose_city(self, index: int) -> None:
        clock = self.cfg["clocks"][index]
        dialog = Gtk.Dialog(title=f"City for face {index + 1}", parent=self, flags=0)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Use this city", Gtk.ResponseType.OK)
        dialog.set_default_size(420, 460)
        entry = Gtk.SearchEntry()
        entry.set_placeholder_text("Type a city or region")
        store = Gtk.ListStore(str, str, str)
        for city, tz in list_zones():
            store.append([city, tz, f"{city}  —  {tz}"])
        filt = store.filter_new()

        def visible(model, iterator, _data):
            query = entry.get_text().strip().lower()
            if not query:
                return True
            city = model[iterator][0].lower()
            tz = model[iterator][1].lower()
            return query in city or query in tz

        filt.set_visible_func(visible)
        view = Gtk.TreeView(model=filt)
        view.set_headers_visible(False)
        column = Gtk.TreeViewColumn("City", Gtk.CellRendererText(), text=2)
        view.append_column(column)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(view)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.pack_start(entry, False, False, 0)
        box.pack_start(scroll, True, True, 0)

        def refilter(*_args):
            filt.refilter()

        entry.connect("search-changed", refilter)
        view.connect("row-activated", lambda *_: dialog.response(Gtk.ResponseType.OK))
        entry.connect("activate", lambda *_: dialog.response(Gtk.ResponseType.OK))
        dialog.show_all()
        for row in store:
            if row[1] != clock["tz"]:
                continue
            found, iterator = filt.convert_child_iter_to_iter(row.iter)
            if not found:
                break
            path = filt.get_path(iterator)
            view.set_cursor(path)
            view.scroll_to_cell(path, None, True, 0.4, 0)
            break
        response = dialog.run()
        chosen = None
        if response == Gtk.ResponseType.OK:
            selection = view.get_selection()
            model, iterator = selection.get_selected()
            if iterator is not None:
                chosen = (model[iterator][0], model[iterator][1])
            else:
                first = filt.get_iter_first()
                if first is not None:
                    chosen = (filt[first][0], filt[first][1])
        dialog.destroy()
        if chosen is not None:
            self._set_city(index, chosen[0], chosen[1])

    def _menu(self, event, index: int) -> None:
        clock = self.cfg["clocks"][index]
        menu = Gtk.Menu()

        def add(item: Gtk.MenuItem) -> None:
            menu.append(item)

        def command(label: str, fn) -> None:
            item = Gtk.MenuItem.new_with_label(label)
            item.connect("activate", lambda *_: fn())
            add(item)

        header = Gtk.MenuItem.new_with_label(f"{clock['city']}  ·  {clock['cm']:.1f} cm")
        header.set_sensitive(False)
        add(header)
        add(Gtk.SeparatorMenuItem())

        places = Gtk.MenuItem.new_with_label("Location")
        places_menu = Gtk.Menu()
        places.set_submenu(places_menu)
        for region, cities in REGIONS:
            region_item = Gtk.MenuItem.new_with_label(region)
            region_menu = Gtk.Menu()
            region_item.set_submenu(region_menu)
            for city, tz in cities:
                mark = "✓  " if tz == clock["tz"] else "    "
                choice = Gtk.MenuItem.new_with_label(mark + city)
                choice.connect(
                    "activate",
                    lambda *_a, c=city, z=tz: self._set_city(index, c, z),
                )
                region_menu.append(choice)
            places_menu.append(region_item)
        places_menu.append(Gtk.SeparatorMenuItem())
        other = Gtk.MenuItem.new_with_label("Other city…")
        other.connect("activate", lambda *_: self._choose_city(index))
        places_menu.append(other)
        add(places)

        colours = Gtk.MenuItem.new_with_label("Face colour")
        colour_menu = Gtk.Menu()
        colours.set_submenu(colour_menu)
        for name, colour in COLOURS:
            item = Gtk.MenuItem()
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            swatch = Gtk.Label(label="●")
            swatch.override_color(Gtk.StateFlags.NORMAL, gdk_rgba(colour))
            words = Gtk.Label(label=name, xalign=0)
            row.pack_start(swatch, False, False, 0)
            row.pack_start(words, True, True, 0)
            item.add(row)
            item.connect("activate", lambda *_a, c=colour: self._set_colour(index, c))
            colour_menu.append(item)
        colour_menu.append(Gtk.SeparatorMenuItem())
        custom = Gtk.MenuItem.new_with_label("Custom colour…")
        custom.connect("activate", lambda *_: self._choose_colour(index))
        colour_menu.append(custom)
        add(colours)

        opacity = Gtk.MenuItem.new_with_label("Face opacity")
        opacity_menu = Gtk.Menu()
        opacity.set_submenu(opacity_menu)
        for percent in (25, 50, 75, 100):
            check = Gtk.CheckMenuItem.new_with_label(f"{percent}%")
            check.set_active(abs(float(clock["opacity"]) - percent / 100.0) < 0.02)
            check.connect(
                "toggled",
                lambda item, p=percent: self._set_opacity(index, p / 100.0) if item.get_active() else None,
            )
            opacity_menu.append(check)
        opacity_menu.append(Gtk.SeparatorMenuItem())
        slider = Gtk.MenuItem.new_with_label("Opacity slider…")
        slider.connect("activate", lambda *_: self._choose_opacity(index))
        opacity_menu.append(slider)
        add(opacity)

        add(Gtk.SeparatorMenuItem())
        command("This face larger", lambda: self._set_cm(index, clock["cm"] + 0.25))
        command("This face smaller", lambda: self._set_cm(index, clock["cm"] - 0.25))
        command("This face 2 cm", lambda: self._set_cm(index, DEFAULT_CM))
        command("All faces 2 cm", lambda: self._set_cms([DEFAULT_CM] * 4, save=True))
        add(Gtk.SeparatorMenuItem())

        seconds = Gtk.CheckMenuItem.new_with_label("Second hands")
        seconds.set_active(bool(self.cfg.get("seconds", True)))

        def toggle_seconds(item):
            self.cfg["seconds"] = bool(item.get_active())
            save_cfg(self.cfg)
            self.area.queue_draw()

        seconds.connect("toggled", toggle_seconds)
        add(seconds)

        login = Gtk.CheckMenuItem.new_with_label("Open at login")
        login.set_active(autostart_enabled())
        login.connect("toggled", lambda item: set_autostart(item.get_active()))
        add(login)

        hint = Gtk.MenuItem.new_with_label("Scroll one face · drag an edge for all")
        hint.set_sensitive(False)
        add(hint)
        add(Gtk.SeparatorMenuItem())
        command("Quit", self._quit)
        menu.show_all()
        menu.popup_at_pointer(event)

    def _on_delete(self, *_args) -> bool:
        self._place._persist()
        save_cfg(self.cfg)
        release_pid()
        return False

    def _quit(self) -> None:
        try:
            self._place._persist()
        except Exception:
            pass
        save_cfg(self.cfg)
        release_pid()
        Gtk.main_quit()


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    GLib.set_prgname(PRGNAME)
    GLib.set_application_name("World Clocks")
    claim_pid("--replace" in sys.argv)
    try:
        write_icon()
        install_launchers()
        cfg = load_cfg()
        win = WorldClockWindow(cfg)
        win.show_all()
        log(f"started pid {os.getpid()} {win.arranged().width}x{win.arranged().height}")
        Gtk.main()
        save_cfg(win.cfg)
    finally:
        release_pid()
    return 0


if __name__ == "__main__":
    sys.exit(main())
