#!/usr/bin/env python3
"""Compose UTF-8 text from linear pen-plotter GCODE glyphs (standard library only)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
import html
import json
import math
from pathlib import Path
import re
import tempfile
import unicodedata


Point = tuple[float, float]
Stroke = tuple[Point, ...]
FONT_ROOT = Path(__file__).resolve().parent / "handwritingfont"
DEFAULT_FONT = FONT_ROOT / "standard"


class PlotError(ValueError):
    """An invalid input or a plot that cannot fit its frame."""


@dataclass(frozen=True)
class Glyph:
    strokes: tuple[Stroke, ...]
    advance: float
    baseline: float = 0
    origin_x: float = 0
    main_strokes: tuple[int, ...] = ()
    connect_in: bool = False
    connect_out: bool = False


@dataclass(frozen=True)
class Settings:
    width: float = 210
    height: float = 297
    margin: float = 5
    x: float = 5
    y: float = 5
    scale: float = 1
    character_spacing: float = 1
    word_spacing: float = 12
    line_spacing: float = 45
    connection_distance: float = 5
    write_speed: float = 600
    travel_speed: float = 18000
    pen_up: float = 750
    pen_down: float = 10
    dwell: float = 0.15


@dataclass(frozen=True)
class DrawPath:
    points: Stroke
    label: str
    kind: str = "glyph"
    join_previous: bool = False


@dataclass(frozen=True)
class Job:
    paths: tuple[DrawPath, ...]
    settings: Settings


def finite_number(value: object, name: str, *, positive: bool = False,
                  nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlotError(f"{name}: Zahl erwartet")
    result = float(value)
    if not math.isfinite(result):
        raise PlotError(f"{name}: endliche Zahl erwartet")
    if positive and result <= 0:
        raise PlotError(f"{name}: muss größer als 0 sein")
    if nonnegative and result < 0:
        raise PlotError(f"{name}: darf nicht negativ sein")
    return result


def validate_settings(settings: Settings) -> None:
    for name in ("width", "height", "scale", "word_spacing", "line_spacing",
                 "write_speed", "travel_speed"):
        finite_number(getattr(settings, name), name, positive=True)
    for name in ("margin", "character_spacing", "connection_distance", "dwell",
                 "pen_up", "pen_down"):
        finite_number(getattr(settings, name), name, nonnegative=True)
    for name in ("x", "y"):
        finite_number(getattr(settings, name), name)
    if settings.pen_up == settings.pen_down:
        raise PlotError("Stiftwerte für oben und unten müssen verschieden sein")
    if 2 * settings.margin >= min(settings.width, settings.height):
        raise PlotError("Der Rand lässt keine nutzbare Zeichenfläche übrig")


def strip_comments(line: str) -> str:
    result = []
    depth = 0
    for char in line:
        if char == ";" and not depth:
            break
        if char == "(":
            depth += 1
        elif char == ")":
            if not depth:
                raise PlotError("Kommentarende ohne Kommentaranfang")
            depth -= 1
        elif not depth:
            result.append(char)
    if depth:
        raise PlotError("Nicht abgeschlossener Kommentar")
    return "".join(result).strip().upper()


WORD = re.compile(r"([A-Z])\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))")


def parse_gcode(source: str, *, pen_up: float = 750, pen_down: float = 10,
                filename: str = "Vorlage") -> tuple[Stroke, ...]:
    """Extract drawn strokes; never forward arbitrary source commands."""
    position: list[float | None] = [None, None]
    absolute = False
    millimeters = False
    down = False
    motion: int | None = None
    strokes: list[Stroke] = []
    current: list[Point] = []

    def finish() -> None:
        if len(current) >= 2 and any(p != current[0] for p in current[1:]):
            strokes.append(tuple(current))
        current.clear()

    for line_number, raw in enumerate(source.splitlines(), 1):
        try:
            line = strip_comments(raw)
            if not line:
                continue
            matches = list(WORD.finditer(line))
            if WORD.sub("", line).strip():
                raise PlotError("Nicht unterstützte GCODE-Syntax")
            words: dict[str, list[float]] = {}
            for match in matches:
                key, raw_value = match.groups()
                value = finite_number(float(raw_value), key)
                words.setdefault(key, []).append(value)
            unknown = set(words) - set("GMXYFSPN")
            if unknown:
                raise PlotError(f"Nicht unterstützte Wörter: {', '.join(sorted(unknown))}")
            for key, values in words.items():
                if key != "G" and len(values) != 1:
                    raise PlotError(f"{key} mehrfach in einer Zeile")
            codes = words.get("G", [])
            if any(g not in (0, 1, 4, 21, 90, 94) for g in codes):
                raise PlotError("Nur G0/G1, G4, G21, G90 und G94 werden unterstützt")
            if len(codes) != len(set(codes)) or sum(g in (0, 1, 4) for g in codes) > 1:
                raise PlotError("Widersprüchliche oder doppelte G-Befehle")
            if "N" in words and (words["N"][0] < 0 or not words["N"][0].is_integer()):
                raise PlotError("Ungültige Zeilennummer")
            if "F" in words:
                finite_number(words["F"][0], "F", positive=True)
            if 21 in codes:
                millimeters = True
            if 90 in codes:
                absolute = True
            explicit_motion = next((int(g) for g in codes if g in (0, 1)), None)
            if explicit_motion is not None:
                motion = explicit_motion
            if "M" in words or "S" in words:
                if words.get("M") != [3] or "S" not in words:
                    raise PlotError("Stiftsteuerung erfordert M3 mit eindeutigem S-Wert")
                if "X" in words or "Y" in words or explicit_motion is not None or 4 in codes:
                    raise PlotError("Stiftwechsel muss in einer eigenen Zeile stehen")
                servo = words["S"][0]
                if servo not in (pen_up, pen_down):
                    raise PlotError(f"Unbekannter Stiftwert S{servo:g}")
                new_down = servo == pen_down
                if down and not new_down:
                    finish()
                down = new_down
            if 4 in codes:
                if "P" not in words or any(k in words for k in "XYF"):
                    raise PlotError("Wartebefehl muss G4 P< Sekunden > sein")
                finite_number(words["P"][0], "P", nonnegative=True)
            elif "P" in words:
                raise PlotError("P ist nur mit G4 erlaubt")
            if "X" in words or "Y" in words:
                if 4 in codes or motion is None or not absolute or not millimeters:
                    raise PlotError("XY-Bewegung benötigt G21, G90 und G0/G1")
                previous = tuple(position)
                for index, key in enumerate(("X", "Y")):
                    if key in words:
                        position[index] = words[key][0]
                if any(v is None for v in position):
                    raise PlotError("Die erste Position muss X und Y enthalten")
                point = (float(position[0]), float(position[1]))
                if down:
                    if any(v is None for v in previous):
                        raise PlotError("Vor dem Zeichnen mit angehobenem Stift positionieren")
                    if not current:
                        current.append((float(previous[0]), float(previous[1])))
                    if point != current[-1]:
                        current.append(point)
        except PlotError as error:
            raise PlotError(f"{filename}:{line_number}: {error}") from error
    finish()
    if not strokes:
        raise PlotError(f"{filename}: keine Zeichenstriche gefunden")
    return tuple(strokes)


def load_font(directory: Path) -> dict[str, Glyph]:
    manifest_path = directory / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PlotError(f"{manifest_path}: {error}") from error
    if (not isinstance(manifest, dict) or type(manifest.get("version")) is not int
            or manifest.get("version") != 1):
        raise PlotError("Manifest muss version: 1 enthalten")
    entries = manifest.get("glyphs")
    if not isinstance(entries, dict) or not entries:
        raise PlotError("Manifest muss ein nicht leeres glyphs-Objekt enthalten")
    up = finite_number(manifest.get("pen_up", 750), "Manifest pen_up", nonnegative=True)
    down = finite_number(manifest.get("pen_down", 10), "Manifest pen_down", nonnegative=True)
    if up == down:
        raise PlotError("Manifest: Stiftwerte müssen verschieden sein")
    result = {}
    for char, entry in entries.items():
        try:
            if len(char) != 1 or char.isspace() or not isinstance(entry, dict):
                raise PlotError("Einzelnes sichtbares Zeichen mit Metadaten erwartet")
            relative = entry.get("file")
            if not isinstance(relative, str) or not relative:
                raise PlotError("file fehlt")
            path = (directory / relative).resolve()
            if not path.is_relative_to(directory.resolve()):
                raise PlotError("Vorlagendatei muss innerhalb des Vorlagenverzeichnisses liegen")
            strokes = parse_gcode(path.read_text(encoding="utf-8"), pen_up=up,
                                  pen_down=down, filename=str(path))
            indices = entry.get("main_strokes", list(range(len(strokes))))
            if (not isinstance(indices, list) or not indices
                    or any(type(i) is not int or not 0 <= i < len(strokes) for i in indices)
                    or len(set(indices)) != len(indices) or indices != sorted(indices)):
                raise PlotError("main_strokes muss aufsteigende eindeutige Strichindizes enthalten")
            for name in ("connect_in", "connect_out"):
                if type(entry.get(name, False)) is not bool:
                    raise PlotError(f"{name}: bool erwartet")
            result[char] = Glyph(
                strokes, finite_number(entry.get("advance"), "advance", positive=True),
                finite_number(entry.get("baseline", 0), "baseline"),
                finite_number(entry.get("origin_x", 0), "origin_x"), tuple(indices),
                entry.get("connect_in", False), entry.get("connect_out", False),
            )
        except (OSError, PlotError) as error:
            raise PlotError(f"Zeichen {char!r}: {error}") from error
    return result


def compose(text: str, font: dict[str, Glyph], settings: Settings) -> Job:
    validate_settings(settings)
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    # A conventional final newline terminates the file, not an additional empty line.
    if text.endswith("\n"):
        text = text[:-1]
    paths: list[DrawPath] = []
    deferred: list[DrawPath] = []
    previous: Glyph | None = None
    x = 0.0
    y = 0.0
    line = column = 1
    for char in text:
        if char in " \n":
            paths.extend(deferred)
            deferred.clear()
            previous = None
            if char == " ":
                x += settings.word_spacing * settings.scale
                column += 1
            else:
                x = 0
                y -= settings.line_spacing * settings.scale
                line += 1
                column = 1
            continue
        label = f"Zeile {line}, Spalte {column}, Zeichen {char!r}"
        if char not in font:
            raise PlotError(f"{label}: keine Vorlage vorhanden")
        glyph = font[char]
        if not char.isalpha():
            paths.extend(deferred)
            deferred.clear()
            previous = None
        transformed = [tuple((x + (px - glyph.origin_x) * settings.scale,
                              y + (py - glyph.baseline) * settings.scale)
                             for px, py in stroke) for stroke in glyph.strokes]
        main_indices = glyph.main_strokes or tuple(range(len(glyph.strokes)))
        main = [DrawPath(transformed[i], label) for i in main_indices]
        if (previous is not None and previous.connect_out and glyph.connect_in
                and char.isalpha()):
            end, start = paths[-1].points[-1], main[0].points[0]
            if math.dist(end, start) <= settings.connection_distance * settings.scale + 1e-9:
                if end != start:
                    paths.append(DrawPath((end, start), label, "connection", True))
                main[0] = DrawPath(main[0].points, label, "glyph", True)
        paths.extend(main)
        secondary = [DrawPath(stroke, label, "secondary")
                     for i, stroke in enumerate(transformed) if i not in main_indices]
        if char.isalpha():
            deferred.extend(secondary)
        else:
            paths.extend(secondary)
        previous = glyph if char.isalpha() else None
        x += (glyph.advance + settings.character_spacing) * settings.scale
        column += 1
    paths.extend(deferred)
    if not paths:
        raise PlotError("Der Text enthält keine zeichnbaren Zeichen")
    # Include logical line origins to preserve indentation and blank lines.
    min_x = min(0.0, min(p[0] for path in paths for p in path.points))
    min_y = min(y, min(p[1] for path in paths for p in path.points))
    shifted = tuple(DrawPath(tuple((round(px - min_x + settings.x, 4),
                                   round(py - min_y + settings.y, 4))
                                  for px, py in path.points), path.label, path.kind,
                             path.join_previous)
                    for path in paths)
    for path in shifted:
        if path.kind != "connection" and all(p == path.points[0] for p in path.points):
            raise PlotError(f"{path.label}: Strich verschwindet bei der gewählten Skalierung "
                            "(Koordinatenauflösung 0,0001 mm)")
    job = Job(shifted, settings)
    validate_frame(job)
    return job


def validate_frame(job: Job) -> None:
    s = job.settings
    xmin, ymin = s.margin, s.margin
    xmax, ymax = s.width - s.margin, s.height - s.margin
    # Every pen-up segment joins two checked endpoints in this convex rectangle.
    for path in job.paths:
        for x, y in path.points:
            if (not math.isfinite(x) or not math.isfinite(y)
                    or not xmin <= x <= xmax or not ymin <= y <= ymax):
                raise PlotError(
                    f"{path.label}: {path.kind}-Weg bei X{x:g} Y{y:g} außerhalb "
                    f"des Rahmens X[{xmin:g}, {xmax:g}] Y[{ymin:g}, {ymax:g}] mm; "
                    "Skalierung, Position oder Papierformat anpassen"
                )


def number(value: float) -> str:
    if value == 0:
        return "0"
    result = format(Decimal(str(value)), "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def render_gcode(job: Job) -> str:
    validate_settings(job.settings)
    validate_frame(job)
    s = job.settings
    lines = ["(Textplotter: absolute Millimeterkoordinaten)", "G21", "G90", "G94",
             f"M3 S{number(s.pen_up)}", f"G4 P{number(s.dwell)}"]
    end: Point | None = None
    down = False
    for path in job.paths:
        start = path.points[0]
        join = path.join_previous and end == start and down
        if path.join_previous and not join:
            raise PlotError(f"{path.label}: ungültiger Anschluss an vorherigen Strich")
        if not join:
            if down:
                lines.extend([f"M3 S{number(s.pen_up)}", f"G4 P{number(s.dwell)}"])
            lines.append(f"G1 X{number(start[0])} Y{number(start[1])} F{number(s.travel_speed)}")
            lines.extend([f"M3 S{number(s.pen_down)}", f"G4 P{number(s.dwell)}"])
        for x, y in path.points[1:]:
            lines.append(f"G1 X{number(x)} Y{number(y)} F{number(s.write_speed)}")
        end = path.points[-1]
        down = True
    lines.extend([f"M3 S{number(s.pen_up)}", f"G4 P{number(s.dwell)}"])
    return "\n".join(lines) + "\n"


def render_svg(job: Job) -> str:
    validate_frame(job)
    s = job.settings
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{s.width}mm" '
             f'height="{s.height}mm" viewBox="0 0 {s.width} {s.height}">',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<rect x="0" y="0" width="{s.width}" height="{s.height}" '
             'fill="none" stroke="#555" stroke-width="0.3"/>',
             f'<rect x="{s.margin}" y="{s.margin}" width="{s.width - 2*s.margin}" '
             f'height="{s.height - 2*s.margin}" fill="none" stroke="#999" '
             'stroke-width="0.2" stroke-dasharray="2 2"/>',
             f'<g transform="translate(0 {s.height}) scale(1 -1)" fill="none" '
             'stroke-width="0.4" stroke-linecap="round" stroke-linejoin="round">']
    for path in job.paths:
        color = "#ce4b20" if path.kind == "connection" else "#182c43"
        points = " ".join(f"{number(x)},{number(y)}" for x, y in path.points)
        lines.append(f'<polyline points="{points}" stroke="{color}">'
                     f'<title>{html.escape(path.label)}</title></polyline>')
    lines.extend(["</g>", "</svg>"])
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, content: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", type=Path, help="UTF-8-Textdatei")
    parser.add_argument("-o", "--output", required=True, type=Path, help="GCODE-Ausgabe")
    parser.add_argument("--font", type=Path, default=DEFAULT_FONT, help="Vorlagenverzeichnis")
    parser.add_argument("--paper", choices=("a4", "a5", "custom"), default="a4")
    parser.add_argument("--orientation", choices=("portrait", "landscape"), default="portrait")
    parser.add_argument("--width", type=float, help="Breite bei custom in mm")
    parser.add_argument("--height", type=float, help="Höhe bei custom in mm")
    for option, default, help_text in (
        ("margin", 5, "Innenrand in mm"), ("x", 5, "Linker Textrand in mm"),
        ("y", 5, "Unterer Textrand in mm"), ("scale", 1, "Gemeinsamer Skalierungsfaktor"),
        ("character-spacing", 1, "Zusätzlicher Zeichenabstand vor Skalierung in mm"),
        ("word-spacing", 12, "Leerzeichenvorschub vor Skalierung in mm"),
        ("line-spacing", 45, "Grundlinienabstand vor Skalierung in mm"),
        ("connection-distance", 5, "Maximale Verbindungslänge vor Skalierung in mm"),
        ("write-speed", 600, "Schreibgeschwindigkeit in mm/min"),
        ("travel-speed", 18000, "Fahrgeschwindigkeit mit Stift oben in mm/min"),
        ("pen-up", 750, "Ausgabe-S-Wert für Stift oben"),
        ("pen-down", 10, "Ausgabe-S-Wert für Stift unten"),
        ("dwell", 0.15, "Wartezeit nach Anheben und Absenken in Sekunden"),
    ):
        parser.add_argument(f"--{option}", type=float, default=default, help=help_text)
    preview = parser.add_mutually_exclusive_group()
    preview.add_argument("--preview", type=Path, help="SVG-Ausgabe; Standard: neben GCODE")
    preview.add_argument("--no-preview", action="store_true", help="Keine SVG-Ausgabe")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = argument_parser()
    args = parser.parse_args(argv)
    try:
        if args.paper == "custom":
            if args.width is None or args.height is None:
                raise PlotError("--paper custom benötigt --width und --height")
            width, height = args.width, args.height
        else:
            if args.width is not None or args.height is not None:
                raise PlotError("--width/--height sind nur mit --paper custom erlaubt")
            width, height = (210, 297) if args.paper == "a4" else (148, 210)
        if args.orientation == "landscape":
            width, height = height, width
        settings = Settings(width=width, height=height, **{
            field: getattr(args, field) for field in Settings.__dataclass_fields__
            if field not in ("width", "height")
        })
        validate_settings(settings)
        preview_path = None if args.no_preview else (args.preview or args.output.with_suffix(".svg"))
        outputs = [args.output] + ([preview_path] if preview_path else [])
        resolved = [path.resolve() for path in outputs]
        if len(set(resolved)) != len(resolved):
            raise PlotError("GCODE- und SVG-Ausgabe müssen unterschiedliche Dateien sein")
        for path in outputs:
            if path.resolve() == args.text.resolve() or path.resolve().is_relative_to(args.font.resolve()):
                raise PlotError("Ausgabe darf weder Textdatei noch Vorlagensammlung überschreiben")
            if not path.parent.is_dir() or path.is_dir():
                raise PlotError(f"Ungültiger Ausgabepfad: {path}")
        job = compose(args.text.read_text(encoding="utf-8-sig"), load_font(args.font), settings)
        gcode = render_gcode(job)
        svg = render_svg(job) if preview_path else None
        # All input, geometry and destinations have been checked before any write.
        if preview_path is not None:
            atomic_write(preview_path, svg)
        atomic_write(args.output, gcode)
        print(f"GCODE: {args.output} | Rahmen: {width:g} × {height:g} mm | "
              f"Schreiben: {settings.write_speed:g}, Fahren: {settings.travel_speed:g} mm/min")
        if preview_path:
            print(f"Vorschau: {preview_path}")
        return 0
    except (PlotError, OSError, UnicodeError) as error:
        parser.exit(2, f"Fehler: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
