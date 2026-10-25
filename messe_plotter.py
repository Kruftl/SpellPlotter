#!/usr/bin/env python3
"""Einfache Messeoberfläche: python3 messe_plotter.py [--demo]."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import queue
import threading

from text_to_gcode import FONT_ROOT, Job, PlotError, Settings, compose, load_font, render_gcode
from send_gcode_sync import (
    Command, check_abort, number, prepare_program, require_idle, stream,
    wait_finished, wait_ready, write_bytes, zero_commands,
)

FONTS = {
    "Geselliger Barde": "geselliger_barde",
    "Tavernenbarde": "tavernenbarde",
    "Bard2": "barde",
    "Standard": "standard",
}
SIZES = {"Klein": .18, "Mittel": .25, "Groß": .35}
MAX_CHARACTERS = 240


@dataclass(frozen=True)
class Machine:
    port: str = ""
    width: float = 210
    height: float = 297
    margin: float = 10
    write_speed: float = 600
    travel_speed: float = 6000

    def settings(self, scale: float) -> Settings:
        settings = Settings(width=self.width, height=self.height, margin=self.margin,
                            x=self.margin, y=self.margin, scale=scale,
                            character_spacing=.4, word_spacing=18, line_spacing=55,
                            write_speed=self.write_speed, travel_speed=self.travel_speed)
        from text_to_gcode import validate_settings
        validate_settings(settings)
        return settings


def make_job(text: str, font: str, size: str, machine: Machine) -> Job:
    if len(text) > MAX_CHARACTERS:
        raise PlotError(f"Bitte höchstens {MAX_CHARACTERS} Zeichen eingeben.")
    if not text.strip():
        raise PlotError("Gib deinen Namen oder einen kurzen Gruß ein.")
    return compose(text, load_font(FONT_ROOT / FONTS[font]), machine.settings(SIZES[size]))


class CancellablePort:
    """Also observe Stop while the existing startup routine reads boot messages."""
    def __init__(self, port, stop: threading.Event):
        self.port, self.stop = port, stop

    def read(self, count):
        check_abort(self.stop.is_set)
        return self.port.read(count)

    def write(self, payload):
        check_abort(self.stop.is_set)
        return self.port.write(payload)


class PlotterSession:
    """One serial connection, used by one background operation at a time."""
    def __init__(self, factory=None):
        self.factory = factory
        self.connection = None
        self.device = None
        self.faulted = False

    @property
    def connected(self):
        return self.connection is not None and self.connection.is_open

    def _hold(self):
        if self.connected:
            write_bytes(self.connection, b"!")

    def connect(self, device: str, stop: threading.Event, report):
        device = device.strip()
        if not device:
            raise PlotError("Bitte einen USB-Port einstellen.")
        if self.connected and device != self.device:
            raise PlotError("Zum Wechseln des USB-Ports zuerst die Verbindung trennen.")
        fresh = not self.connected
        if fresh:
            self.close()
            factory = self.factory
            if factory is None:
                import serial
                factory = serial.Serial
            self.connection = factory(port=None, baudrate=115200, timeout=.1, write_timeout=2)
            self.connection.dtr = False
            self.connection.rts = False
            import os
            if os.name == "posix":
                self.connection.exclusive = True
            self.connection.port = device
            self.device = device
        try:
            check_abort(stop.is_set)
            if fresh:
                self.connection.open()
            port = CancellablePort(self.connection, stop)
            if fresh:
                report("Verbindung wird einmalig vorbereitet …")
                wait_ready(port, 60, report=report)
            else:
                require_idle(port, 60, report=report)
            # Explicitly select the same work coordinate system as the setup panel.
            commands = self._commands(("G21", "G90", "G94", "G54", "M3S750", "G4P0.15"))
            stream(port, commands, 60, report=report, cancel=stop.is_set)
            wait_finished(port, 60, report=report, cancel=stop.is_set)
            self.faulted = False
        except (Exception, KeyboardInterrupt):
            self.faulted = True
            try:
                self._hold()
            finally:
                if fresh:
                    self.close()
            raise

    @staticmethod
    def _commands(lines):
        return tuple(Command(i, line) for i, line in enumerate(lines, 1))

    def _execute(self, commands, stop, report, parking=None):
        if not self.connected or self.faulted:
            raise PlotError("Bitte im Betreuermenü die Verbindung herstellen / prüfen.")
        port = CancellablePort(self.connection, stop)
        try:
            require_idle(port, 60, report=report)
            # Every operation starts in absolute G54 coordinates, including after jogs.
            prefix = self._commands(("G21", "G90", "G94", "G54"))
            stream(port, prefix + commands, 60, report=report, cancel=stop.is_set)
            wait_finished(port, 60, report=report, cancel=stop.is_set)
            if parking is not None:
                report("Stift fährt zur Papier-Ecke zurück …")
                stream(port, parking, 60, report=report, cancel=stop.is_set)
                wait_finished(port, 60, report=report, cancel=stop.is_set)
        except (Exception, KeyboardInterrupt):
            self.faulted = True
            self._hold()
            raise

    def send_job(self, source: str, machine: Machine, stop, report):
        commands = prepare_program(source)  # Validate before any serial operation.
        if machine.port.strip() != self.device:
            raise PlotError("Der eingestellte USB-Port stimmt nicht mit der Verbindung überein.")
        report("Dein Text wird geschrieben …")
        self._execute(commands, stop, report, zero_commands(machine.travel_speed))

    def jog(self, axis: str, distance: float, stop, report):
        import math
        if axis not in ("X", "Y") or not math.isfinite(distance) or not 0 < abs(distance) <= 10:
            raise PlotError("Fahrschritt muss auf X/Y liegen und höchstens 10 mm betragen.")
        self._execute(self._commands(("M3S750", "G4P0.15",
                      f"G91G1{axis}{number(distance)}F600", "G90")), stop, report)

    def pen(self, down: bool, stop, report):
        self._execute(self._commands(("M3S10" if down else "M3S750", "G4P0.15")), stop, report)

    def set_zero(self, stop, report):
        self._execute(self._commands(("M3S750", "G4P0.15", "G10L20P1X0Y0")), stop, report)

    def go_zero(self, speed, stop, report):
        self._execute(zero_commands(speed), stop, report)

    def close(self):
        try:
            if self.connection is not None:
                self.connection.close()
        finally:
            self.connection = None
            self.device = None


class App:
    def __init__(self, root, machine: Machine, demo: bool):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root, self.machine, self.demo = root, machine, demo
        self.session = PlotterSession()
        self.admin_controls = []
        self.operation = None
        self.admin_dialog = None
        self.busy = False
        self.blocked = False
        self.stop = threading.Event()
        self.events = queue.Queue()
        self.preview_after = None
        self.job = None
        root.title("Schreib dir einen Gruß · Stiftplotter")
        root.geometry("1050x760")
        root.minsize(850, 650)
        ttk.Style().configure("Big.TButton", font=("Arial", 16), padding=12)
        body = ttk.Frame(root, padding=22)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Dein Gruß zum Mitnehmen", font=("Arial", 27, "bold")).pack(anchor="w")
        mode = "Demo: Es wird keine Maschine verbunden." if demo else "Schreibe deinen Namen oder einen kurzen Gruß."
        ttk.Label(body, text=mode, font=("Arial", 13)).pack(anchor="w", pady=(5, 15))
        columns = ttk.Frame(body)
        columns.pack(fill="both", expand=True)
        left = ttk.Frame(columns)
        left.pack(side="left", fill="both", expand=True, padx=(0, 20))
        ttk.Label(left, text="1. Deinen Text eingeben (max. 240 Zeichen)", font=("Arial", 14)).pack(anchor="w")
        self.text = tk.Text(left, height=6, width=34, wrap="none", font=("Arial", 20), undo=True)
        self.text.pack(fill="both", expand=True, pady=10)
        scroll = ttk.Scrollbar(left, orient="horizontal", command=self.text.xview)
        scroll.pack(fill="x")
        self.text.configure(xscrollcommand=scroll.set)
        self.text.bind("<<Modified>>", self.modified)
        ttk.Label(left, text="Zeilen mit Enter umbrechen. Die Vorschau zeigt den Platz auf dem Papier.",
                  wraplength=430).pack(anchor="w")
        options = ttk.Frame(left)
        options.pack(fill="x", pady=12)
        self.font = tk.StringVar(value="Geselliger Barde")
        self.size = tk.StringVar(value="Mittel")
        self.controls = []
        for label, variable, values in (("Schrift", self.font, tuple(FONTS)), ("Größe", self.size, tuple(SIZES))):
            group = ttk.Frame(options)
            group.pack(side="left", padx=(0, 12))
            ttk.Label(group, text=label).pack(anchor="w")
            box = ttk.Combobox(group, textvariable=variable, values=values, state="readonly", width=19)
            box.pack(pady=5)
            box.bind("<<ComboboxSelected>>", lambda event: self.preview())
            self.controls.append(box)
        self.ready = tk.BooleanVar(value=False)
        self.paper = ttk.Checkbutton(left, text="2. Neues Papier liegt bereit (Betreuer hat ausgerichtet)",
                                     variable=self.ready, command=self.update_start)
        self.paper.pack(anchor="w", pady=10)
        self.start = ttk.Button(left, text="3. Schreiben", style="Big.TButton", command=self.start_job)
        self.start.pack(fill="x", pady=6)
        self.hold = ttk.Button(left, text="Anhalten", style="Big.TButton", command=self.request_stop, state="disabled")
        self.hold.pack(fill="x", pady=6)
        self.clear_button = ttk.Button(left, text="Neuer Text", command=self.clear)
        self.clear_button.pack(fill="x", pady=6)
        right = ttk.Frame(columns)
        right.pack(side="right", fill="both", expand=True)
        ttk.Label(right, text="Vorschau", font=("Arial", 14)).pack(anchor="w")
        self.canvas = tk.Canvas(right, width=400, height=470, background="#e4e8eb", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, pady=10)
        self.canvas.bind("<Configure>", lambda event: self.draw())
        self.status = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.status, wraplength=950, font=("Arial", 13)).pack(fill="x", pady=12)
        footer = ttk.Frame(body)
        footer.pack(fill="x")
        self.admin = ttk.Button(footer, text="Betreuer · Einrichtung", command=self.configure)
        self.admin.pack(side="left")
        self.connection_status = tk.StringVar()
        ttk.Label(footer, textvariable=self.connection_status).pack(side="left", padx=15)
        ttk.Button(footer, text="Vollbild (F11)", command=self.fullscreen).pack(side="right")
        root.bind("<F11>", self.fullscreen)
        root.bind("<Escape>", lambda event: root.attributes("-fullscreen", False))
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.preview()
        root.after(100, self.poll)

    def fullscreen(self, event=None):
        self.root.attributes("-fullscreen", not self.root.attributes("-fullscreen"))

    def modified(self, event=None):
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        # Invalidate immediately, even before the debounced preview runs.
        self.job = None
        self.update_start()
        if self.preview_after:
            self.root.after_cancel(self.preview_after)
        self.preview_after = self.root.after(250, self.preview)

    def preview(self):
        self.preview_after = None
        if self.busy:
            return
        self.job = None
        try:
            self.job = make_job(self.text.get("1.0", "end-1c"), self.font.get(), self.size.get(), self.machine)
            if not self.blocked:
                self.status.set("Der Text passt. Neues Papier bestätigen und Schreiben drücken.")
        except (PlotError, OSError, ValueError) as error:
            if not self.blocked:
                self.status.set(str(error))
        self.draw()
        self.update_start()

    def draw(self):
        self.canvas.delete("all")
        w, h = max(100, self.canvas.winfo_width()), max(100, self.canvas.winfo_height())
        m = self.machine
        factor = min((w - 30) / m.width, (h - 30) / m.height)
        ox, oy = (w - m.width * factor) / 2, (h - m.height * factor) / 2
        def point(x, y):
            return ox + x * factor, oy + (m.height - y) * factor
        self.canvas.create_rectangle(*point(0, m.height), *point(m.width, 0), fill="white", outline="#88929a")
        self.canvas.create_rectangle(*point(m.margin, m.height - m.margin),
                                     *point(m.width - m.margin, m.margin), dash=(4, 4), outline="#bbc3ca")
        if self.job:
            for path in self.job.paths:
                coordinates = [v for x, y in path.points for v in point(x, y)]
                if len(coordinates) >= 4:
                    self.canvas.create_line(*coordinates, fill="#224d63", width=1.3)

    def update_start(self):
        if self.demo:
            self.connection_status.set("Demo · ohne USB")
        elif self.session.connected:
            suffix = " · prüfen" if self.session.faulted else ""
            self.connection_status.set(f"USB verbunden: {self.session.device}{suffix}")
        else:
            self.connection_status.set("USB nicht verbunden")
        allowed = self.job is not None and self.ready.get() and not self.busy and not self.blocked
        self.start.configure(state="normal" if allowed else "disabled")

    def clear(self):
        self.text.delete("1.0", "end")
        self.ready.set(False)
        self.preview()
        self.text.focus_set()

    def configure(self):
        from tkinter import messagebox
        if self.busy:
            return
        if self.admin_dialog is not None and self.admin_dialog.winfo_exists():
            self.admin_dialog.lift()
            return
        tk, ttk = self.tk, self.ttk
        dialog = tk.Toplevel(self.root)
        self.admin_dialog = dialog
        dialog.title("Einrichtung und Ausrichten")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Papiernullpunkt: unten links · Stift S750 oben / S10 unten · 115200 Baud\n"
                  "Einmal verbinden, Stift ausrichten, Nullpunkt setzen und Einrichtung bestätigen.").grid(
                      row=0, column=0, columnspan=2, pady=8)
        self.admin_controls = []
        variables = {}
        fields = (("port", "USB-Port"), ("width", "Papierbreite (mm)"), ("height", "Papierhöhe (mm)"),
                  ("margin", "Rand (mm)"), ("write_speed", "Schreiben (mm/min)"),
                  ("travel_speed", "Fahren (mm/min)"))
        for row, (name, label) in enumerate(fields, 1):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=3)
            variables[name] = tk.StringVar(value=str(getattr(self.machine, name)))
            entry = ttk.Entry(frame, textvariable=variables[name], width=28)
            entry.grid(row=row, column=1)
            self.admin_controls.append(entry)
        ports = tk.StringVar(value="")
        def scan():
            try:
                from serial.tools import list_ports
                ports.set("\n".join(f"{p.device}: {p.description}" for p in list_ports.comports()) or "Keine Ports gefunden")
            except ImportError:
                ports.set("USB benötigt: python3 -m pip install pyserial")
        def button(parent, label, command, **grid):
            widget = ttk.Button(parent, text=label, command=command)
            widget.grid(**grid, padx=3, pady=3, sticky="ew")
            self.admin_controls.append(widget)
            return widget
        def current_machine():
            values = {name: (v.get().strip() if name == "port" else float(v.get().replace(",", ".")))
                      for name, v in variables.items()}
            machine = Machine(**values)
            machine.settings(SIZES[self.size.get()])
            return machine
        def connect():
            try:
                machine = current_machine()
                if not self.demo and not machine.port:
                    raise PlotError("Bitte den USB-Port angeben.")
                self.machine = machine
                self.blocked = True
                self.run_operation("connect", lambda: self.session.connect(machine.port, self.stop, self.report),
                                   "Verbunden. Stift ausrichten und Einrichtung bestätigen.")
            except ValueError as error:
                messagebox.showerror("Einrichtung prüfen", str(error), parent=dialog)
        def disconnect():
            self.session.close()
            self.blocked = True
            self.ready.set(False)
            self.status.set("Verbindung getrennt. Zum Fortfahren wieder verbinden.")
            self.update_start()
        row = len(fields) + 1
        links = ttk.Frame(frame)
        links.grid(row=row, column=0, columnspan=2)
        button(links, "USB-Ports suchen", scan, row=0, column=0)
        button(links, "Verbinden / prüfen", connect, row=0, column=1)
        button(links, "Trennen", disconnect, row=0, column=2)
        ttk.Label(frame, textvariable=ports, wraplength=520).grid(row=row+1, column=0, columnspan=2)
        step = tk.StringVar(value="1")
        jog = ttk.LabelFrame(frame, text="Stift ausrichten · jeder Fahrschritt hebt den Stift an", padding=8)
        jog.grid(row=row+2, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(jog, text="Schritt (mm):").grid(row=0, column=0)
        step_box = ttk.Combobox(jog, textvariable=step, values=("0.1", "1", "10"), state="readonly", width=6)
        step_box.grid(row=0, column=1)
        self.admin_controls.append(step_box)
        def action(callback, message):
            self.blocked = True  # Moving or inspecting the tip requires a new setup confirmation.
            self.run_operation("setup", callback, message)
        def move(axis, sign):
            distance = sign * float(step.get())
            action(lambda: self.session.jog(axis, distance, self.stop, self.report), "Fahrschritt beendet; Stift oben.")
        button(jog, "↑ Y+", lambda: move("Y", 1), row=1, column=1)
        button(jog, "← X−", lambda: move("X", -1), row=2, column=0)
        button(jog, "↓ Y−", lambda: move("Y", -1), row=2, column=1)
        button(jog, "→ X+", lambda: move("X", 1), row=2, column=2)
        button(jog, "Stift hoch", lambda: action(
            lambda: self.session.pen(False, self.stop, self.report), "Stift oben."), row=1, column=3)
        button(jog, "Stift runter", lambda: action(
            lambda: self.session.pen(True, self.stop, self.report), "Stift unten. Vor Freigabe wieder anheben."), row=2, column=3)
        def set_zero():
            if messagebox.askyesno("Papiernullpunkt setzen", "Aktuelle Stiftposition als X0 Y0 (G54) setzen?\n"
                                   "Diese Position soll unten links am Papier liegen.", parent=dialog):
                action(lambda: self.session.set_zero(self.stop, self.report), "Arbeitsnullpunkt gesetzt; Stift oben.")
        button(jog, "Hier X/Y null setzen", set_zero, row=3, column=0, columnspan=2)
        def go_zero():
            try:
                speed = current_machine().travel_speed
                action(lambda: self.session.go_zero(speed, self.stop, self.report), "Arbeitsnullpunkt erreicht; Stift oben.")
            except ValueError as error:
                messagebox.showerror("Einrichtung prüfen", str(error), parent=dialog)
        button(jog, "Zum Nullpunkt fahren", go_zero, row=3, column=2, columnspan=2)
        confirmed = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Papier und Nullpunkt sind eingerichtet; Maschine ist bereit.\n"
                        "Nach Anhalten/Fehler: Controller wurde manuell geprüft und vorbereitet.",
                        variable=confirmed).grid(row=row+3, column=0, columnspan=2, pady=8)
        ttk.Label(frame, textvariable=self.status, wraplength=560).grid(row=row+4, column=0, columnspan=2, pady=5)
        # Main-window controls are behind this modal dialog, so Stop must also be available here.
        stop_button = ttk.Button(frame, text="Anhalten", command=self.request_stop)
        stop_button.grid(row=row+5, column=0, columnspan=2, sticky="ew", pady=5)
        def apply():
            try:
                machine = current_machine()
                if not self.demo and (not self.session.connected or self.session.faulted):
                    raise PlotError("Bitte zuerst die Verbindung herstellen / prüfen.")
                if not self.demo and machine.port != self.session.device:
                    raise PlotError("Zum Wechseln des USB-Ports zuerst trennen und neu verbinden.")
                if not confirmed.get():
                    raise PlotError("Bitte die Einrichtung der Maschine bestätigen.")
                self.machine = machine
                self.ready.set(False)
                def approved():
                    dialog.destroy()
                    self.admin_dialog = None
                    self.blocked = False
                    self.preview()
                # Finish alignment with the pen raised, using the existing connection.
                self.run_operation("setup", lambda: self.session.pen(False, self.stop, self.report),
                                   "Einrichtung bereit. Neues Papier bestätigen und Text schreiben.", approved)
            except ValueError as error:
                messagebox.showerror("Einrichtung prüfen", str(error), parent=dialog)
        button(frame, "Einrichtung übernehmen", apply, row=row+6, column=0, columnspan=2)
        def close_dialog():
            if self.busy:
                self.request_stop()
                return
            dialog.destroy()
            self.admin_dialog = None
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)

    def report(self, text):
        self.events.put(("status", text))

    def run_operation(self, kind, callback, success, on_success=None):
        if self.busy:
            return
        self.busy = True
        self.operation = (kind, success, on_success)
        self.ready.set(False)
        self.stop.clear()
        self.set_busy_controls(True)
        self.status.set("Demo läuft …" if self.demo else "Bitte warten …")
        def worker():
            try:
                if self.demo:
                    if self.stop.wait(.4 if kind != "job" else 2):
                        raise KeyboardInterrupt
                else:
                    callback()
                self.events.put(("done", None))
            except KeyboardInterrupt:
                self.events.put(("error", "Angehalten. Bitte den Betreuer rufen und die Maschine prüfen lassen."))
            except Exception as error:
                self.events.put(("error", f"Bitte den Betreuer rufen: {error}"))
        threading.Thread(target=worker, daemon=True).start()

    def set_busy_controls(self, busy):
        for widget in (self.paper, self.admin, self.clear_button):
            widget.configure(state="disabled" if busy else "normal")
        self.text.configure(state="disabled" if busy else "normal")
        for widget in self.controls:
            widget.configure(state="disabled" if busy else "readonly")
        for widget in self.admin_controls:
            if widget.winfo_exists():
                state = "readonly" if isinstance(widget, self.ttk.Combobox) else "normal"
                widget.configure(state="disabled" if busy else state)
        self.hold.configure(state="normal" if busy else "disabled")
        self.update_start()

    def start_job(self):
        if self.busy or self.blocked or not self.ready.get():
            return
        # Snapshot and revalidate the actual input instead of trusting an old preview.
        self.preview()
        if not self.job:
            return
        if not self.demo and not self.machine.port:
            self.status.set("Bitte den Betreuer rufen: USB-Port einrichten.")
            return
        source, machine = render_gcode(self.job), self.machine
        self.run_operation("job", lambda: self.session.send_job(source, machine, self.stop, self.report),
                           "Demo fertig. Gib einen neuen Text ein." if self.demo else
                           "Fertig! Nimm dein Blatt mit. Für den nächsten Gruß neues Papier einlegen.")

    def request_stop(self):
        if not self.busy:
            return
        self.stop.set()
        self.hold.configure(state="disabled")
        self.status.set("Anhalten angefordert …")

    def poll(self):
        try:
            while True:
                kind, message = self.events.get_nowait()
                if kind == "status":
                    if not self.stop.is_set():
                        self.status.set(message)
                    continue
                self.busy = False
                operation, success, on_success = self.operation
                self.operation = None
                if kind == "error":
                    self.blocked = True
                self.set_busy_controls(False)
                if kind == "done":
                    if operation == "job":
                        self.clear()
                    if on_success is not None:
                        on_success()
                    self.status.set(success)
                else:
                    self.status.set(message)
                self.update_start()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def close(self):
        from tkinter import messagebox
        if self.busy:
            self.request_stop()
            messagebox.showinfo("Job wird angehalten", "Bitte warten, bis der Job angehalten wurde. Danach Fenster schließen.")
            return
        self.session.close()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Ohne USB-Verbindung ausprobieren")
    parser.add_argument("--port", default="", help="USB-Port vorbelegen, z. B. COM3 oder /dev/ttyUSB0")
    args = parser.parse_args()
    try:
        import tkinter as tk
        root = tk.Tk()
    except ImportError:
        parser.exit(2, "Tkinter fehlt. Unter Debian/Ubuntu: sudo apt install python3-tk\n")
    except tk.TclError as error:
        parser.exit(2, f"Grafische Oberfläche kann nicht gestartet werden: {error}\n")
    app = App(root, Machine(port=args.port), args.demo)
    # A real machine always requires an operator's initial setup confirmation.
    if not args.demo:
        app.blocked = True
        app.status.set("Bitte den Betreuer rufen: verbinden und Stift über Einrichtung ausrichten.")
        if args.port:
            root.after(200, lambda: app.run_operation("connect",
                lambda: app.session.connect(args.port, app.stop, app.report),
                "Verbunden. Bitte Stift über Einrichtung ausrichten und bestätigen."))
        else:
            root.after(200, app.configure)
    root.mainloop()


if __name__ == "__main__":
    main()
