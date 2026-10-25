"""Plan individual pen strokes relative to a document's upper-left work origin."""

from dataclasses import dataclass
import math

from text_to_gcode import Glyph, PlotError, Point, Settings, Stroke, number, validate_settings


@dataclass(frozen=True)
class Cursor:
    x: float = 0.
    row: int = 0
    previous: str | None = None
    main_end: Point | None = None


@dataclass(frozen=True)
class TypingPlan:
    source: str
    cursor: Cursor


class Typewriter:
    """Plan before transmission; commit the cursor after controller acknowledgement."""

    def __init__(self, font: dict[str, Glyph], settings: Settings):
        validate_settings(settings)
        if not font:
            raise PlotError("Die Schrift enthält keine Zeichen")
        self.font = font
        self.settings = settings
        self.cursor = Cursor()
        points = [(x-glyph.origin_x, y-glyph.baseline)
                  for glyph in font.values() for stroke in glyph.strokes for x, y in stroke]
        self.left = min(0., min(x for x, y in points))
        self.top = max(y for x, y in points)
        self.span = (self.top - min(y for x, y in points)) * settings.scale
        if self.span > settings.height:
            raise PlotError("Die Schriftgröße überschreitet die Dokumenthöhe")

    def _up(self) -> list[str]:
        return [f"M3S{number(self.settings.pen_up)}", f"G4P{number(self.settings.dwell)}"]

    def _move(self, point: Point, speed: float) -> str:
        return f"G1X{number(point[0])}Y{number(point[1])}F{number(speed)}"

    def plan(self, key: str) -> TypingPlan:
        s, cursor = self.settings, self.cursor
        y = round(-cursor.row * s.line_spacing * s.scale, 4)
        if key in ("enter", " "):
            if key == "enter":
                row, x = cursor.row + 1, 0.
                y = round(-row * s.line_spacing * s.scale, 4)
                if y - self.span < -s.height:
                    raise PlotError("Dokumentende erreicht: keine weitere Zeile möglich")
            else:
                row, x = cursor.row, cursor.x + s.word_spacing * s.scale
                if x > s.width:
                    raise PlotError("Rechter Dokumentrand erreicht; Enter beginnt eine neue Zeile")
            return TypingPlan("\n".join(self._up() + [self._move((round(x, 4), round(y, 4)), s.travel_speed)]),
                              Cursor(x=x, row=row))
        if key not in self.font:
            raise PlotError(f"Zeichen {key!r}: keine Vorlage vorhanden")
        glyph = self.font[key]
        next_x = cursor.x + (glyph.advance + s.character_spacing) * s.scale
        if next_x > s.width:
            raise PlotError("Rechter Dokumentrand erreicht; Enter beginnt eine neue Zeile")
        transformed: list[Stroke] = [tuple((round(cursor.x + (px-glyph.origin_x-self.left)*s.scale, 4),
                                          round(y + round((py-glyph.baseline-self.top)*s.scale, 4), 4))
                                         for px, py in stroke) for stroke in glyph.strokes]
        for stroke in transformed:
            if all(point == stroke[0] for point in stroke):
                raise PlotError("Schriftgröße zu klein: Zeichenstrich verschwindet")
            for x, py in stroke:
                if not (0 <= x <= s.width and -s.height <= py <= 0):
                    raise PlotError("Zeichen überschreitet den Dokumentrand; Größe oder Zeile ändern")
        main = glyph.main_strokes or tuple(range(len(transformed)))
        paths = [transformed[i] for i in main]
        previous = self.font.get(cursor.previous)
        if (key.isalpha() and previous is not None and previous.connect_out and glyph.connect_in
                and cursor.main_end is not None
                and math.dist(cursor.main_end, paths[0][0]) <= s.connection_distance * s.scale):
            if cursor.main_end != paths[0][0]:
                paths[0] = (cursor.main_end,) + paths[0]
        # Complete dots and crossbars immediately, before accepting the next key.
        paths.extend(transformed[i] for i in range(len(transformed)) if i not in main)
        lines = ["G21", "G90", "G94"] + self._up()
        for index, stroke in enumerate(paths):
            if index:
                lines.extend(self._up())
            lines.extend([self._move(stroke[0], s.travel_speed),
                          f"M3S{number(s.pen_down)}", f"G4P{number(s.dwell)}"])
            lines.extend(self._move(point, s.write_speed) for point in stroke[1:])
        lines.extend(self._up())
        return TypingPlan("\n".join(lines), Cursor(next_x, cursor.row,
                                                  key if key.isalpha() else None,
                                                  transformed[main[-1]][-1] if key.isalpha() else None))

    def commit(self, plan: TypingPlan) -> None:
        self.cursor = plan.cursor
