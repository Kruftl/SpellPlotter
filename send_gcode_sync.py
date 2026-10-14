#!/usr/bin/env python3
"""Send a pen-plotter job over USB, optionally buffering consecutive moves."""

from __future__ import annotations

import argparse
import codecs
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import sys
import time

from text_to_gcode import FONT_ROOT, PlotError, Settings, WORD, load_font, number, parse_gcode, strip_comments
from typewriter import Typewriter


class StreamError(ValueError):
    """The controller rejected a command, reset, or stopped responding."""


class ReturnHome(Exception):
    """Stop sending the job, drain acknowledged motion, then park the pen."""


@dataclass(frozen=True)
class Command:
    line: int
    text: str


class PrintTimer:
    """Report elapsed wall time once per second, without serial commands."""

    def __init__(self, report=print, label: str = "Druckdauer"):
        self.report = report
        self.label = label
        self.started = time.monotonic()
        self.last_second = -1
        self.finished = False
        self.tick()

    def elapsed(self) -> int:
        return max(0, int(time.monotonic() - self.started))

    @staticmethod
    def format(seconds: int) -> str:
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def tick(self) -> None:
        seconds = self.elapsed()
        if not self.finished and seconds != self.last_second:
            self.report(f"{self.label}: {self.format(seconds)}")
            self.last_second = seconds

    def finish(self) -> None:
        if not self.finished:
            self.report(f"{self.label} gesamt: {self.format(self.elapsed())}")
            self.finished = True


def timed_cancel(timer: PrintTimer, cancel=None):
    def poll():
        timer.tick()
        return cancel() if cancel is not None else False
    return poll


def prepare_program(source: str) -> tuple[Command, ...]:
    commands = []
    expanded = []
    pen_values = []
    first_motion = None
    first_pen = None
    for line_number, raw in enumerate(source.splitlines(), 1):
        text = "".join(strip_comments(raw).split())
        if not text:
            continue
        if not text.isascii() or len(text) > 79:
            raise StreamError(f"Zeile {line_number}: nur ASCII, höchstens 79 Zeichen pro Befehl")
        # The CONTROL test combines exactly M3/servo and its following G4 pause.
        # Expand only for the conservative template parser; send the original block.
        combined = re.fullmatch(r"(M3S(?:750|10))(G4P(?:\d+(?:\.\d*)?|\.\d+))", text)
        expanded.extend(combined.groups() if combined else (text,))
        commands.append(Command(line_number, text))
        words = WORD.findall(text)
        if any(letter in "XY" for letter, _ in words) and first_motion is None:
            first_motion = len(commands) - 1
        if any(letter == "M" for letter, _ in words):
            if first_pen is None:
                first_pen = len(commands) - 1
            pen_values.extend(float(value) for letter, value in words if letter == "S")
    if not commands:
        raise StreamError("Die Datei enthält keine Befehle")
    parse_gcode("\n".join(expanded))  # Reject relative motion, homing, Z and other commands.
    if (not pen_values or pen_values[0] != 750 or pen_values[-1] != 750
            or first_motion is None or first_pen >= first_motion):
        raise StreamError("Die Plotterdatei muss vor der ersten Fahrt und am Ende den Stift anheben")
    return tuple(commands)


def simplify_program(source: str, tolerance: float) -> tuple[Command, ...]:
    """Reduce consecutive drawing G1 segments; retain pen, timing and feed barriers."""
    if not math.isfinite(tolerance) or tolerance < 0:
        raise StreamError("Vereinfachung muss endlich und nicht negativ sein")
    commands = prepare_program(source)
    if tolerance == 0:
        return commands
    result = []
    pending = []
    anchor = None
    position = [None, None]
    feed = None
    down = False

    def flush():
        if not pending:
            return
        points = [anchor] + [point for command, point in pending]
        keep = {0, len(points) - 1}
        ranges = [(0, len(points) - 1)]
        while ranges:
            start, end = ranges.pop()
            ax, ay = points[start]
            bx, by = points[end]
            dx, dy = bx - ax, by - ay
            length2 = dx * dx + dy * dy
            farthest, distance2 = None, -1.
            for index in range(start + 1, end):
                x, y = points[index]
                fraction = max(0., min(1., ((x-ax)*dx + (y-ay)*dy) / length2)) if length2 else 0.
                error2 = (x-ax-fraction*dx)**2 + (y-ay-fraction*dy)**2
                if error2 > distance2:
                    farthest, distance2 = index, error2
            if farthest is not None and distance2 > tolerance * tolerance:
                keep.add(farthest)
                ranges.extend(((start, farthest), (farthest, end)))
        result.extend(command for index, (command, point) in enumerate(pending, 1) if index in keep)
        pending.clear()

    for command in commands:
        words = WORD.findall(command.text)
        values = {letter: float(value) for letter, value in words}
        # Restrict simplification to self-contained, explicit XY/F moves.
        eligible = (len(words) == 4 and set(values) == set("GXYF")
                    and values["G"] == 1 and down
                    and all(value is not None for value in position)
                    and values["F"] == feed)
        if eligible:
            if not pending:
                anchor = tuple(position)
            pending.append((command, (values["X"], values["Y"])))
        else:
            flush()
            result.append(command)
        for index, key in enumerate(("X", "Y")):
            if key in values:
                position[index] = values[key]
        if "F" in values:
            feed = values["F"]
        if "M" in values:
            down = values["S"] == 10
    flush()
    # Recheck the emitted program using the same validation as an ordinary job.
    prepare_program("\n".join(command.text for command in result))
    return tuple(result)


