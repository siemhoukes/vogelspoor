"""Vogelspoor - koppel GPX-tracks van je horloge aan foto's via EXIF-tijden.

Kernlogica: GPX parsen, fototijden matchen op de track (met interpolatie),
en GPS-coordinaten wegschrijven met ExifTool.
"""
from __future__ import annotations

import bisect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Interpoleer niet over opname-gaten groter dan dit (bijv. horloge gepauzeerd)
MAX_INTERP_GAP_S = 15 * 60
# Foto's net buiten de track (of in een gat) plakken we aan het dichtstbijzijnde
# trackpunt, mits binnen deze marge
EDGE_SNAP_S = 10 * 60

BACKUP_DIRNAME = "vogelspoor-backup"

PHOTO_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".tif", ".tiff", ".png", ".heic", ".heif",
    ".arw", ".cr2", ".cr3", ".nef", ".nrw", ".orf", ".raf", ".rw2",
    ".dng", ".pef", ".srw", ".x3f",
}

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ---------------------------------------------------------------- exiftool

def exiftool_exe() -> str:
    if getattr(sys, "frozen", False):
        cand = Path(sys._MEIPASS) / "exiftool" / "exiftool.exe"
        if cand.exists():
            return str(cand)
    here = Path(__file__).resolve().parent
    for d in sorted((here / "vendor").glob("exiftool*")):
        cand = d / "exiftool.exe"
        if cand.exists():
            return str(cand)
    return "exiftool"  # hoop op PATH


def run_exiftool(args: list[str]) -> tuple[str, str, int]:
    """Draai exiftool met args via een UTF-8 argfile (veilig voor rare paden)."""
    fd, argfile = tempfile.mkstemp(suffix=".args", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(args))
        proc = subprocess.run(
            [exiftool_exe(), "-@", argfile],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
        )
        out = proc.stdout.decode("utf-8", errors="replace")
        err = proc.stderr.decode("utf-8", errors="replace")
        return out, err, proc.returncode
    finally:
        try:
            os.unlink(argfile)
        except OSError:
            pass


# ---------------------------------------------------------------- GPX

def parse_gpx(path: str) -> dict:
    """Lees alle trackpunten (met tijd) uit een GPX-bestand."""
    tree = ET.parse(path)
    pts = []
    for el in tree.iter():
        if not el.tag.endswith("trkpt"):
            continue
        lat = el.get("lat")
        lon = el.get("lon")
        if lat is None or lon is None:
            continue
        t = ele = None
        for child in el:
            if child.tag.endswith("}time") or child.tag == "time":
                t = _parse_gpx_time(child.text)
            elif child.tag.endswith("}ele") or child.tag == "ele":
                try:
                    ele = float(child.text)
                except (TypeError, ValueError):
                    pass
        if t is None:
            continue
        pts.append((t, float(lat), float(lon), ele))

    if not pts:
        raise ValueError("Geen trackpunten met tijd gevonden in dit GPX-bestand.")

    pts.sort(key=lambda p: p[0])
    # dubbele tijden eruit
    dedup = [pts[0]]
    for p in pts[1:]:
        if p[0] > dedup[-1][0]:
            dedup.append(p)

    return {
        "times": [p[0] for p in dedup],
        "points": dedup,
        "start": dedup[0][0],
        "end": dedup[-1][0],
    }


