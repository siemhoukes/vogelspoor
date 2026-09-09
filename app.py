"""Vogelspoor - desktop-app: GPS uit je horloge-GPX, in je vogelfoto's."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import webview
from webview.dom import DOMEventHandler

import geotag


def ui_path() -> str:
    if getattr(sys, "frozen", False):
        return str(Path(sys._MEIPASS) / "ui" / "index.html")
    return str(Path(__file__).resolve().parent / "ui" / "index.html")


class Api:
    def __init__(self):
        self._reset()

    def _reset(self):
        self.track = None       # geotag.parse_gpx-resultaat
        self.track_path = None
        self.photos = []        # geotag.read_photos-resultaat

    # ------------------------------------------------ state naar de UI

    def state(self):
        track = None
        if self.track:
            track = {
                "path": self.track_path,
                "name": os.path.basename(self.track_path),
                "n_points": len(self.track["points"]),
                "start_local": _fmt(self.track["start"]),
                "end_local": _fmt(self.track["end"]),
                "latlngs": geotag.downsample_latlngs(self.track),
            }
        return {"track": track, "photos": self.photos}

    def reset(self):
        self._reset()
        return self.state()

    # ------------------------------------------------ bestanden kiezen

    def pick_gpx(self):
        sel = webview.windows[0].create_file_dialog(
            webview.FileDialog.OPEN,
            file_types=("GPX track (*.gpx)", "Alle bestanden (*.*)"),
        )
        if sel:
            return self.add_paths(list(sel))
        return self.state()

    def pick_photos(self):
        exts = ";".join(f"*{e}" for e in sorted(geotag.PHOTO_EXTS))
        sel = webview.windows[0].create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True,
            file_types=(f"Fotobestanden ({exts})", "Alle bestanden (*.*)"),
        )
        if sel:
            return self.add_paths(list(sel))
        return self.state()

    def pick_folder(self):
        sel = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER)
        if sel:
            return self.add_paths(list(sel))
        return self.state()

    def add_paths(self, paths):
        """Gedropte of gekozen paden: gpx -> track, map -> scannen, foto -> lijst."""
        errors = []
        photo_paths = []
        for p in paths:
            if not p:
                continue
            if os.path.isdir(p):
                found = geotag.scan_folder(p)
                if not found:
                    errors.append(f"Geen foto's gevonden in map: {p}")
                photo_paths += found
            elif p.lower().endswith(".gpx"):
                try:
                    self.track = geotag.parse_gpx(p)
                    self.track_path = p
                except Exception as e:
                    errors.append(f"{os.path.basename(p)}: {e}")
            elif Path(p).suffix.lower() in geotag.PHOTO_EXTS:
                photo_paths.append(p)
            else:
                errors.append(f"{os.path.basename(p)}: geen GPX of foto")

        known = {ph["path"] for ph in self.photos}
        new = [p for p in photo_paths if p not in known]
        if new:
            self.photos += geotag.read_photos(new)
            self.photos.sort(key=lambda ph: (ph["t_utc"] is None, ph["t_utc"] or 0))

        st = self.state()
        st["errors"] = errors
        return st

    # ------------------------------------------------ tijd verschuiven

    def shift_time(self, paths, shift_s: float, make_backups: bool = True):
        """Verschuif de EXIF-opnametijd van deze foto's in de bestanden zelf."""
        shift_s = int(shift_s)
        by_path = {ph["path"]: ph for ph in self.photos}
        valid = [p for p in paths if p in by_path]
        old = {p: by_path[p]["t_utc"] for p in valid}

        res = geotag.shift_times(valid, shift_s, make_backups)

        fresh = {ph["path"]: ph for ph in geotag.read_photos(valid)}
        failed = []
        for p in valid:
            nw = fresh.get(p)
            if nw:
                i = next(i for i, ph in enumerate(self.photos) if ph["path"] == p)
                self.photos[i] = nw
            ok = (nw and old[p] is not None and nw["t_utc"] is not None
                  and abs(nw["t_utc"] - (old[p] + shift_s)) < 2)
            if not ok:
                failed.append(os.path.basename(p))
        if res["updated"] == len(valid):
            failed = []  # exiftool meldt alles gelukt (bv. DST-randgeval)
        self.photos.sort(key=lambda ph: (ph["t_utc"] is None, ph["t_utc"] or 0))
        return {"state": self.state(), "shifted": len(valid) - len(failed),
                "failed": failed, "backup_dirs": res["backup_dirs"]}

    # ------------------------------------------------ matchen & schrijven

    def match(self, offset_s: float):
        """Match alle foto's op de track met de gegeven tijdcorrectie (sec)."""
        results = []
        if not self.track:
            return {"results": results, "suggestion": None}
        for ph in self.photos:
            if ph["t_utc"] is None:
                results.append({"path": ph["path"], "status": "no_time"})
                continue
            loc = geotag.locate(self.track, ph["t_utc"] + offset_s)
            if loc is None:
                results.append({"path": ph["path"], "status": "outside"})
            else:
                results.append({
                    "path": ph["path"],
                    "status": "edge" if loc["method"] == "edge" else "ok",
                    "lat": round(loc["lat"], 7),
                    "lon": round(loc["lon"], 7),
                    "ele": None if loc["ele"] is None else round(loc["ele"], 1),
                    "gap_s": round(loc["gap_s"]),
                })
        suggestion = geotag.best_offset_suggestion(
            self.track, [ph["t_utc"] for ph in self.photos], offset_s)
        return {"results": results, "suggestion": suggestion}

    def write(self, offset_s: float, skip_existing: bool = True,
              make_backups: bool = True, manual: dict | None = None):
        """Schrijf GPS naar gematchte + handmatig geprikte foto's.

        manual: {pad: [lat, lon]} — wint van de track-match en negeert
        skip_existing (handmatig prikken is een bewuste keuze).
        """
        manual = manual or {}
        matched = self.match(offset_s)["results"]
        by_path = {ph["path"]: ph for ph in self.photos}
        items, skipped_existing = [], 0
        for r in matched:
            if r["path"] in manual:
                continue
            if r["status"] not in ("ok", "edge"):
                continue
            if skip_existing and by_path[r["path"]]["has_gps"]:
                skipped_existing += 1
                continue
            items.append({"path": r["path"], "lat": r["lat"],
                          "lon": r["lon"], "ele": r["ele"]})
        for p, latlon in manual.items():
            if p in by_path:
                items.append({"path": p, "lat": float(latlon[0]),
                              "lon": float(latlon[1]), "ele": None})
        no_match = len(self.photos) - len(items) - skipped_existing

        res = geotag.write_gps(items, make_backups=make_backups)
        # has_gps bijwerken zodat de UI klopt
        written = set(os.path.normcase(p) for p in res["written"])
        for ph in self.photos:
            if os.path.normcase(ph["path"]) in written:
                ph["has_gps"] = True
        return {
            "written": len(res["written"]),
            "failed": [{"name": os.path.basename(f["path"]), "error": f["error"]}
                       for f in res["failed"]],
            "skipped_existing": skipped_existing,
            "no_match": no_match,
            "backup_dirs": res["backup_dirs"],
        }


def _fmt(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%d-%m-%Y %H:%M:%S")


def _bind_drop(window, api):
    """Vang gedropte bestanden aan de Python-kant op.

    pywebview geeft volledige bestandspaden alleen door aan drop-handlers die
    via de Python DOM-API geregistreerd zijn; een puur-JavaScript handler
    krijgt ze (op Windows) nooit te zien.
    """
    def on_drop(e):
        files = (e.get("dataTransfer") or {}).get("files") or []
        paths = [f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")]
        if not paths:
            return
        window.evaluate_js('vgsBusy("Bestanden lezen\\u2026")')
        try:
            st = api.add_paths(paths)
        except Exception as ex:
            st = api.state()
            st["errors"] = [f"Er ging iets mis bij het lezen: {ex}"]
        window.evaluate_js(f"vgsPushState({json.dumps(st)})")

    window.dom.document.events.drop += DOMEventHandler(
        on_drop, prevent_default=True, stop_propagation=False)


def main():
    api = Api()
    window = webview.create_window(
        "Vogelspoor", ui_path(), js_api=api,
        width=1180, height=780, min_size=(900, 600),
    )
    webview.start(_bind_drop, (window, api))


if __name__ == "__main__":
    main()