def check_abort(cancel=None) -> None:
    if cancel is not None and cancel():
        raise KeyboardInterrupt


def read_line(port, timeout: float, cancel=None) -> str:
    deadline = time.monotonic() + timeout
    buffer = bytearray()
    while time.monotonic() < deadline:
        check_abort(cancel)
        byte = port.read(1)
        if not byte:
            continue
        if byte == b"\n":
            return buffer.decode("ascii", errors="replace").strip()
        buffer.extend(byte)
        if len(buffer) > 4096:
            raise StreamError("Controllerantwort überschreitet 4096 Bytes")
    raise StreamError("Zeitüberschreitung beim Warten auf die Controllerantwort")


def write_bytes(port, payload: bytes) -> None:
    if port.write(payload) != len(payload):
        raise StreamError("USB-Befehl wurde nicht vollständig geschrieben")


def wait_ok(port, timeout: float, report=print, cancel=None) -> None:
    deadline = time.monotonic() + timeout
    home_requested = False

    def poll():
        nonlocal home_requested
        try:
            return cancel() if cancel is not None else False
        except ReturnHome:
            home_requested = True
            return False

    while time.monotonic() < deadline:
        response = read_line(port, max(0., deadline - time.monotonic()), poll)
        if response == "ok":
            if home_requested:
                raise ReturnHome
            return
        if response.startswith(("error:", "ALARM:", "Grbl ")):
            raise StreamError(response)
        if response:
            report(f"Controller: {response}")
    raise StreamError("Keine Bestätigung vom Controller")


def finish_and_return_home(port, timeout: float, report=print, cancel=None) -> None:
    # Send-response can have motions already in the planner. Let these finish
    # before raising/parking, without resuming an interrupted job or resetting.
    report("Q: Übertragung beendet; warte auf bereits übertragene Bewegungen")
    wait_finished(port, timeout, report, cancel)
    report("Stift anheben und zum Arbeitsnullpunkt X0 Y0 zurückfahren")
    stream(port, zero_commands(), timeout, report, cancel)
    wait_finished(port, timeout, report, cancel)
    report("Arbeitsnullpunkt erreicht, Stift oben; Skript wird beendet")


def require_idle(port, timeout: float, report=print) -> None:
    write_bytes(port, b"?")  # Real-time query, no queued command or 'ok'.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = read_line(port, max(0., deadline - time.monotonic()))
        if response.startswith("<") and response.endswith(">"):
            state = re.split(r"[|,]", response[1:-1], maxsplit=1)[0]
            if state.lower() != "idle":
                raise StreamError(f"Controller ist {state}; Start nur im Zustand Idle")
            return
        if response.startswith(("error:", "ALARM:", "Grbl ")):
            raise StreamError(response)
        if response:
            report(f"Controller: {response}")
    raise StreamError("Controller meldet keinen Idle-Zustand")


def zero_commands(travel_speed: float = 6000.) -> tuple[Command, ...]:
    """Raise the pen and move to the current work origin, without homing/reset."""
    if not math.isfinite(travel_speed) or travel_speed <= 0:
        raise StreamError("Fahrvorschub muss positiv und endlich sein")
    lines = ("G21", "G90", "G94", "M3S750", "G4P0.15",
             f"G1X0Y0F{number(travel_speed)}")
    return tuple(Command(i, text) for i, text in enumerate(lines, 1))