def _parse_gpx_time(text: str | None) -> float | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def downsample_latlngs(track: dict, max_points: int = 2000) -> list[list[float]]:
    pts = track["points"]
    step = max(1, len(pts) // max_points)
    lls = [[p[1], p[2]] for p in pts[::step]]
    if lls[-1] != [pts[-1][1], pts[-1][2]]:
        lls.append([pts[-1][1], pts[-1][2]])
    return lls


# ---------------------------------------------------------------- matching

def locate(track: dict, t: float) -> dict | None:
    """Vind (geinterpoleerde) positie op de track voor tijdstip t (epoch UTC)."""
    ts = track["times"]
    pts = track["points"]

    def snap(i: int, gap: float) -> dict:
        _, lat, lon, ele = pts[i]
        return {"lat": lat, "lon": lon, "ele": ele, "gap_s": gap, "method": "edge"}

    if t <= ts[0]:
        d = ts[0] - t
        return snap(0, d) if d <= EDGE_SNAP_S else None
    if t >= ts[-1]:
        d = t - ts[-1]
        return snap(len(ts) - 1, d) if d <= EDGE_SNAP_S else None

    i = bisect.bisect_right(ts, t)
    a, b = i - 1, i
    span = ts[b] - ts[a]
    if span > MAX_INTERP_GAP_S:
        da, db = t - ts[a], ts[b] - t
        d, j = (da, a) if da <= db else (db, b)
        return snap(j, d) if d <= EDGE_SNAP_S else None

    frac = (t - ts[a]) / span if span else 0.0
    _, lat_a, lon_a, ele_a = pts[a]
    _, lat_b, lon_b, ele_b = pts[b]
    ele = None
    if ele_a is not None and ele_b is not None:
        ele = ele_a + frac * (ele_b - ele_a)
    return {
        "lat": lat_a + frac * (lat_b - lat_a),
        "lon": lon_a + frac * (lon_b - lon_a),
        "ele": ele,
        "gap_s": 0.0,
        "method": "interp",
    }


def best_offset_suggestion(track: dict, photo_times: list[float],
                           current_offset_s: float) -> dict | None:
    """Vind de correctie die de meeste foto's op de track laat vallen.

    Cameraklokken staan een rond aantal uren of dagen verkeerd (zomertijd,
    verkeerde datum, andere tijdzone); kleine drift vangt de randmarge op.
    We proberen dus alleen hele uren (tot ±7 dagen) en kiezen die met de
    meeste matches. Bij gelijke stand wint een heel aantal dagen, daarna de
    kleinste correctie. Halve uren (zones als +x:30) alleen als geen enkel
    heel uur iets oplevert.
    """
    ts = [t for t in photo_times if t is not None]
    if not ts:
        return None

    def count(off: float) -> int:
        return sum(1 for t in ts if locate(track, t + off) is not None)

    def better(n: int, off: float, cur_n: int, cur_off: float) -> bool:
        if n != cur_n:
            return n > cur_n
        day_new, day_cur = off % 86400 == 0, cur_off % 86400 == 0
        if day_new != day_cur:
            return day_new
        return abs(off) < abs(cur_off)

    max_hours = 7 * 24
    best_n, best_off = 0, 0.0
    for k in range(-max_hours, max_hours + 1):
        off = k * 3600.0
        n = count(off)
        if better(n, off, best_n, best_off):
            best_n, best_off = n, off

    if best_n == 0:
        # niets op hele uren: probeer halve uren (bijv. tijdzone +x:30)
        for k in range(-max_hours * 2 + 1, max_hours * 2, 2):
            off = k * 1800.0
            n = count(off)
            if better(n, off, best_n, best_off):
                best_n, best_off = n, off

    if best_n > count(current_offset_s):
        return {"offset_s": best_off, "matched": best_n}
    return None


# ---------------------------------------------------------------- foto's

def scan_folder(folder: str, max_files: int = 5000) -> list[str]:
    """Zoek foto's in een map, inclusief submappen (camera's maken DCIM\\100MSDCF
    e.d.); backup-mappen van onszelf slaan we over."""
    out = []
    for p in sorted(Path(folder).rglob("*")):
        if len(out) >= max_files:
            break
        if not p.is_file() or p.suffix.lower() not in PHOTO_EXTS:
            continue
        if BACKUP_DIRNAME in p.parts:
            continue
        out.append(str(p))
    return out


def read_photos(paths: list[str]) -> list[dict]:
    """Lees tijd, bestaande GPS en thumbnail van elke foto via ExifTool."""
    if not paths:
        return []
    args = [
        "-charset", "UTF8", "-charset", "filename=UTF8",
        "-j", "-b", "-n", "-fast", "-q",
        "-DateTimeOriginal", "-OffsetTimeOriginal", "-CreateDate",
        "-GPSLatitude", "-GPSLongitude", "-Model",
    ]
    if len(paths) <= 300:  # thumbnails worden te zwaar bij enorme batches
        args.append("-ThumbnailImage")
    args += paths
    out, err, _ = run_exiftool(args)
    by_file = {}
    if out.strip():
        try:
            for rec in json.loads(out):
                by_file[os.path.normcase(rec.get("SourceFile", ""))] = rec
        except json.JSONDecodeError:
            pass

    photos = []
    for p in paths:
        rec = by_file.get(os.path.normcase(p.replace("\\", "/"))) or \
              by_file.get(os.path.normcase(p)) or {}
        dto = rec.get("DateTimeOriginal") or rec.get("CreateDate")
        t_utc, time_err = _photo_time_utc(dto, rec.get("OffsetTimeOriginal"))
        thumb = rec.get("ThumbnailImage")
        if isinstance(thumb, str) and thumb.startswith("base64:"):
            thumb = "data:image/jpeg;base64," + thumb[7:]
        else:
            thumb = None
        photos.append({
            "path": p,
            "name": os.path.basename(p),
            "time_str": _fmt_local(t_utc) if t_utc else (dto or ""),
            "t_utc": t_utc,
            "time_error": time_err,
            "has_gps": rec.get("GPSLatitude") is not None
                       and rec.get("GPSLongitude") is not None,
            "model": rec.get("Model") or "",
            "thumb": thumb,
        })
    return photos


def _photo_time_utc(dto: str | None, offset_tag: str | None):
    """EXIF-tijd (lokale cameratijd) -> epoch UTC.

    Met OffsetTimeOriginal als die er is; anders nemen we aan dat de camera
    in de tijdzone van deze computer staat.
    """
    if not dto:
        return None, "geen opnametijd in EXIF"
    try:
        naive = datetime.strptime(str(dto)[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None, f"onleesbare tijd: {dto}"
    tz = _parse_offset(offset_tag)
    if tz is not None:
        aware = naive.replace(tzinfo=tz)
    else:
        aware = naive.astimezone()  # systeem-tijdzone
    return aware.timestamp(), None


def _parse_offset(s: str | None):
    if not s:
        return None
    s = s.strip()
    try:
        sign = -1 if s[0] == "-" else 1
        hh, mm = s.lstrip("+-").split(":")
        return timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))
    except (ValueError, IndexError):
        return None


def _fmt_local(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%d-%m-%Y %H:%M:%S")


# ---------------------------------------------------------------- tijd verschuiven

def shift_times(paths: list[str], shift_s: int, make_backups: bool = True) -> dict:
    """Verschuif alle EXIF-datums (AllDates) met shift_s seconden."""
    shift_s = int(shift_s)
    if not paths or shift_s == 0:
        return {"updated": 0, "backup_dirs": [], "stderr": ""}

    backup_dirs = set()
    if make_backups:
        for p in paths:
            backup_dirs.add(backup_photo(p))

    days, rem = divmod(abs(shift_s), 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    op = "+=" if shift_s > 0 else "-="
    shift = f"0:0:{days} {h}:{m}:{s}"

    args: list[str] = []
    for p in paths:
        args += ["-charset", "filename=UTF8", "-P", "-overwrite_original",
                 f"-AllDates{op}{shift}", p, "-execute"]
    out, err, _ = run_exiftool(args)
    return {
        "updated": out.count("1 image files updated"),
        "backup_dirs": sorted(backup_dirs),
        "stderr": err,
    }


# ---------------------------------------------------------------- schrijven

def backup_photo(path: str) -> str:
    """Kopieer de foto naar een backup-map naast het origineel (eenmalig)."""
    src = Path(path)
    bdir = src.parent / BACKUP_DIRNAME
    bdir.mkdir(exist_ok=True)
    dst = bdir / src.name
    if not dst.exists():
        shutil.copy2(src, dst)
    return str(bdir)


def write_gps(items: list[dict], make_backups: bool = True) -> dict:
    """Schrijf GPS-tags. items: [{path, lat, lon, ele}].

    Originelen worden eerst gebackupt naar een 'vogelspoor-backup'-map.
    Na afloop verifieren we per bestand of de GPS er echt in staat.
    """
    if not items:
        return {"written": [], "failed": [], "backup_dirs": []}

    backup_dirs = set()
    if make_backups:
        for it in items:
            backup_dirs.add(backup_photo(it["path"]))

    args: list[str] = []
    for it in items:
        lat, lon = it["lat"], it["lon"]
        args += [
            "-charset", "filename=UTF8", "-n", "-P", "-overwrite_original",
            f"-GPSLatitude={abs(lat)}",
            f"-GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
            f"-GPSLongitude={abs(lon)}",
            f"-GPSLongitudeRef={'E' if lon >= 0 else 'W'}",
        ]
        if it.get("ele") is not None:
            args += [f"-GPSAltitude={abs(it['ele'])}",
                     f"-GPSAltitudeRef={'0' if it['ele'] >= 0 else '1'}"]
        args += [it["path"], "-execute"]

    out, err, _ = run_exiftool(args)

    ok_paths = verify_gps([it["path"] for it in items])
    written, failed = [], []
    for it in items:
        if os.path.normcase(it["path"]) in ok_paths:
            written.append(it["path"])
        else:
            failed.append({"path": it["path"], "error": _err_for(err, it["path"])})
    return {
        "written": written,
        "failed": failed,
        "backup_dirs": sorted(backup_dirs),
    }


def verify_gps(paths: list[str]) -> set[str]:
    """Set van (normcase) paden die nu GPS-coordinaten bevatten."""
    if not paths:
        return set()
    args = ["-charset", "UTF8", "-charset", "filename=UTF8",
            "-j", "-n", "-fast", "-q",
            "-GPSLatitude", "-GPSLongitude", *paths]
    out, _, _ = run_exiftool(args)
    ok = set()
    if out.strip():
        try:
            for rec in json.loads(out):
                if rec.get("GPSLatitude") is not None and rec.get("GPSLongitude") is not None:
                    ok.add(os.path.normcase(rec.get("SourceFile", "").replace("/", os.sep)))
        except json.JSONDecodeError:
            pass
    return ok


def _err_for(stderr: str, path: str) -> str:
    name = os.path.basename(path)
    for line in stderr.splitlines():
        if name in line:
            return line.strip()
    return "schrijven mislukt (zie backup-map voor het origineel)"
