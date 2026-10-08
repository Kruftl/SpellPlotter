# Tavernenbarde – lebhaft

Einlinige, verbundene Schreibschrift für Barden-Liederbücher. Die Formen sind
nach der Bildprobe „Tavernenbarde · lebhaft“ stilistisch nachgezeichnet, keine
pixelgenaue Vektorisierung. Im Bild fehlende Zeichen wurden im passenden Stil
ergänzt. Die Sammlung enthält 82 Zeichen:

`A–Z a–z ÄÖÜäöüß 0–9 . , ! ? : ; - – ‘ / © ( )`

Jedes Zeichen liegt als eigene Datei vor, benannt nach seinem Unicode-Wert:
`U0041.gcode` = A, `U0061.gcode` = a, `U00E4.gcode` = ä.
`manifest.json` enthält Zeichenursprünge, Vorschübe und Verbindungserlaubnisse.
Leerzeichen und Zeilenumbrüche übernimmt das Textskript ohne Zeichendatei.

Die Vorlagen nutzen Millimeter, absolute Koordinaten und die vorhandene
Stiftsteuerung: `M3 S750` oben, `M3 S10` unten. Schreibvorschub ist F600,
Fahrvorschub F18000, Wartezeit beim Stiftwechsel 0,3 Sekunden. Die nominale
Kleinbuchstabenhöhe ist 18 mm; die Größe des fertigen Textes bestimmt `--scale`.
Die Vorlagen sind für das Zusammensetzen mit `text_to_gcode.py` gedacht.
Unterlängen und Großbuchstabenschleifen können im Zeichenkoordinatensystem
negative Koordinaten haben; das Textskript positioniert und prüft den Textblock.

Kleinbuchstaben sind innerhalb eines Wortes verbunden. Umlautpunkte,
i-/j-Punkte und Querstriche werden nach der Hauptschrift ergänzt. Die
Großbuchstaben V, B, S und D der Satzprobe haben ebenfalls verbundene Ausläufe.

## Enthaltenes Beispiel

„Victor jagt zwölf Boxkämpfer quer über den großen Sylter Deich.“

- `beispiel.jpg`: 3200 × 340 Pixel, aus den tatsächlichen GCODE-Zeichenpfaden
  einschließlich Verbindungslinien gerendert.
- `beispiel.gcode`: fertiger Satz auf A4 quer, Maßstab 0,25.
- `beispiel.svg`: zugehörige Vorschau mit Papierrahmen.
- `beispiel.txt`: Textquelle.

Im Projektverzeichnis lässt sich die Satzprobe erneut zusammensetzen:

```bash
python3 text_to_gcode.py handwritingfont/tavernenbarde/beispiel.txt \
  --font handwritingfont/tavernenbarde \
  -o handwritingfont/tavernenbarde/beispiel.gcode \
  --paper a4 --orientation landscape --scale 0.25 \
  --x 15 --y 100 --character-spacing 0.5 --travel-speed 6000
```

Zum Regenerieren aller Zeichen und der JPG-Probe:

```bash
python3 generate_tavernenbarde.py --example
```

Nur die JPG-Erstellung benötigt Pillow; die Zeichengenerierung ohne
`--example` und das Textskript benötigen keine zusätzlichen Python-Pakete.