def outline_commands(source: str, travel_speed: float = 6000.) -> tuple[tuple[Command, ...], tuple[float, float, float, float]]:
    """Trace the drawn strokes' bounding box once with the pen raised."""
    commands = prepare_program(source)
    if not math.isfinite(travel_speed) or travel_speed <= 0:
        raise StreamError("Fahrvorschub muss positiv und endlich sein")
    # Expand the supported combined servo/dwell blocks as prepare_program does.
    lines = []
    for command in commands:
        combined = re.fullmatch(r"(M3S(?:750|10))(G4P(?:\d+(?:\.\d*)?|\.\d+))", command.text)
        lines.extend(combined.groups() if combined else (command.text,))
    strokes = parse_gcode("\n".join(lines))
    points = [point for stroke in strokes for point in stroke]
    xmin, ymin = min(x for x, y in points), min(y for x, y in points)
    xmax, ymax = max(x for x, y in points), max(y for x, y in points)
    corners = ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin))
    lines = ["G21", "G90", "G94", "M3S750", "G4P0.15"]
    lines.extend(f"G1X{number(x)}Y{number(y)}F{number(travel_speed)}" for x, y in corners)
    lines.append(f"G1X0Y0F{number(travel_speed)}")
    return tuple(Command(i, line) for i, line in enumerate(lines, 1)), (xmin, ymin, xmax, ymax)


