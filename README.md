# Stiftplotter – Text in Handschrift als G-Code

Python-Werkzeuge zum Schreiben von UTF-8-Texten mit einem Stiftplotter:
Aus einzelnen Zeichenpfaden entstehen G-Code-Dateien und eine SVG-Vorschau.
Dazu kommen mehrere einlinige Handschriften, ein USB-Sender für GRBL und
Konstruktionsdateien für Stifthalter.

## Funktionen

- Text auf A4, A5 oder eigenen Papierformaten, hochkant oder quer setzen.
- Schriftgröße, Abstände, Position und Vorschübe einstellen.
- Geeignete Buchstaben verbinden und Punkte sowie Querstriche nachtragen.
- Alle erzeugten Zielpunkte gegen den Papierrahmen mit Innenrand prüfen.
- G-Code und SVG lokal erzeugen, ohne eine Maschine anzuschließen.
- G-Code über USB senden, optional mit Pufferung und Pfadvereinfachung.
- Im Schreibmaschinenmodus direkt über die Tastatur schreiben.

## Inhalt

- [Installation](#installation)
- [Schnellstart](#beispiel-starten)
- [Schriftübersicht](#schriftübersicht)
- [Rahmen und Position](#rahmen-und-position)
- [Geschwindigkeiten, Abstände und Stift](#geschwindigkeiten-abstände-und-stift)
- [Eigene Zeichenvorlagen](#eigene-zeichenvorlagen)
- [Entwicklung und Tests](#sammlung-regenerieren-und-prüfen)
- [Am Plotter](#am-plotter)
- [Projektstruktur](#projektstruktur)
- [Lizenz und Veröffentlichung](#lizenz-und-veröffentlichung)

## Installation

Das Repository klonen oder als ZIP herunterladen und entpacken. Alle folgenden
Befehle im Projektverzeichnis ausführen. Benötigt wird **Python 3.10 oder neuer**.
Unter Windows kann der Aufruf `python` oder `py` statt `python3` lauten.

**Text in G-Code und SVG umwandeln:** keine zusätzlichen Pakete erforderlich.
Die fertigen Zeichensammlungen sind bereits enthalten.

Für optionale Funktionen empfiehlt sich eine virtuelle Umgebung:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Unter Windows (PowerShell) lautet die Aktivierung `.venv\Scripts\Activate.ps1`.
Anschließend nur die benötigten Pakete installieren:

```bash
# USB-Sender und Schreibmaschinenmodus
python3 -m pip install pyserial

# TTF-Export und PNG-Vorschau für „geselliger Barde“
python3 -m pip install -r requirements-font.txt
```

Für die JPG-Probe von Tavernenbarde genügt `python3 -m pip install Pillow`.
Die Abhängigkeiten aus `requirements-font.txt` enthalten Pillow bereits.

## Beispiel starten

Im Projektverzeichnis:

```bash
python3 text_to_gcode.py examples/text.txt \
  --output examples/text.gcode \
  --paper a4 --scale 0.55 \
  --write-speed 600 --travel-speed 18000
```

Das erzeugt `examples/text.gcode` und `examples/text.svg`. Die SVG-Datei lässt
sich im Browser öffnen. Sie zeigt den Papierrahmen, den gestrichelten Innenrand
und die Schrift; orange Linien sind die zusätzlich gezeichneten Verbindungen.
Die Vorschau bildet die ausgegebenen Koordinaten ab, nicht die tatsächliche
Strichbreite eines bestimmten Stifts.

Ein weiterer Beispieltext liegt in `examples/fablab_gruss.txt`. Die fertigen
Dateien `examples/fablab_gruss.gcode`, `examples/fablab_gruss.svg` und
`examples/fablab_gruss.png` zeigen diesen Text auf A4. GCODE und SVG lassen
sich so erneut erzeugen:

```bash
python3 text_to_gcode.py examples/fablab_gruss.txt \
  -o examples/fablab_gruss.gcode --paper a4 --margin 10 \
  --x 20 --y 60 --scale 0.5 --write-speed 600 --travel-speed 6000
```

Für einen eigenen Text ersetze `examples/text.txt` durch deine UTF-8-Datei.
Ein abschließender Zeilenumbruch wird als Dateiende behandelt. Andere
Zeilenumbrüche und Leerzeilen bleiben erhalten. Die erste Zeile steht oben.
Umlaute werden auch aus getrennt gespeicherten Buchstaben und Akzenten erkannt.
Tabulatoren und Zeichen ohne Vorlage führen zu einer Fehlermeldung.

Unterstützte Zeichen: `A–Z`, `a–z`, `ÄÖÜäöüß`, `0–9`, `. , ! ? : ; - – ‘ / © ( )`,
Leerzeichen und Zeilenumbrüche.

## Schriftübersicht

| Schrift | Verzeichnis | Umfang | Besonderheit |
| --- | --- | --- | --- |
| Standard | `handwritingfont/standard/` | 82 Zeichen | Standardschrift ohne `--font` |
| Bard2 | `handwritingfont/barde/` | 82 Zeichen | Runde, nach rechts geneigte Schreibschrift |
| Tavernenbarde · lebhaft | `handwritingfont/tavernenbarde/` | 82 Zeichen | Verbundene Schreibschrift, JPG-Satzprobe |
| geselliger Barde | `handwritingfont/geselliger_barde/` | 113 Zeichen einschließlich Leerzeichen | Erweiterter Zeichensatz, TTF-Font und Mittellinien-Datensatz |

Die oben aufgeführten unterstützten Zeichen beziehen sich auf Standard, Bard2
und Tavernenbarde. „geselliger Barde“ ergänzt unter anderem weitere ASCII-Satzzeichen,
`ẞ`, typografische Anführungszeichen und Auslassungspunkte.

Beispiel mit „geselliger Barde“:

```bash
python3 text_to_gcode.py handwritingfont/geselliger_barde/beispiel.txt \
  --font handwritingfont/geselliger_barde \
  -o examples/geselliger_barde.gcode \
  --paper a4 --orientation landscape --scale 0.23 \
  --x 15 --y 30 --character-spacing 0.4 --word-spacing 18 \
  --line-spacing 55 --travel-speed 6000
```

[Schrift, TTF-Export und Kurvenauflösung](handwritingfont/geselliger_barde/README.md)
sind separat dokumentiert. Der installierbare TTF-Font verwendet geschlossene
Konturen; der Plotter nutzt die zugehörigen Mittellinien aus den G-Code-Vorlagen.

## Rahmen und Position

Die Rahmenprüfung ist **immer aktiv**, standardmäßig für **A4 hochkant mit
5 mm Innenrand**. Das Papier beginnt bei Arbeitskoordinate `X0 Y0` unten links.
Alle Zeichenstriche, Nebenstriche, Verbindungslinien und die Fahrten zwischen
ihnen müssen im nutzbaren Rechteck liegen. Da ausschließlich gerade Segmente
erzeugt werden, liegen auch die Wege zwischen geprüften Endpunkten darin.

| Option | Standard | Bedeutung |
| --- | --- | --- |
| `--paper a4` | A4 | 210 × 297 mm |
| `--paper a5` | | 148 × 210 mm |
| `--orientation landscape` | `portrait` | Breite und Höhe vertauschen |
| `--paper custom --width 100 --height 150` | | Eigener Rahmen in mm, vor Ausrichtung |
| `--margin 5` | 5 mm | Innenrand an allen vier Seiten |
| `--x 5 --y 5` | jeweils 5 mm | Linker und unterer Rand des Textblocks |
| `--scale 0.55` | 1 | Gemeinsamer Skalierungsfaktor |

X/Y bezeichnet **nicht** die Grundlinie der ersten Zeile. Das Skript verschiebt
den gesamten Block nach oben, damit auch Unterlängen auf dem Blatt liegen.
Einrückungen mit Leerzeichen bleiben erhalten. Bei geändertem Innenrand müssen
X und Y mindestens passend gewählt werden, beispielsweise
`--margin 10 --x 10 --y 10`.

Ist der Inhalt zu groß, bricht das Skript mit der Textstelle, den betroffenen
Koordinaten und dem erlaubten Bereich ab. Bei Eingabe- und Prüfungsfehlern
werden keine Ausgaben geschrieben und bestehende Ausgaben bleiben erhalten.
Es gibt keinen automatischen Zeilenumbruch oder automatische Verkleinerung.
Passe in diesem Fall `--scale`, Zeilenumbrüche, Position oder Papierformat an.

Beispiel für A5 quer mit eigenen Geschwindigkeiten:

```bash
python3 text_to_gcode.py examples/text.txt \
  -o examples/text_a5.gcode --paper a5 --orientation landscape \
  --scale 0.4 --write-speed 1200 --travel-speed 6000
```

## Geschwindigkeiten, Abstände und Stift

**Beide Geschwindigkeiten werden in mm/min angegeben**: 600 mm/min entsprechen
10 mm/s, 6000 mm/min entsprechen 100 mm/s.

| Option | Standard | Wirkung |
| --- | --- | --- |
| `--write-speed` | 600 | Zeichnen, einschließlich Verbindungslinien |
| `--travel-speed` | 18000 | Fahren mit angehobenem Stift |
| `--character-spacing` | 1 mm | Zusätzlicher Abstand zwischen Zeichen |
| `--word-spacing` | 12 mm | Vorschub für jedes Leerzeichen |
| `--line-spacing` | 45 mm | Abstand zwischen Grundlinien |
| `--connection-distance` | 5 mm | Maximale Länge einer Verbindung |
| `--pen-up` | 750 | Ausgabe: `M3 S750` für Stift oben |
| `--pen-down` | 10 | Ausgabe: `M3 S10` für Stift unten |
| `--dwell` | 0.15 s | Wartezeit nach Anheben und Absenken |

Die Großbuchstaben sind nominal 30 mm hoch; Umlaute reichen darüber hinaus.
Skalierung wirkt auf Zeichen, Vorschub, Abstände und Verbindungsdistanz.
Papierformat, Innenrand, Position, Geschwindigkeiten und Wartezeiten werden
nicht skaliert. Koordinaten werden auf 0,0001 mm gerundet; eine Skalierung,
bei der ein Zeichenstrich vollständig verschwindet, wird abgewiesen.
Die Fahrbewegungen verwenden bewusst `G1` mit explizitem `F`,
damit die Fahrgeschwindigkeit einstellbar ist. Vorlagenvorschübe werden beim
Zusammensetzen ersetzt.

Benachbarte Buchstaben werden verbunden, wenn ihre Metadaten dies erlauben und
ihre Endpunkte nah genug beieinander liegen. Der Stift bleibt während des
Übergangs unten. Getrennte Hauptstriche innerhalb eines Zeichens bleiben
getrennt. Punkte, Umlautpunkte und Querstriche werden nach der Hauptschrift
eines Wortes einzeln ergänzt. Leerzeichen, Zeilenumbrüche und Satzzeichen
unterbrechen Verbindungen. Mit `--connection-distance 0` lassen sich zusätzliche
Verbindungslinien abschalten; freigegebene, exakt berührende Endpunkte können
weiter ohne Stiftwechsel gezeichnet werden.

`--preview pfad.svg` legt die Vorschau an einem eigenen Ort ab,
`--no-preview` unterdrückt sie. Standardmäßig erhält sie den Namen der
GCODE-Ausgabe mit `.svg` als Endung. Ausgabeverzeichnisse müssen bereits
existieren. Die Datei wird erst nach erfolgreicher Prüfung ersetzt.

## Alternative Handschrift Bard2

`handwritingfont/barde/` ist eine zweite Sammlung mit denselben 82 Zeichen wie
`handwritingfont/standard/`. Sie orientiert sich an der Bildvorlage **„Barde 2“**: rundere,
stärker nach rechts geneigte Schreibschrift mit geschlungenen Ober- und
Unterlängen sowie passenden Großbuchstaben. Sie enthält nur Zeichenstriche,
keine Verzierungen. Die Vorlage wurde als einlinige Plotterschrift nachgezeichnet;
im Bild fehlende Zeichen sind im gleichen Stil ergänzt. Strichstärken aus dem
Rasterbild werden nicht durch doppelte Konturen nachgebildet; die Strichbreite
bestimmt der verwendete Stift.

Die ursprünglichen Dateien in `handwritingfont/standard/` bleiben unverändert und sind
weiter die Standardschrift. Wähle Bard2 ausdrücklich mit
`--font handwritingfont/barde`:

```bash
python3 text_to_gcode.py examples/bard2.txt \
  --font handwritingfont/barde --output examples/bard2.gcode \
  --paper a4 --scale 0.28 --x 15 --y 230 \
  --write-speed 600 --travel-speed 6000
```

Die fertige Probe liegt in `examples/bard2.gcode`, die Vorschau in
`examples/bard2.svg` und `examples/bard2.png`. Sie enthält den Text aus dem
Bild und die Buchstabenpaare Aa bis Hh, oben auf A4. Für eigenen Text ersetze
`examples/bard2.txt` und verwende einen neuen Ausgabenamen. Position und
Skalierung lassen sich mit den vorhandenen Optionen anpassen.

Die [schnelle Bard2-Probe](examples/bard2_schnell.md) liegt in
`examples/bard2_schnell.gcode`, mit SVG- und PNG-Vorschau. Sie nutzt A4 quer
und F36000 (600 mm/s) beim Schreiben sowie F18000 (300 mm/s) für Fahrten.
Das sind Sollvorschübe; die tatsächlich erreichbare Geschwindigkeit hängt
von Kurven, Beschleunigung und Firmware ab.

Eine getrennte Zeichenprobe mit allen unterstützten Zeichen liegt in
`examples/bard2_alphabet.gcode` und `examples/bard2_alphabet.svg`:

```bash
python3 text_to_gcode.py examples/bard2_alphabet.txt \
  --font handwritingfont/barde --output examples/bard2_alphabet.gcode \
  --paper a4 --scale 0.36 --x 15 --y 130 \
  --write-speed 600 --travel-speed 6000
```

Nur die alternative Sammlung regenerieren:

```bash
python3 generate_bard2.py
```

`generate_bard2.py` nutzt standardmäßig `handwritingfont/barde/`. Mit `--output`
ist ein eigenes Verzeichnis möglich; `handwritingfont/standard/` und seine Unterverzeichnisse
werden als Ziel abgewiesen. Die Zeichendefinitionen stehen im neuen Generator,
der vorhandene Generator `generate_handwriting.py` bleibt unverändert.

## Schreibschrift Tavernenbarde

`handwritingfont/tavernenbarde/` enthält 82 einzelne GCODE-Zeichen der verbundenen
Schreibschrift **„Tavernenbarde · lebhaft“**, stilistisch nach der
Barden-Liederbuch-Bildprobe nachgezeichnet. Wähle sie mit
`--font handwritingfont/tavernenbarde`. Im Schriftordner liegen auch die
[JPG-Satzprobe](handwritingfont/tavernenbarde/beispiel.jpg), der fertige Satz als
GCODE, die SVG-Vorschau und eine [Anleitung](handwritingfont/tavernenbarde/README.md).
Die Probe verwendet den Satz „Victor jagt zwölf Boxkämpfer quer über den großen
Sylter Deich.“ und wird aus den tatsächlichen Zeichenpfaden gerendert.

`python3 generate_tavernenbarde.py --example` regeneriert Schrift und Probe;
für die JPG-Ausgabe wird Pillow benötigt. Die vorhandenen Sammlungen bleiben
unverändert.

## Eigene Zeichenvorlagen

Mit `--font pfad/zur/sammlung` wird ein anderes Vorlagenverzeichnis verwendet.
Dieses enthält `manifest.json` und die zugeordneten GCODE-Dateien.
Die Dateinamen sind frei wählbar; die mitgelieferten Unicode-Namen wie
`U0041.gcode` für A und `U0061.gcode` für a vermeiden Konflikte zwischen
Groß- und Kleinschreibung auf unterschiedlichen Dateisystemen.

Minimales Manifest für `U0041.gcode` (A), die du aus
`handwritingfont/standard/` in ein eigenes Vorlagenverzeichnis kopieren kannst:

```json
{
  "version": 1,
  "pen_up": 750,
  "pen_down": 10,
  "glyphs": {
    "A": {
      "file": "U0041.gcode",
      "advance": 20,
      "origin_x": 5,
      "baseline": 5
    }
  }
}
```

`advance` ist der horizontale Vorschub in unskalierten Millimetern, zusätzlich
zum Zeichenabstand. `origin_x` und `baseline` legen den Zeichenursprung in den
Koordinaten der Vorlagendatei fest; beide sind standardmäßig 0. Gemeinsame
Grundlinien müssen in allen Zeichen korrekt eingetragen sein, damit etwa
Unterlängen und unterschiedlich hohe Buchstaben erhalten bleiben.

Optionale Metadaten:

```json
{
  "main_strokes": [0],
  "connect_in": true,
  "connect_out": true
}
```

Die Striche werden ab 0 in ihrer Dateireihenfolge gezählt. `main_strokes`
enthält die aufsteigenden Indizes der Hauptstriche. Alle übrigen Striche werden
als Nebenstriche später ergänzt. Ohne diese Angabe bleiben alle Striche in
ihrer Reihenfolge Hauptstriche. Verbindungserlaubnisse sind standardmäßig
`false`; aktiviere sie nur für geeignete Ein- und Ausläufe.

Die Stiftwerte im Manifest beschreiben die **Eingabedateien**. Die CLI-Optionen
`--pen-up` und `--pen-down` betreffen ausschließlich die **Ausgabe**.

Unterstützter Vorlagen-GCODE:

- `G21`, `G90`, optional `G94`; die erste XY-Bewegung benötigt `G21` und `G90`.
- Lineare Bewegungen `G0/G1`, auch ohne wiederholtes G-Wort und mit nur einer
  Achse, sobald die erste vollständige XY-Position bekannt ist.
- `M3 S…` in eigener Zeile, `G4 P…`, positive Vorschübe `F…`, Zeilennummern
  und Kommentare in Klammern oder ab Semikolon.

Vor dem ersten Zeichenstrich muss eine Position mit angehobenem Stift bekannt
sein. Kreisbögen, relative Koordinaten, Z-Bewegungen, Homing und andere
Maschinenbefehle werden abgewiesen. Inkscape-Exporte mit Z-Steuerung sind daher keine passenden Vorlagen.
Es werden ausschließlich die erkannten Zeichenstriche übernommen, keine
beliebigen Kopf- oder Endbefehle aus den Quelldateien.

## Sammlung regenerieren und prüfen

Die Standardschrift lässt sich ohne Zusatzpakete regenerieren:

```bash
python3 generate_handwriting.py
```

Für die vollständige Testsammlung die optionalen Schrift-Abhängigkeiten
installieren. Die Tests benötigen keinen angeschlossenen Plotter:

```bash
python3 -m pip install -r requirements-font.txt
python3 -m unittest discover -s tests -v
```

Der Generator überschreibt die von ihm erzeugten Dateien in `handwritingfont/standard/`.
Für eigene Anpassungen nutze eine separate Sammlung oder ändere die
Zeichendefinitionen im Generator. Mit `--output anderes_verzeichnis` erzeugt
er eine separate Sammlung.

Die Tests prüfen unter anderem alle Zeichen, reproduzierbare Vorlagen,
Schriftverbindungen, Nebenstriche, Skalierung, getrennte Vorschübe, A4/A5 in
beiden Ausrichtungen, eigene Rahmen, Grenzfälle und unveränderte Ausgaben bei
Eingabefehlern.

## Am Plotter

Der [USB-Sender](USB_Sender.md) `send_gcode_sync.py` wartet standardmäßig nach
jedem Befehl auf `ok` und bricht bei Controllerfehlern ab. Mit `--buffered`
überträgt er aufeinanderfolgende Fahrbefehle gepuffert. Weitere Funktionen
sind `--simplify` zur Pfadvereinfachung, `--jog` zum Ausrichten und
`--typewriter` zum direkten Schreiben über die Tastatur.

Eine Datei ohne USB-Verbindung prüfen:

```bash
python3 send_gcode_sync.py examples/text.gcode --check
```

Ports auflisten und anschließend mit dem tatsächlich angezeigten Port senden:

```bash
python3 send_gcode_sync.py --list-ports
python3 send_gcode_sync.py examples/text.gcode --port /dev/ttyUSB0
```

Unter Windows kann der Port beispielsweise `COM3` heißen. Vor dem Senden andere
Programme schließen, die denselben Port verwenden. Details zu Startverhalten,
Nullpunkt und Bedienung stehen in der [Sender-Anleitung](USB_Sender.md).
**Im Schreibmaschinenmodus liegt der Arbeitsnullpunkt oben links**;
die folgende Beschreibung gilt für mit `text_to_gcode.py` erzeugte Dateien.

`text_to_gcode.py` erzeugt Dateien und sendet keine Befehle an die Maschine. Vor dem
Plotten den Arbeitsnullpunkt unten links am Papier setzen und Stiftsteuerung
und Vorschübe passend zur Maschine wählen. GCODE verwendet absolute
Millimeterkoordinaten und startet und endet mit angehobenem Stift; es enthält
kein Homing und setzt keinen Nullpunkt.

Die tatsächliche Maschinenposition vor der ersten Fahrt ist dem Skript
unbekannt. Die Rahmenprüfung deckt alle erzeugten Zielpunkte und die Wege
zwischen ihnen ab. Die initiale Fahrt ist ebenfalls innerhalb des Rahmens,
wenn die Maschine bereits innerhalb des nutzbaren Rahmens steht. Die
eingestellten Geschwindigkeiten sind Sollwerte, deren erreichbare Werte von
der Maschine und ihren Beschleunigungsgrenzen abhängen.


## Projektstruktur

| Pfad | Inhalt |
| --- | --- |
| `text_to_gcode.py` | Textlayout, Vorlagenprüfung, G-Code- und SVG-Ausgabe |
| `send_gcode_sync.py` | USB-Sender und interaktive Bedienung |
| `typewriter.py` | Planung einzelner Zeichen im Schreibmaschinenmodus |
| `generate_*.py` | Generatoren für die Zeichensammlungen und Schriftproben |
| `handwritingfont/` | Zeichenvorlagen, Metadaten und Schriftproben |
| `examples/` | Beispieltexte, Plotterdateien und Vorschauen |
| `tests/` | Automatisierte Tests |
| `3D/` | CAD-Dateien, Druckmodelle und Elektronikdateien |
| [USB_Sender.md](USB_Sender.md) | Ausführliche Anleitung zur USB-Übertragung |


