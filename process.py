#!/usr/bin/env python3
"""
process.py — consolidated video overlay pipeline.

Replaces clean_all.sh + go.sh + together.sh with a single pass per video:
  1. Read GPS + date metadata directly from source (ffprobe / exiftool)
  2. Reverse-geocode + fetch satellite map tile (PIL)
  3. Generate a full-frame RGBA overlay PNG
  4. ffmpeg: rotate + add overlay + scale to TARGET_WxTARGET_H, all in one encode
  5. ffmpeg concat all outputs → final_output.mp4

Usage:
    python3 process.py [input_dir=.] [output_dir=overlayed] [--debug] [--gps LAT,LON]
                        [--scale FACTOR] [--text-position top-left|bottom-left]
                        [--no-overlay] [--output NAME]

    --gps LAT,LON        Manual fallback coordinates (decimal degrees, e.g.
                          49.1471944,-123.1036111) used for any file whose
                          source metadata has no embedded GPS.
    --scale FACTOR       Upscale the output canvas by this factor (e.g. 2)
                          before compositing the overlay. Useful for very
                          small sources (e.g. 160x112 camcorder clips) where
                          the overlay's minimum font/map sizes would
                          otherwise cover most of the frame.
    --text-position POS  Where the date/location/weather text is drawn:
                          "top-left" (default) or "bottom-left". The map
                          thumbnail always stays bottom-right.
    --no-overlay          Skip metadata lookup, weather, and overlay
                          generation entirely. Just sorts source videos by
                          capture date and stream-copies the video into
                          final_output.mp4 (audio re-encoded to stay in sync;
                          source files untouched).
                          Photos are skipped in this mode since stills can't
                          be concatenated into a video without converting
                          them.
    --output NAME        Filename for the concatenated result, written inside
                          output_dir (default: final_output.mp4).
"""

import os
import sys
import subprocess
import json
import math
import re
import datetime
import random
import tempfile
from pathlib import Path
from datetime import timedelta
from io import BytesIO
from time import sleep
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")

from PIL import Image, ImageDraw, ImageFont, ImageOps
import pillow_heif
import requests
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut

TARGET_W = 1080
TARGET_H = 1920
FONT_PATH = "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf"
# NotoSansMono has no Arabic/Tifinagh glyphs (renders as tofu boxes), and
# NotoSansArabic/NotoSansTifinagh have no Latin glyphs, so overlay text mixing
# scripts (e.g. Moroccan place names carry Latin + Arabic + Tifinagh/Berber
# all in one string) needs per-script font fallback.
SCRIPT_FONTS = [
    (range(0x0600, 0x0780), "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
    (range(0x08A0, 0x0900), "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
    (range(0xFB50, 0xFE00), "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
    (range(0xFE70, 0xFF00), "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
    (range(0x2D30, 0x2D80), "/usr/share/fonts/truetype/noto/NotoSansTifinagh-Regular.ttf"),
]
FONT_SIZE = 35
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".MOV", ".MP4", ".AVI"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif",
              ".JPG", ".JPEG", ".PNG", ".HEIC", ".HEIF"}
PHOTO_CLIP_SECONDS = 3

geolocator = Nominatim(user_agent="video-overlay/1.0")


# ─── METADATA ────────────────────────────────────────────────────────────────

def ffprobe(path, show_entries, select_streams=None, extra_args=None):
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
           "-show_entries", show_entries]
    if select_streams:
        cmd += ["-select_streams", select_streams]
    if extra_args:
        cmd += extra_args
    cmd.append(str(path))
    r = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(r.stdout) if r.stdout.strip() else {}


def get_format_tags(path):
    return ffprobe(path, "format_tags").get("format", {}).get("tags", {})


def get_stream_info(path):
    data = ffprobe(path, "stream_tags:stream_side_data:stream=width,height,codec_type,color_transfer", "v:0")
    streams = data.get("streams", [])
    return streams[0] if streams else {}


# HDR transfer characteristics that need tonemapping before an 8-bit SDR
# encode — otherwise libx264 gets fed the same nonlinear HLG/PQ sample
# values just truncated to 8 bits, which most players render as flat,
# washed-out, low-contrast video (they display it as if it were normal SDR
# gamma, since an 8-bit yuv420p stream isn't valid HDR delivery to begin
# with).
HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}

HDR_TONEMAP_FILTER = (
    "zscale=transfer=linear:npl=100,format=gbrpf32le,zscale=primaries=bt709,"
    "tonemap=hable:desat=0,zscale=transfer=bt709:matrix=bt709:range=tv,format=yuv420p"
)


def get_rotation(stream):
    """Normalize a video stream's rotation to one of 0/90/180/270 (degrees
    clockwise needed to display upright), from whichever mechanism the file
    actually uses. Newer iPhone/QuickTime exports increasingly signal
    rotation via a displaymatrix side-data transform rather than the legacy
    'rotate' stream tag — checking only the tag silently mistreats those
    files as unrotated, which both shows them sideways and (since the target
    canvas is computed from the wrong effective width/height) can distort
    the aspect ratio once ffmpeg's scale filter forces them into it.
    """
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            # displaymatrix rotation uses the opposite sign convention from
            # the legacy tag (verified empirically: displaymatrix -90 needs
            # the same transpose=1 fix as legacy rotate=90).
            return int(round(-sd["rotation"])) % 360
    return int(stream.get("tags", {}).get("rotate", 0)) % 360


def parse_iso6709(loc):
    if not loc or loc == "None":
        return None, None
    parts = re.findall(r"[+-][0-9.]+", loc.rstrip("/"))
    if len(parts) >= 2:
        return float(parts[0]), float(parts[1])
    return None, None


def gps_from_exiftool(path):
    r = subprocess.run(["exiftool", str(path)], capture_output=True, text=True)
    for line in r.stdout.split("\n"):
        if "GPS Coordinates" in line:
            m = re.search(
                r"(\d+)\s+deg\s+(\d+)'\s+([\d.]+)\"\s+([NS]),\s+"
                r"(\d+)\s+deg\s+(\d+)'\s+([\d.]+)\"\s+([EW])", line)
            if m:
                ld, lm, ls, lref, od, om, os_, oref = m.groups()
                lat = float(ld) + float(lm)/60 + float(ls)/3600
                lon = float(od) + float(om)/60 + float(os_)/3600
                if lref == "S": lat = -lat
                if oref == "W": lon = -lon
                return lat, lon
    return None, None


# Embedded date tags to search, in priority order — DateTimeOriginal is the
# capture time when present, but plenty of files (edited, re-exported, or
# missing DateTimeOriginal/CreateDate) still carry one of these instead.
DATE_TAGS = [
    "DateTimeOriginal", "CreateDate", "MediaCreateDate", "TrackCreateDate",
    "DateTimeDigitized", "SubSecCreateDate", "GPSDateTime",
    "ModifyDate", "MediaModifyDate", "XMP:CreateDate", "XMP:DateCreated",
    "IPTC:DateCreated",
]


# Last-resort fallback when a file has no usable embedded date anywhere:
# some apps stamp the capture time directly into the filename. Patterns are
# tried in order against the filename stem; (regex, strptime format) pairs.
FILENAME_DATE_FORMATS = [
    (r"\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}", "%Y-%m-%d-%H-%M-%S"),  # Signal/Telegram: PHOTO-2026-06-16-22-19-52
    (r"\d{8}_\d{6}", "%Y%m%d_%H%M%S"),                              # camera apps: 20260516_120417
    (r"^\d{8}", "%Y%m%d"),                                          # bare date prefix: 20260516...
    (r"\d{2}\.\d{2}\.\d{2}$", "%d.%m.%y"),                          # old camcorder exports: MOV00029_16.06.08
]


def date_from_filename(path):
    """Parse a capture date embedded directly in the filename. Treated as
    already being local (Pacific) time, since apps stamp filenames with the
    device's local clock. Returns (date, date_utc) or (None, None).
    """
    stem = Path(path).stem
    this_year = datetime.datetime.now().year
    for pattern, fmt in FILENAME_DATE_FORMATS:
        m = re.search(pattern, stem)
        if not m:
            continue
        try:
            dt = datetime.datetime.strptime(m.group(0), fmt)
        except ValueError:
            continue
        # Sanity check so an unrelated numeric filename (sequential photo
        # IDs, CDN hashes) can't masquerade as a plausible date.
        if not (2000 <= dt.year <= this_year + 1):
            continue
        date_utc = dt.replace(tzinfo=PACIFIC).astimezone(datetime.timezone.utc).replace(tzinfo=None)
        dt = (dt.replace(second=0, microsecond=0, minute=0)
              + timedelta(hours=dt.minute // 30))
        return dt, date_utc
    return None, None


def date_from_exiftool(path):
    """Search a broad set of embedded metadata date tags (in priority order)
    and return the first usable one as (date, date_utc), or (None, None) if
    the file truly has no embedded date anywhere.
    """
    r = subprocess.run(
        ["exiftool", "-j", "-OffsetTimeOriginal", "-OffsetTime",
         *(f"-{t}" for t in DATE_TAGS), str(path)],
        capture_output=True, text=True)
    data = json.loads(r.stdout)[0] if r.stdout.strip() else {}
    offset = (data.get("OffsetTimeOriginal") or data.get("OffsetTime") or "").strip()

    for tag in DATE_TAGS:
        raw = (data.get(tag) or "").strip().rstrip("Z")
        if not raw:
            continue
        try:
            dt = datetime.datetime.strptime(raw[:19], "%Y:%m:%d %H:%M:%S")
        except Exception:
            continue

        if tag == "GPSDateTime":
            # GPS timestamps are always UTC, no offset tag needed.
            date_utc = dt
            dt_local = dt.replace(tzinfo=datetime.timezone.utc).astimezone(PACIFIC).replace(tzinfo=None)
        elif offset:
            sign = 1 if offset[0] == "+" else -1
            oh, om = offset[1:].split(":")
            tz = datetime.timezone(sign * timedelta(hours=int(oh), minutes=int(om)))
            dt_tz = dt.replace(tzinfo=tz)
            date_utc = dt_tz.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            dt_local = dt_tz.astimezone(PACIFIC).replace(tzinfo=None)
        else:
            date_utc = dt
            dt_local = dt

        dt_local = (dt_local.replace(second=0, microsecond=0, minute=0)
                    + timedelta(hours=dt_local.minute // 30))
        return dt_local, date_utc

    return None, None


def extract_photo_metadata(path):
    """Returns (date, lat, lon, orig_location_tag, date_utc) for a photo via EXIF tags."""
    date, date_utc = date_from_exiftool(path)

    r = subprocess.run(
        ["exiftool", "-j", "-GPSLatitude#", "-GPSLongitude#", str(path)],
        capture_output=True, text=True)
    data = json.loads(r.stdout)[0] if r.stdout.strip() else {}
    lat = data.get("GPSLatitude")
    lon = data.get("GPSLongitude")

    loc_tag = f"{lat:+.4f}{lon:+.4f}/" if lat is not None and lon is not None else ""
    return date, lat, lon, loc_tag, date_utc


def _is_heif(path):
    """Detect real HEIF/HEIC files by magic bytes rather than trusting the
    extension — some files in this library carry a .HEIC extension but are
    actually plain JPEGs (iOS's "Compatible" export mode)."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return False
    return len(head) >= 8 and head[4:8] == b"ftyp"


def open_oriented_image(path):
    """Open a photo as an orientation-corrected RGB PIL image.
    Real HEIC/HEIF files are decoded via pillow_heif, whose bundled libheif
    (newer than this system's) doesn't choke on the dangling depth-image
    item references common in iPhone Portrait/HDR-gain-map photos — the
    system ImageMagick/libheif1 fails on roughly half the HEICs in this
    library with "Non-existing item ID referenced".
    """
    if _is_heif(path):
        img = pillow_heif.open_heif(path, convert_hdr_to_8bit=True).to_pillow()
    else:
        img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def extract_metadata(path):
    """Returns (date, lat, lon, rotation_degrees, orig_location_tag, date_utc, width, height, is_hdr)."""
    tags = get_format_tags(path)
    stream = get_stream_info(path)
    width = stream.get("width")
    height = stream.get("height")
    is_hdr = stream.get("color_transfer") in HDR_TRANSFERS

    # Date — prefer Apple quicktime tag, fall back to creation_time / date
    raw = (tags.get("com.apple.quicktime.creationdate")
           or tags.get("creation_time")
           or tags.get("date"))
    date = None
    date_utc = None
    if raw and raw != "None":
        try:
            dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            # com.apple.quicktime.creationdate already carries its UTC offset (e.g. -0700);
            # creation_time is stored as UTC. astimezone handles both correctly and
            # respects DST (PDT = -7, PST = -8) rather than blindly subtracting 8h.
            if dt.tzinfo is not None:
                # Keep a UTC copy for the weather lookup, which needs the true
                # instant in time rather than the Pacific display time below.
                date_utc = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                dt = dt.astimezone(PACIFIC).replace(tzinfo=None)
            else:
                date_utc = dt
            # Round to nearest hour so ~%-I doesn't truncate e.g. 9:55 → 9
            dt = (dt.replace(second=0, microsecond=0, minute=0)
                  + timedelta(hours=dt.minute // 30))
            date = dt
        except Exception:
            pass

    # ffprobe only sees container-level tags; if those are empty, try the
    # broader exiftool tag search (MediaCreateDate, TrackCreateDate, etc.)
    # before giving up.
    if date is None:
        date, date_utc = date_from_exiftool(path)

    # Location
    loc_tag = (tags.get("com.apple.quicktime.location.ISO6709")
               or tags.get("location"))
    lat, lon = parse_iso6709(loc_tag)
    if lat is None:
        lat, lon = gps_from_exiftool(path)

    rotation = get_rotation(stream)

    return date, lat, lon, rotation, loc_tag or "", date_utc, width, height, is_hdr


# ─── GEOCODING ───────────────────────────────────────────────────────────────

def _unq(seq):
    seen = set()
    return [x for x in seq if not (x in seen or seen.add(x))]


def split_address(address, max_length=25):
    if len(address) <= max_length:
        return address

    def _break(s):
        idx = s.find(", ", max_length)
        if idx != -1:
            return s[:idx], s[idx+2:].strip()
        idx = s.find(" ", max_length)
        if idx != -1:
            return s[:idx], s[idx+1:].strip()
        return s, ""

    line1, rest = _break(address)
    if not rest:
        return line1
    if len(rest) > max_length:
        line2, line3 = _break(rest)
        return f"{line1}\n{line2}\n{line3}" if line3 else f"{line1}\n{line2}"
    return f"{line1}\n{rest}"


def format_pretty_place(lat, lon):
    if lat is None or lon is None:
        return ""
    try:
        loc = geolocator.reverse(f"{lat},{lon}")
        addr = loc.raw["address"]
        print(addr)
        parts = _unq([
            addr.get("building", ""), addr.get("shop", ""),
            addr.get("amenity", ""), addr.get("commercial", ""),
            addr.get("road", "") if not (addr.get("amenity") or addr.get("building")) else "",
            addr.get("neighbourhood", ""),
            addr.get("suburb", "") if not addr.get("neighbourhood") else "",
            addr.get("village", ""), addr.get("town", ""), addr.get("borough", ""),
            addr.get("city", ""),
            addr.get("county", "") if not addr.get("city") else "",
            addr.get("state", ""),
            addr.get("country", ""),
        ])
        return split_address(", ".join(p for p in parts if p))
    except GeocoderTimedOut:
        sleep(5)
        return ""


def format_pretty_date(date):
    if date is None:
        return "Unknown Date"
    base = date.strftime("%b. %d, %Y (%a)")
    # Midnight is the sentinel used when a date came from a source with no
    # time component at all (filename-derived dates, e.g. DD.MM.YY or a bare
    # YYYYMMDD prefix) rather than a genuinely known midnight capture time,
    # so showing "~12AM" would be misleading — omit the time in that case.
    if date.hour == 0 and date.minute == 0:
        return base
    return base + date.strftime(" ~%-I%p")


# ─── WEATHER ─────────────────────────────────────────────────────────────────

WEATHER_CODES = {
    0: "Clear", 1: "Mostly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog",
    51: "Light Drizzle", 53: "Drizzle", 55: "Heavy Drizzle",
    56: "Freezing Drizzle", 57: "Freezing Drizzle",
    61: "Light Rain", 63: "Rain", 65: "Heavy Rain",
    66: "Freezing Rain", 67: "Freezing Rain",
    71: "Light Snow", 73: "Snow", 75: "Heavy Snow", 77: "Snow Grains",
    80: "Rain Showers", 81: "Rain Showers", 82: "Violent Showers",
    85: "Snow Showers", 86: "Snow Showers",
    95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm",
}


def fetch_weather(date_utc, lat, lon):
    """Look up historical weather (°F, condition) for a UTC timestamp + GPS coord
    via Open-Meteo's free archive API. Returns None if unavailable."""
    if date_utc is None or lat is None or lon is None:
        return None
    try:
        day = date_utc.strftime("%Y-%m-%d")
        r = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": lat, "longitude": lon,
                "start_date": day, "end_date": day,
                "hourly": "temperature_2m,weather_code",
                "timezone": "UTC",
            },
            timeout=10,
        )
        r.raise_for_status()
        hourly = r.json()["hourly"]
        times = [datetime.datetime.fromisoformat(t) for t in hourly["time"]]
        idx = min(range(len(times)), key=lambda i: abs(times[i] - date_utc))
        temp_c = round(hourly["temperature_2m"][idx])
        cond = WEATHER_CODES.get(hourly["weather_code"][idx], "")
        return temp_c, cond
    except Exception:
        return None


def format_pretty_weather(weather):
    if not weather:
        return ""
    temp_c, cond = weather
    temp_f = round(temp_c * 9 / 5 + 32)
    temps = f"{temp_c}°C / {temp_f}°F"
    return f"{temps}, {cond}" if cond else temps


# ─── MAP TILE ────────────────────────────────────────────────────────────────

def osm_tile_map_image(lat, lon, zoom=12, px=320, tile_size=256):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xt = (lon + 180.0) / 360.0 * n
    yt = (1.0 - math.log(math.tan(lat_rad) + 1/math.cos(lat_rad)) / math.pi) / 2.0 * n

    cx_px, cy_px = xt * tile_size, yt * tile_size
    half = px / 2
    left_px, top_px = cx_px - half, cy_px - half

    x0 = int(math.floor(left_px / tile_size))
    y0 = int(math.floor(top_px / tile_size))
    x1 = int(math.floor((left_px + px - 1) / tile_size))
    y1 = int(math.floor((top_px + px - 1) / tile_size))

    big = Image.new("RGBA", ((x1-x0+1)*tile_size, (y1-y0+1)*tile_size))
    sess = requests.Session()
    max_tile = int(n) - 1

    for ty in range(y0, y1+1):
        if ty < 0 or ty > max_tile:
            continue
        for tx in range(x0, x1+1):
            url = (f"https://server.arcgisonline.com/ArcGIS/rest/services/"
                   f"World_Imagery/MapServer/tile/{zoom}/{ty}/{tx % int(n)}")
            try:
                r = sess.get(url, headers={"User-Agent": "video-overlays/1.0"}, timeout=10)
                r.raise_for_status()
                tile = Image.open(BytesIO(r.content)).convert("RGBA")
                big.paste(tile, ((tx-x0)*tile_size, (ty-y0)*tile_size))
            except Exception:
                pass

    crop_l = int(round(left_px - x0*tile_size))
    crop_t = int(round(top_px - y0*tile_size))
    img = big.crop((crop_l, crop_t, crop_l+px, crop_t+px))

    # Location pin
    draw = ImageDraw.Draw(img)
    cx, cy, r = px//2, px//2, max(5, px//45)
    draw.ellipse((cx-r-2, cy-r-2, cx+r+2, cy+r+2), fill=(255, 255, 255, 220))
    draw.ellipse((cx-r, cy-r, cx+r, cy+r), fill=(0xEB, 0x96, 0x05, 255))
    return img


# ─── OVERLAY IMAGE ───────────────────────────────────────────────────────────

def _font_for_char(ch, default_font, script_fonts):
    cp = ord(ch)
    for rng, font in script_fonts:
        if cp in rng:
            return font
    return default_font


def _split_font_runs(line, default_font, script_fonts):
    """Split a line into consecutive (text, font) runs so each run can be
    drawn with a font that actually has glyphs for its script."""
    if not line:
        return []
    runs = []
    cur_font = _font_for_char(line[0], default_font, script_fonts)
    cur = line[0]
    for ch in line[1:]:
        font = _font_for_char(ch, default_font, script_fonts)
        if font is cur_font:
            cur += ch
        else:
            runs.append((cur, cur_font))
            cur, cur_font = ch, font
    runs.append((cur, cur_font))
    return runs


def draw_multiscript_text(draw, xy, text, default_font, script_fonts, fill, spacing=4):
    """Like draw.multiline_text, but picks a fallback font per character run
    so scripts missing from `default_font` (e.g. Arabic) don't render as tofu."""
    x0, y = xy
    for line in text.split("\n"):
        runs = _split_font_runs(line, default_font, script_fonts)
        fonts_in_line = [font for _, font in runs] or [default_font]
        # Match PIL's own multiline_text line-height formula (textbbox of "A"
        # rather than raw ascent/descent) so plain-Latin lines are unchanged.
        line_height = max(draw.textbbox((0, 0), "A", f)[3] for f in fonts_in_line) + spacing
        x = x0
        for run_text, font in runs:
            draw.text((x, y), run_text, font=font, fill=fill)
            x += draw.textlength(run_text, font=font)
        y += line_height


def make_overlay_png(date, lat, lon, weather, out_w, out_h, tmp_path, text_position="top-left"):
    """
    Build an out_w × out_h RGBA overlay:
      - date + location + weather text in the top-left or bottom-left
        (per `text_position`)
      - satellite map thumbnail in the bottom-right
    Text/map/margins are scaled by the photo's diagonal relative to the
    TARGET_W×TARGET_H reference design, so they read the same size relative
    to the photo regardless of resolution or aspect ratio (portrait,
    landscape, high-res, thumbnail — all scale the same way). Diagonal
    (rather than width or height alone) is used so the scale doesn't depend
    on orientation.
    Saves to tmp_path and returns it.
    """
    layout_scale = math.hypot(out_w, out_h) / math.hypot(TARGET_W, TARGET_H)
    font_size = max(12, round(FONT_SIZE * layout_scale))
    map_px = max(80, round(320 * layout_scale))
    margin = round(80 * layout_scale)
    shadow = max(1, round(3 * layout_scale))
    text_x = round(80 * layout_scale)
    text_y = round(100 * layout_scale)

    img = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, font_size)
    fallback_font_cache = {}
    script_fonts = []
    for rng, font_path in SCRIPT_FONTS:
        if font_path not in fallback_font_cache:
            fallback_font_cache[font_path] = ImageFont.truetype(font_path, font_size)
        script_fonts.append((rng, fallback_font_cache[font_path]))

    pretty_date = format_pretty_date(date)
    location_str = format_pretty_place(lat, lon)
    weather_str = format_pretty_weather(weather)
    text = pretty_date
    if location_str:
        text += "\n" + location_str
    if weather_str:
        text += "\n" + weather_str

    if text_position == "bottom-left":
        # multiline_text draws top-down from its anchor, so to hug the
        # bottom margin the anchor needs to sit `text height` above it —
        # measure the block first with the same spacing draw_multiscript_text
        # uses by default.
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=4)
        text_y = out_h - margin - (bbox[3] - bbox[1])

    # Text: drop shadow drawn as a small cluster of offsets (not just one) so
    # it reads as a thicker/bolder silhouette rather than a single thin
    # offset copy.
    for dx, dy in ((shadow, shadow), (shadow + 1, shadow), (shadow, shadow + 1)):
        draw_multiscript_text(draw, (text_x + dx, text_y + dy), text, font, script_fonts, "black")
    draw_multiscript_text(draw, (text_x, text_y), text, font, script_fonts, "#eb9605")

    # Map thumbnail: bottom-right
    if lat is not None and lon is not None:
        try:
            zoom = random.choice([18, 17, 16, 15, 14]) #, 4, 3, 2])
            map_img = osm_tile_map_image(lat, lon, zoom=zoom, px=map_px)
            map_x = out_w - map_px - margin
            map_y = out_h - map_px - margin
            plate = Image.new("RGBA", (map_px + 8, map_px + 8), (0, 0, 0, 140))
            img.paste(plate, (map_x - 4, map_y - 4), plate)
            img.paste(map_img, (map_x, map_y), map_img)
        except Exception as e:
            print(f"  Map fetch failed: {e}")

    img.save(tmp_path, "PNG")
    return tmp_path


# ─── VIDEO PROCESSING ────────────────────────────────────────────────────────

def compute_output_dims(width, height, rotation, scale=1.0):
    """
    Every source keeps its own aspect ratio — no letterboxing/padding to a
    fixed canvas. Just round to even (required by libx264) after accounting
    for the rotate tag.

    `scale` upscales the output canvas (and thus the source video, via the
    ffmpeg scale filter) before the overlay is composited. make_overlay_png's
    font/map sizing has floors (min 12px font, min 80px map) that dominate on
    very small sources (e.g. 160x112 camcorder clips), covering most of the
    frame; rendering onto a larger canvas keeps those floors a much smaller
    fraction of the frame.
    """
    if not width or not height:
        return TARGET_W, TARGET_H

    # Account for the rotate tag: e.g. a 1920x1080 stream tagged rotate=90
    # actually displays as 1080x1920 (portrait) once the player rotates it.
    if rotation in (90, 270):
        eff_w, eff_h = height, width
    else:
        eff_w, eff_h = width, height

    eff_w, eff_h = round(eff_w * scale), round(eff_h * scale)
    return eff_w - (eff_w % 2), eff_h - (eff_h % 2)


def process_video(src, output_dir, fails_path, debug=False, override_gps=None, scale=1.0, text_position="top-left"):
    src = Path(src)
    print(f"\n▶ {src.name}")

    try:
        date, lat, lon, rotation, loc_tag, date_utc, width, height, is_hdr = extract_metadata(src)
    except Exception as e:
        print(f"  ✗ metadata failed: {e}")
        with open(fails_path, "a") as f:
            f.write(f"{src}\n")
        return None

    if date is None:
        date, date_utc = date_from_filename(src)

    if (lat is None or lon is None) and override_gps:
        lat, lon = override_gps
        loc_tag = f"{lat:+.4f}{lon:+.4f}/"

    ts = date.strftime("%Y%m%d-%H%M%S") if date else "19700101-000000"
    suffix = "_debug" if debug else ""
    out = output_dir / f"overlay_{ts}_{src.stem}{suffix}.mp4"

    if out.exists():
        print(f"  ↩ already exists, skipping")
        return out

    out_w, out_h = compute_output_dims(width, height, rotation, scale)
    print(f"  date={date}  lat={lat}  lon={lon}  rotation={rotation}  size={width}x{height} → {out_w}x{out_h}")

    weather = fetch_weather(date_utc, lat, lon)
    print(f"  weather={weather}")

    # Generate overlay PNG
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        overlay_path = tmp.name
    try:
        make_overlay_png(date, lat, lon, weather, out_w, out_h, overlay_path, text_position)
    except Exception as e:
        print(f"  ✗ overlay generation failed: {e}")
        os.unlink(overlay_path)
        with open(fails_path, "a") as f:
            f.write(f"{src}\n")
        return None

    # Build ffmpeg filter_complex: (tonemap if HDR) → scale to target →
    # overlay PNG → [vout]. No manual transpose here — ffmpeg auto-applies
    # the source's rotation (legacy 'rotate' tag or displaymatrix) during
    # decode by default, before the filtergraph runs. `rotation` is only
    # used above to size the target canvas correctly; applying our own
    # transpose on top double-rotates.
    cur = "0:v"
    vf_steps = []
    if is_hdr:
        # Without this, libx264 gets fed the same nonlinear HLG/PQ sample
        # values just truncated to 8-bit — most players then render it as
        # flat, washed-out SDR since an 8-bit yuv420p stream isn't valid HDR
        # delivery. zscale+tonemap does a real HDR→SDR conversion.
        vf_steps.append(f"[{cur}]{HDR_TONEMAP_FILTER}[vsdr]")
        cur = "vsdr"
    vf_steps.append(f"[{cur}]scale={out_w}:{out_h}[vscaled]")
    vf_steps.append("[vscaled][1:v]overlay=0:0[vout]")
    filter_complex = ";".join(vf_steps)

    # Metadata to carry forward
    creation_time = date.isoformat() if date else ""
    location = loc_tag or ""

    cmd = [
        "ffmpeg", "-y",
        *(["-t", "1"] if debug else []),
        "-i", str(src),
        "-i", overlay_path,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "0:a:0",
        "-c:v", "libx264",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-colorspace", "bt709",
        "-c:a", "aac",
        "-r", "30",
        "-preset", "fast",
        "-map_metadata", "-1",
        "-metadata", f"creation_time={creation_time}",
        "-metadata", f"com.apple.quicktime.creationdate={creation_time}",
        "-metadata", f"location={location}",
        "-metadata", f"com.apple.quicktime.location.ISO6709={location}",
        str(out),
    ]

    try:
        subprocess.run(cmd, check=True)
        print(f"  ✔ → {out.name}")
    except subprocess.CalledProcessError as e:
        print(f"  ✗ ffmpeg failed: {e}")
        with open(fails_path, "a") as f:
            f.write(f"{src}\n")
        out = None
    finally:
        os.unlink(overlay_path)

    return out


def process_photo(src, output_dir, fails_path, debug=False, override_gps=None, scale=1.0, text_position="top-left"):
    src = Path(src)
    print(f"\n▶ {src.name}")

    try:
        img = open_oriented_image(src)
    except Exception as e:
        print(f"  ✗ open failed: {e}")
        with open(fails_path, "a") as f:
            f.write(f"{src}\n")
        return None

    try:
        date, lat, lon, loc_tag, date_utc = extract_photo_metadata(src)
    except Exception as e:
        print(f"  ✗ metadata failed: {e}")
        with open(fails_path, "a") as f:
            f.write(f"{src}\n")
        return None

    if date is None:
        date, date_utc = date_from_filename(src)

    if (lat is None or lon is None) and override_gps:
        lat, lon = override_gps
        loc_tag = f"{lat:+.4f}{lon:+.4f}/"

    ts = date.strftime("%Y%m%d-%H%M%S") if date else "19700101-000000"
    suffix = "_debug" if debug else ""
    still_out = output_dir / f"overlay_{ts}_{src.stem}{suffix}.jpg"
    clip_out = output_dir / f"overlay_{ts}_{src.stem}{suffix}.mp4"

    if clip_out.exists():
        print(f"  ↩ already exists, skipping")
        return clip_out

    out_w, out_h = compute_output_dims(*img.size, 0, scale)
    print(f"  date={date}  lat={lat}  lon={lon}  size={img.size[0]}x{img.size[1]} → {out_w}x{out_h}")

    weather = fetch_weather(date_utc, lat, lon)
    print(f"  weather={weather}")

    # Generate overlay PNG at the photo's own aspect ratio, then composite it
    # directly on top — no letterboxing/padding, the photo keeps its own shape.
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        overlay_path = tmp.name
    try:
        make_overlay_png(date, lat, lon, weather, out_w, out_h, overlay_path, text_position)
        canvas = img if img.size == (out_w, out_h) else img.resize((out_w, out_h), Image.LANCZOS)
        with Image.open(overlay_path) as overlay:
            overlay = overlay.convert("RGBA")
            canvas.paste(overlay, (0, 0), overlay)
        canvas.save(still_out, "JPEG", quality=92)
    except Exception as e:
        print(f"  ✗ overlay generation failed: {e}")
        os.unlink(overlay_path)
        with open(fails_path, "a") as f:
            f.write(f"{src}\n")
        return None
    os.unlink(overlay_path)

    # Also render a short silent-audio clip from the still so it splices into
    # the video concat alongside real clips.
    creation_time = date.isoformat() if date else ""
    location = loc_tag or ""
    duration = 1 if debug else PHOTO_CLIP_SECONDS

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-t", str(duration), "-i", str(still_out),
        "-f", "lavfi", "-t", str(duration), "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-r", "30",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        "-preset", "fast",
        "-map_metadata", "-1",
        "-metadata", f"creation_time={creation_time}",
        "-metadata", f"com.apple.quicktime.creationdate={creation_time}",
        "-metadata", f"location={location}",
        "-metadata", f"com.apple.quicktime.location.ISO6709={location}",
        str(clip_out),
    ]

    try:
        subprocess.run(cmd, check=True)
        print(f"  ✔ → {still_out.name}, {clip_out.name}")
    except subprocess.CalledProcessError as e:
        print(f"  ✗ ffmpeg failed: {e}")
        with open(fails_path, "a") as f:
            f.write(f"{src}\n")
        return None

    return clip_out


def concat_videos(output_dir, final_path):
    clips = sorted(output_dir.glob("overlay_*.mp4"))
    if not clips:
        print("No overlay clips found to concatenate.")
        return

    list_file = output_dir / "concat_list.txt"
    with open(list_file, "w") as f:
        for c in clips:
            f.write(f"file '{c.resolve()}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(final_path),
    ]
    subprocess.run(cmd, check=True)
    print(f"\n✔ Final output: {final_path}")


def concat_raw(media, output_dir, final_path):
    """--no-overlay path: sort source videos by capture date and
    stream-copy the video straight into one file (no overlay, no video
    re-encode; audio is re-encoded to pad gaps, see below).
    Requires all inputs to share compatible codecs/parameters
    for concat demuxer + `-c copy` to work.
    """
    def sort_key(src):
        try:
            date, *_ = extract_metadata(src)
        except Exception:
            date = None
        if date is None:
            date, _ = date_from_filename(src)
        return (date or datetime.datetime.min, src.name)

    ordered = sorted(media, key=sort_key)

    list_file = output_dir / "concat_list.txt"
    with open(list_file, "w") as f:
        for src in ordered:
            f.write(f"file '{src.resolve()}'\n")

    # Video is stream-copied, but audio must be re-encoded: the Kodak's audio
    # track stops short of the video in every clip (by up to ~1s). The concat
    # demuxer still starts each clip's audio at the right timestamp, leaving a
    # gap, but a plain copy into MP4 drops those gaps so audio slides earlier
    # with every clip. aresample=async=1 fills each gap with silence.
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c:v", "copy",
        "-af", "aresample=async=1:first_pts=0",
        "-c:a", "aac", "-b:a", "128k",
        str(final_path),
    ]
    try:
        subprocess.run(cmd, check=True)
        print(f"\n✔ Final output (raw, no overlay): {final_path}")
    except subprocess.CalledProcessError as e:
        print(f"\n✗ raw concat failed: {e}\n"
              f"  (stream-copy concat requires all inputs to share the same "
              f"codec/resolution/params — mixed sources may need re-encoding)")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    debug = "--debug" in args
    args = [a for a in args if a != "--debug"]

    no_overlay = "--no-overlay" in args
    args = [a for a in args if a != "--no-overlay"]

    override_gps = None
    if "--gps" in args:
        idx = args.index("--gps")
        try:
            lat_str, lon_str = args[idx + 1].split(",")
            override_gps = (float(lat_str), float(lon_str))
        except (IndexError, ValueError):
            sys.exit("--gps requires LAT,LON (e.g. --gps 49.1471944,-123.1036111)")
        del args[idx:idx + 2]

    scale = 1.0
    if "--scale" in args:
        idx = args.index("--scale")
        try:
            scale = float(args[idx + 1])
        except (IndexError, ValueError):
            sys.exit("--scale requires a number (e.g. --scale 2)")
        del args[idx:idx + 2]

    text_position = "top-left"
    if "--text-position" in args:
        idx = args.index("--text-position")
        try:
            text_position = args[idx + 1]
        except IndexError:
            sys.exit("--text-position requires a value (top-left or bottom-left)")
        if text_position not in ("top-left", "bottom-left"):
            sys.exit("--text-position must be 'top-left' or 'bottom-left'")
        del args[idx:idx + 2]

    output_name = "final_output.mp4"
    if "--output" in args:
        idx = args.index("--output")
        try:
            output_name = args[idx + 1]
        except IndexError:
            sys.exit("--output requires a filename (e.g. --output upload.mp4)")
        del args[idx:idx + 2]

    input_dir = Path(args[0]) if len(args) > 0 else Path(".")
    output_dir = Path(args[1]) if len(args) > 1 else Path("overlayed")
    output_dir.mkdir(exist_ok=True)

    if debug:
        print("⚡ debug mode: trimming each clip to 1 second")

    fails_path = input_dir / "fails.txt"

    # Sanitize filenames (replaces detox)
    try:
        subprocess.run(["detox", ".", "-r", "-v"], cwd=input_dir, check=True)
    except FileNotFoundError:
        print("detox not found, skipping filename sanitization")

    media_exts = VIDEO_EXTS | PHOTO_EXTS
    media = sorted(p for p in input_dir.iterdir()
                   if p.suffix in media_exts and not p.name.startswith("."))

    if not media:
        print(f"No media files found in {input_dir}")
        return

    if no_overlay:
        videos = [p for p in media if p.suffix in VIDEO_EXTS]
        skipped_photos = [p for p in media if p.suffix in PHOTO_EXTS]
        if skipped_photos:
            print(f"⚠ --no-overlay: skipping {len(skipped_photos)} photo(s) "
                  f"(can't concat stills without converting them)")
        if not videos:
            print(f"No video files found in {input_dir}")
            return
        print(f"Found {len(videos)} video file(s) in {input_dir}")
        concat_raw(videos, output_dir, output_dir / output_name)
        return

    print(f"Found {len(media)} media file(s) in {input_dir}")

    processed = []
    for src in media:
        handler = process_video if src.suffix in VIDEO_EXTS else process_photo
        out = handler(src, output_dir, fails_path, debug=debug, override_gps=override_gps, scale=scale, text_position=text_position)
        if out:
            processed.append(out)

    if processed:
        concat_videos(output_dir, output_dir / output_name)
    else:
        print("No media processed successfully.")


if __name__ == "__main__":
    main()