def wait_finished(port, timeout: float, report=print, cancel=None) -> None:
    """An acknowledged motion may still be running; wait for actual Idle."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_abort(cancel)
        write_bytes(port, b"?")
        while time.monotonic() < deadline:
            response = read_line(port, max(0., deadline - time.monotonic()), cancel)
            if response.startswith("<") and response.endswith(">"):
                state = re.split(r"[|,]", response[1:-1], maxsplit=1)[0]
                if state.lower() == "idle":
                    return
                if state.lower() != "run":
                    raise StreamError(f"Controller ist {state}; Abschluss benötigt Idle")
                break
            if response.startswith(("error:", "ALARM:", "Grbl ")):
                raise StreamError(response)
            if response:
                report(f"Controller: {response}")
        time.sleep(min(.1, max(0., deadline - time.monotonic())))
    raise StreamError("Zeitüberschreitung: Controller erreicht nach der Fahrt kein Idle")


def wait_ready(port, timeout: float, minimum_wait: float = 5., report=print) -> None:
    """Drain boot output, then require Idle followed by two quiet seconds.

    Some controllers briefly answer a status query before their boot banner.
    A banner is expected here, but remains a fatal reset during a running job.
    """
    started_at = time.monotonic()
    last_activity = started_at
    buffer = bytearray()
    query_pending = False
    idle_confirmed = False
    last_state = None
    while time.monotonic() - started_at < timeout:
        byte = port.read(1)
        now = time.monotonic()
        if byte:
            last_activity = now
            if byte != b"\n":
                buffer.extend(byte)
                if len(buffer) > 4096:
                    raise StreamError("Controllerantwort überschreitet 4096 Bytes")
                continue
            response = buffer.decode("ascii", errors="replace").strip()
            buffer.clear()
            if response.startswith(("error:", "ALARM:")):
                raise StreamError(response)
            if response.startswith("<") and response.endswith(">"):
                state = re.split(r"[|,]", response[1:-1], maxsplit=1)[0]
                if state.lower() == "home":
                    if last_state != "home":
                        report("Controller ist Home: warte auf das Ende der Referenzfahrt")
                    last_state = "home"
                    idle_confirmed = False
                    query_pending = False
                    continue
                if state.lower() != "idle":
                    raise StreamError(f"Controller ist {state}; Start nur im Zustand Idle")
                last_state = "idle"
                if query_pending:
                    idle_confirmed = True
                    query_pending = False
            elif response:
                report(f"Controller: {response}")
                # Boot output after an early Idle means readiness must be checked again.
                idle_confirmed = False
                query_pending = False
        elif (not buffer and now - started_at >= minimum_wait
              and now - last_activity >= 2.):
            if idle_confirmed:
                report("Controller bereit: Startphase ruhig, Idle bestätigt")
                return
            if not query_pending:
                write_bytes(port, b"?")
                query_pending = True
    detail = "; zuletzt Home (Referenzfahrt)" if last_state == "home" else ""
    raise StreamError("Controller wird nicht startbereit: keine ruhige Startphase mit Idle" + detail)


def stream(port, commands: tuple[Command, ...], timeout: float, report=print, cancel=None) -> None:
    for index, command in enumerate(commands, 1):
        try:
            check_abort(cancel)
            write_bytes(port, (command.text + "\n").encode("ascii"))
            wait_ok(port, timeout, report, cancel)
        except (StreamError, OSError) as error:
            raise StreamError(f"Dateizeile {command.line} [{command.text}]: {error}") from error
        if index % 100 == 0 or index == len(commands):
            report(f"Bestätigt: {index}/{len(commands)}")


def stream_buffered(port, commands: tuple[Command, ...], timeout: float,
                    report=print, cancel=None, rx_buffer: int = 120) -> None:
    """Count unacknowledged bytes; queue only motion, drain before any barrier."""
    if not isinstance(rx_buffer, int) or isinstance(rx_buffer, bool) or not 1 <= rx_buffer <= 120:
        raise StreamError("Empfangsfenster muss zwischen 1 und 120 Bytes liegen")
    blocks = []
    for command in commands:
        payload = (command.text + "\n").encode("ascii")
        words = WORD.findall(command.text)
        motion = ([float(value) for letter, value in words if letter == "G"] in ([0.], [1.])
                  and any(letter in "XY" for letter, value in words)
                  and all(letter in "GXYF" for letter, value in words))
        if len(payload) > rx_buffer:
            raise StreamError(f"Dateizeile {command.line}: Befehl größer als Empfangsfenster")
        blocks.append((command, payload, motion))
    pending = deque()
    used = index = confirmed = 0
    home_requested = False

    def poll():
        nonlocal home_requested
        try:
            return cancel() if cancel is not None else False
        except ReturnHome:
            home_requested = True
            return False

    while index < len(blocks) or pending:
        check_abort(poll)
        while not home_requested and index < len(blocks):
            command, payload, motion = blocks[index]
            # A pen command, pause, or modal change must be sent alone.
            if pending and (not motion or not pending[-1][2]):
                break
            if motion and used + len(payload) > rx_buffer:
                break
            if pending and getattr(port, "in_waiting", 0):
                break  # Observe available errors before writing more.
            check_abort(poll)
            if home_requested:
                break
            try:
                write_bytes(port, payload)
            except (StreamError, OSError) as error:
                raise StreamError(f"Dateizeile {command.line} [{command.text}]: {error}") from error
            pending.append((command, len(payload), motion))
            used += len(payload)
            index += 1
            if not motion:
                break
        if pending:
            command, size, motion = pending[0]
            try:
                # poll records ReturnHome without interrupting a partial answer.
                # All queued acknowledgements must be drained before parking.
                wait_ok(port, timeout, report, poll)
            except (StreamError, OSError) as error:
                raise StreamError(f"Dateizeile {command.line} [{command.text}]: {error}") from error
            pending.popleft()
            used -= size
            confirmed += 1
            if confirmed % 100 == 0 or confirmed == len(blocks):
                report(f"Bestätigt: {confirmed}/{len(blocks)}")
        if home_requested and not pending:
            raise ReturnHome


JOG_HELP = ("Pfeiltasten: X/Y fahren | 1: 0,1 mm | 2: 1 mm | 3: 10 mm\n"
            "U/Leertaste: Stift hoch | D: Stift runter | Z: hier X/Y null setzen (G54)\n"
            "Enter: Vorbereitung beenden / Datei starten | Q: Stift hoch und X0 Y0 | Esc: beenden")


class Keyboard:
    """Read single keys in a Windows or POSIX terminal, restoring its settings."""
    def __init__(self):
        self.decoder = codecs.getincrementaldecoder(sys.stdin.encoding or "utf-8")()

    def __enter__(self):
        if not sys.stdin.isatty():
            raise StreamError("Tastaturmodus benötigt ein interaktives Terminal")
        if os.name != "nt":
            import termios
            import tty
            self.fd = sys.stdin.fileno()
            self.saved = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
            active = termios.tcgetattr(self.fd)
            active[0] &= ~termios.IXON  # Ctrl+Q must reach the typewriter.
            termios.tcsetattr(self.fd, termios.TCSANOW, active)
        return self

    def __exit__(self, *args):
        if os.name != "nt":
            import termios
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)

    def read_key(self, timeout=None, *, preserve_case=False):
        if os.name == "nt":
            import msvcrt
            if timeout is not None and not msvcrt.kbhit():
                return None
            key = msvcrt.getwch()
            if key in ("\x00", "\xe0"):
                return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(msvcrt.getwch(), "")
        else:
            import select
            while True:
                if timeout is not None and not select.select([self.fd], [], [], timeout)[0]:
                    return None
                byte = os.read(self.fd, 1)
                if not byte:
                    raise StreamError("Tastatur-Terminal wurde geschlossen")
                key = self.decoder.decode(byte)
                if key:
                    break
            if key == "\x1b":
                sequence = ""
                for _ in range(2):
                    if not select.select([self.fd], [], [], .05)[0]:
                        break
                    sequence += os.read(self.fd, 1).decode("ascii", errors="ignore")
                if sequence:
                    return {"[A": "up", "[B": "down", "[C": "right", "[D": "left",
                            "OA": "up", "OB": "down", "OC": "right", "OD": "left"}.get(sequence, "")
        if key == "\x03":
            raise KeyboardInterrupt
        return {"\r": "enter", "\n": "enter", "\x1b": "escape"}.get(key, key if preserve_case else key.lower())

    def discard_pending(self):
        # Do not accumulate key repeats while a step is in progress.
        if os.name == "nt":
            import msvcrt
            while msvcrt.kbhit():
                msvcrt.getwch()
        else:
            import termios
            termios.tcflush(self.fd, termios.TCIFLUSH)
        self.decoder.reset()


@contextmanager
def abort_keys(report=print):
    """Poll the terminal during serial waits; Ctrl+C also works without a TTY."""
    if not sys.stdin.isatty():
        yield None
        return
    with Keyboard() as keyboard:
        keyboard.discard_pending()
        report("Q: Stift anheben, nach X0 Y0 zurückfahren und beenden | Esc/Strg+C: Abbruch")
        def cancel():
            key = keyboard.read_key(timeout=0)
            if key == "q":
                raise ReturnHome
            return key == "escape"
        yield cancel


def jog_session(port, timeout: float, keyboard, jog_speed: float = 600., report=print) -> bool:
    """Prepare the origin interactively; return True only for an explicit Enter."""
    if not math.isfinite(jog_speed) or jog_speed <= 0:
        raise StreamError("Tastatur-Fahrvorschub muss positiv und endlich sein")

    def execute(lines):
        require_idle(port, timeout, report)
        stream(port, tuple(Command(i, line) for i, line in enumerate(lines, 1)), timeout, lambda _: None)
        wait_finished(port, timeout, report)
        keyboard.discard_pending()

    execute(("G21", "G90", "G94", "G54", "M3S750", "G4P0.15"))
    report(JOG_HELP)
    step = 1.
    report(f"Schrittweite: {number(step)} mm; Vorschub: {number(jog_speed)} mm/min")
    directions = {"left": ("X", -1), "right": ("X", 1), "up": ("Y", 1), "down": ("Y", -1)}
    while True:
        key = keyboard.read_key()
        if key in ("1", "2", "3"):
            step = {"1": .1, "2": 1., "3": 10.}[key]
            report(f"Schrittweite: {number(step)} mm")
        elif key in directions:
            axis, sign = directions[key]
            # Lift before every move, even if D was used to inspect the tip.
            execute(("M3S750", "G4P0.15", f"G91G1{axis}{number(sign*step)}F{number(jog_speed)}", "G90"))
            report(f"{axis}: {number(sign*step)} mm; Stift oben")
        elif key in ("u", " ", "d"):
            execute(("M3S10" if key == "d" else "M3S750", "G4P0.15"))
            report("Stift unten" if key == "d" else "Stift oben")
        elif key == "z":
            execute(("M3S750", "G4P0.15", "G10L20P1X0Y0"))
            report("Aktuelle Position ist jetzt Arbeitsnullpunkt X0 Y0 (G54)")
        elif key == "q":
            raise ReturnHome
        elif key in ("enter", "escape"):
            execute(("G90", "M3S750", "G4P0.15"))
            return key == "enter"


TYPEWRITER_HELP = ("Schreibmaschine: Zeichen sofort schreiben | Leerzeichen: weiter | Enter: neue Zeile links\n"
                   "Strg+Q: Stift hoch, X0 Y0 und beenden | Esc/Strg+C: Bewegungsstopp\n"
                   "Q schreibt einen Buchstaben. Rücktaste kann geschriebene Zeichen nicht löschen.")


def typewriter_session(port, timeout: float, keyboard, writer: Typewriter,
                       tolerance: float = 0., report=print, *, buffered: bool = False,
                       rx_buffer: int = 120, timer: PrintTimer | None = None) -> None:
    pending = deque()

    def cancel():
        if timer is not None:
            timer.tick()
        key = keyboard.read_key(timeout=0, preserve_case=True)
        if key == "\x11":
            raise ReturnHome
        if key == "escape":
            return True
        if key:
            if len(pending) >= 4096:
                raise StreamError("Tastaturpuffer voll; Übertragung angehalten")
            pending.append(key)
        return False

    report(TYPEWRITER_HELP)
    report(f"Dokument: {number(writer.settings.width)} × {number(writer.settings.height)} mm; "
           "oben links X0 Y0, rechts +X, nach unten −Y")
    initial = (Command(1, "G54"),) + zero_commands(writer.settings.travel_speed)
    stream(port, initial, timeout, lambda _: None, cancel)
    wait_finished(port, timeout, report, cancel)
    while True:
        if timer is not None:
            timer.tick()
        key = pending.popleft() if pending else keyboard.read_key(
            timeout=.1 if timer is not None else None, preserve_case=True)
        if key == "\x11":
            raise ReturnHome
        if key == "escape":
            raise KeyboardInterrupt
        if not key:
            if timer is not None:
                time.sleep(.05)  # Windows nonblocking keyboard must not busy-spin.
            continue
        if key not in ("enter", " ") and (len(key) != 1 or not key.isprintable()):
            report("Taste ignoriert; bereits geschriebene Zeichen können nicht gelöscht werden")
            continue
        try:
            plan = writer.plan(key)
        except PlotError as error:
            report(f"Nicht geschrieben: {error}")
            continue
        # Space and newline contain no drawing; their safe moves are generated
        # by the planner rather than read from a user GCODE file.
        if key in ("enter", " "):
            commands = tuple(Command(i, line) for i, line in enumerate(plan.source.splitlines(), 1))
        else:
            commands = simplify_program(plan.source, tolerance) if tolerance else prepare_program(plan.source)
        if buffered:
            if key not in ("enter", " "):
                # The initial positioning and previous character both end pen-up.
                # Their modal state is already G21/G90/G94. Avoid repeating its
                # setup and an extra pen-up pause for every character.
                commands = commands[5:]
            stream_buffered(port, commands, timeout, lambda _: None, cancel, rx_buffer)
        else:
            stream(port, commands, timeout, lambda _: None, cancel)
        if not buffered or not pending:
            wait_finished(port, timeout, report, cancel)
        writer.commit(plan)
        report(f"{'Übertragen' if buffered else 'Geschrieben'}: {key!r}; "
               f"Zeile {writer.cursor.row + 1}, X {number(round(writer.cursor.x, 4))} mm")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, nargs="?", help="Plotter-GCODE mit S750/S10")
    parser.add_argument("--port", help="USB-Port, z. B. COM3 oder /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=60., help="Bestätigungsfrist in Sekunden")
    parser.add_argument("--startup-wait", type=float, default=5.,
                        help="Mindestwartezeit beim Verbinden in Sekunden (Standard: 5)")
    parser.add_argument("--list-ports", action="store_true", help="Ports auflisten, nicht verbinden")
    parser.add_argument("--check", action="store_true", help="Nur Datei prüfen, nicht verbinden")
    parser.add_argument("--buffered", action="store_true",
                        help="Fahrbefehle mit Bytezählung puffern, für flüssigere Ausgabe")
    parser.add_argument("--rx-buffer", type=int, default=120,
                        help="Empfangsfenster für --buffered in Bytes (1–120; Standard: 120)")
    parser.add_argument("--simplify", type=float, default=0., metavar="MM",
                        help="Zeichenwege mit maximal MM Abweichung vereinfachen (z. B. 0.02; 0 = aus)")
    parser.add_argument("--zero", action="store_true",
                        help="Stift anheben und zum Arbeitsnullpunkt X0 Y0 fahren, ohne Datei")
    parser.add_argument("--travel-speed", type=float, default=6000.,
                        help="Fahrvorschub für --zero/--outline in mm/min (Standard: 6000)")
    parser.add_argument("--outline", action="store_true",
                        help="Nur den Schreibbereich der Datei einmal mit Stift oben umfahren")
    parser.add_argument("--jog", action="store_true",
                        help="Position per Tastatur ausrichten, optional vor einer Datei")
    parser.add_argument("--jog-speed", type=float, default=600.,
                        help="Tastatur-Fahrvorschub in mm/min (Standard: 600 = 10 mm/s)")
    parser.add_argument("--typewriter", action="store_true", help="Tastatur als Schreibmaschine; X0 Y0 ist oben links")
    parser.add_argument("--font", type=Path, default=FONT_ROOT / "tavernenbarde", help="Schrift für --typewriter")
    for option, default, description in (
        ("scale", .14, "Schriftmaßstab"), ("width", 210., "Dokumentbreite in mm ab X0"),
        ("height", 297., "Dokumenthöhe in mm nach unten ab Y0"),
        ("line-spacing", 60., "Zeilenabstand vor Skalierung"),
        ("character-spacing", .5, "Zeichenabstand vor Skalierung"),
        ("word-spacing", 12., "Leerzeichenvorschub vor Skalierung"),
        ("write-speed", 24000., "Schreibvorschub in mm/min"),
        ("dwell", .15, "Stiftpause in Sekunden"),
    ):
        parser.add_argument(f"--{option}", type=float, default=default, help=f"Für --typewriter: {description}")
    args = parser.parse_args(argv)
    if not 1 <= args.rx_buffer <= 120:
        parser.error("--rx-buffer muss zwischen 1 und 120 liegen")
    if not args.buffered and args.rx_buffer != 120:
        parser.error("--rx-buffer gilt nur für --buffered")
    if args.buffered and (args.list_ports or args.zero or args.outline or (args.file is None and not args.typewriter)):
        parser.error("--buffered benötigt einen Schreibjob oder --typewriter")
    if args.typewriter and (args.file is not None or args.zero or args.outline or args.list_ports):
        parser.error("--typewriter nicht mit Datei, --zero, --outline oder --list-ports kombinieren")
    if not math.isfinite(args.simplify) or args.simplify < 0:
        parser.error("--simplify muss endlich und nicht negativ sein")
    if args.simplify and ((args.file is None and not args.typewriter) or args.outline or args.list_ports):
        parser.error("--simplify benötigt einen Schreibjob und gilt nicht für --outline/--list-ports")
    if (args.baud <= 0 or not math.isfinite(args.timeout) or args.timeout <= 0
            or not math.isfinite(args.startup_wait) or args.startup_wait <= 0):
        parser.error("Baudrate, Timeout und Startwartezeit müssen positiv und endlich sein")
    if not math.isfinite(args.travel_speed) or args.travel_speed <= 0:
        parser.error("Fahrvorschub muss positiv und endlich sein")
    if not math.isfinite(args.jog_speed) or args.jog_speed <= 0:
        parser.error("Tastatur-Fahrvorschub muss positiv und endlich sein")
    if args.jog and (args.zero or args.list_ports):
        parser.error("--jog nicht mit --zero oder --list-ports kombinieren")
    if not args.jog and args.jog_speed != 600.:
        parser.error("--jog-speed gilt nur für --jog")
    if args.zero and (args.file is not None or args.list_ports):
        parser.error("--zero nicht mit einer Datei oder --list-ports kombinieren")
    if not args.zero and args.travel_speed != 6000.:
        if not args.outline and not args.typewriter:
            parser.error("--travel-speed gilt nur für --zero/--outline; Dateivorschübe stehen im GCODE")
    if args.outline and (args.file is None or args.zero or args.list_ports):
        parser.error("--outline benötigt eine Datei und ist nicht mit --zero/--list-ports kombinierbar")
    if not args.list_ports and ((args.file is None and not args.zero and not args.jog and not args.typewriter) or (not args.check and not args.port)):
        parser.error("Datei, --zero oder --jog und --port angeben; alternativ --list-ports oder --check")
    if (args.jog or args.typewriter) and not args.check and not sys.stdin.isatty():
        parser.error("--jog/--typewriter benötigt ein interaktives Terminal")
    try:
        if args.list_ports:
            from serial.tools import list_ports
            ports = list(list_ports.comports())
            for port in ports:
                print(f"{port.device}: {port.description}")
            if not ports:
                print("Keine seriellen Ports gefunden")
            return 0
        source = args.file.read_text(encoding="utf-8-sig") if args.file is not None else None
        writer = None
        if args.typewriter:
            writer = Typewriter(load_font(args.font), Settings(
                width=args.width, height=args.height, margin=0, x=0, y=0,
                scale=args.scale, line_spacing=args.line_spacing,
                character_spacing=args.character_spacing, word_spacing=args.word_spacing,
                write_speed=args.write_speed, travel_speed=args.travel_speed, dwell=args.dwell))
        if args.outline:
            commands, (xmin, ymin, xmax, ymax) = outline_commands(source, args.travel_speed)
            print(f"Schreibbereich: X {number(xmin)} bis {number(xmax)}, "
                  f"Y {number(ymin)} bis {number(ymax)} mm; "
                  f"Größe {number(round(xmax-xmin, 4))} × {number(round(ymax-ymin, 4))} mm")
            print("Nur Randprüfung: einmal umfahren, Stift oben; anschließend zurück nach X0 Y0")
        else:
            commands = zero_commands(args.travel_speed) if args.zero else (
                prepare_program(source) if source is not None else ())
            if args.simplify and not args.typewriter:
                original_count = len(commands)
                commands = simplify_program(source, args.simplify)
                print(f"Vereinfacht: {original_count} auf {len(commands)} Befehle; "
                      f"maximale Pfadabweichung {number(args.simplify)} mm")
        if args.check:
            if args.buffered:
                print(f"Gepufferte Übertragung: Empfangsfenster {args.rx_buffer} Bytes")
            if args.typewriter:
                print("Schreibmaschineneinstellungen geprüft; keine USB-Verbindung geöffnet")
            else:
                print(f"Geprüft: {len(commands)} Plotterbefehle; keine USB-Verbindung geöffnet")
            if args.zero or args.outline:
                print("\n".join(command.text for command in commands))
            if args.jog:
                print(JOG_HELP)
            if args.typewriter:
                print(f"Schrift geprüft: {args.font}; {len(writer.font)} Zeichen; Dokument oben links X0 Y0")
                print(TYPEWRITER_HELP)
            return 0
        import serial
        connection = serial.Serial(port=None, baudrate=args.baud, timeout=.1, write_timeout=5)
        connection.dtr = False
        connection.rts = False
        if os.name == "posix":
            connection.exclusive = True
        connection.port = args.port
        started = False
        timer = None
        try:
            connection.open()
            print(f"USB: {args.port}, {args.baud} Baud; warte auf den Controller")
            wait_ready(connection, args.timeout, args.startup_wait)
            started = True
            if args.jog:
                with Keyboard() as keyboard:
                    if args.outline:
                        print("Nach dem Ausrichten startet Enter nur die Randprüfung, keinen Schreibjob.")
                    proceed = jog_session(connection, args.timeout, keyboard, args.jog_speed)
                if not proceed or (not commands and not args.typewriter):
                    started = False
                    print("Tastaturmodus beendet; Controller ist Idle, Stift oben")
                    return 0
            timer = PrintTimer(label="Randprüfungsdauer" if args.outline else (
                "Fahrtdauer" if args.zero else "Druckdauer"))
            if args.typewriter:
                with Keyboard() as keyboard:
                    typewriter_session(connection, args.timeout, keyboard, writer, args.simplify,
                                       buffered=args.buffered, rx_buffer=args.rx_buffer, timer=timer)
            with abort_keys() as cancel:
                cancel = timed_cancel(timer, cancel)
                if args.buffered:
                    stream_buffered(connection, commands, args.timeout, cancel=cancel, rx_buffer=args.rx_buffer)
                else:
                    stream(connection, commands, args.timeout, cancel=cancel)
                wait_finished(connection, args.timeout, cancel=cancel)
                if args.file is not None and not args.outline:
                    print("Schreibjob abgeschlossen; Stift anheben und nach X0 Y0 zurückfahren")
                    stream(connection, zero_commands(), args.timeout, cancel=cancel)
                    wait_finished(connection, args.timeout, cancel=cancel)
            started = False
            print("Randprüfung abgeschlossen; zurück bei X0 Y0, Stift oben" if args.outline else (
                "Arbeitsnullpunkt X0 Y0 erreicht; Controller ist Idle, Stift oben" if args.zero
                else "Schreibjob abgeschlossen; zurück bei X0 Y0, Controller ist Idle, Stift oben"))
            return 0
        except ReturnHome:
            # Keep Esc/Ctrl+C available during parking; another Q is ignored.
            with abort_keys() as cancel:
                def stop_only():
                    try:
                        return cancel() if cancel is not None else False
                    except ReturnHome:
                        return False
                # Forward the emergency callback through the parking sequence.
                parking_cancel = timed_cancel(timer, stop_only) if timer is not None else stop_only
                finish_and_return_home(connection, args.timeout, cancel=parking_cancel)
            started = False
            return 0
        finally:
            try:
                if started and connection.is_open:
                    # Request feed hold. Never reset, retry a failed command, or auto-resume.
                    try:
                        connection.write(b"!")
                    except OSError as error:
                        print(f"Feed Hold konnte nicht übertragen werden: {error}")
            finally:
                try:
                    connection.close()
                finally:
                    if timer is not None:
                        timer.finish()
    except ModuleNotFoundError as error:
        if error.name in ("serial", "serial.tools"):
            parser.exit(2, "pyserial fehlt: python3 -m pip install pyserial\n")
        raise
    except (OSError, UnicodeError, PlotError, StreamError) as error:
        parser.exit(2, f"Abbruch: {error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "Abbruch durch Benutzer; Feed Hold wurde angefordert, falls der Job lief\n")


if __name__ == "__main__":
    raise SystemExit(main())
