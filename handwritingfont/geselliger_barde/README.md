# geselliger Barde

Gut lesbare, lockere Schreibschrift nach Variante 2 „Der gesellige Tavernenbarde“.
Die Buchstaben wurden stilistisch als Einlinien-Pfade nachgezeichnet; die
Bildvorlage ist keine pixelgenaue Vektorvorlage. Fehlende Zeichen wurden passend
ergänzt. Der Satz enthält 113 Zeichen: alle druckbaren ASCII-Zeichen einschließlich
Leerzeichen sowie `ÄÖÜäöüßẞ “ ” „ ‘ ’ – — … · ©`.

## Dateien

- **geselliger Barde.ttf**: installierbarer TrueType-Font, Schriftfamilie
  „geselliger Barde“, Schnitt Regular. Dünne geschlossene Konturen wurden um
  dieselben Mittellinien gelegt, die der Plotter schreibt. Kein externer Font
  wurde eingebettet. Kleinbuchstaben haben anschließende Ein- und Ausläufe.
- **UXXXX.gcode**: eine Vorlage je Zeichen nach Unicode, beispielsweise
  `U0041.gcode` für A und `U00E4.gcode` für ä.
- **manifest.json**: Vorschübe, Hauptstriche und Verbindungsinformationen für
  das vorhandene `text_to_gcode.py`.
- **strokes.json**: vollständiger Datensatz mit Mittellinien-Koordinaten in mm,
  Zeichenmetadaten und Vorschüben. 20 Font-Einheiten entsprechen 1 mm der Vorlage.
- **beispiel.txt / .gcode / .svg**: Satzprobe auf A4 quer, Maßstab 0,23.
- **vorschau.png**: aus dem echten Font und den echten Plotterpfaden gerendert.
- **geselliger_Barde.zip**: Font, Datensatz, Einzelzeichen, Dokumentation und Proben.

## Plotterdaten verwenden

Die Dateien sind **Zeichenvorlagen**, mit lokaler Grundlinie Y=0 und möglichen
negativen Koordinaten für Unterlängen. Das Textskript platziert und prüft den
Text auf dem Papier. Für einen Druck die zusammengesetzte Satzdatei verwenden.

Stiftsteuerung wie im Projekt: `M3 S750` oben, `M3 S10` unten; Millimeter,
absolute Koordinaten, Schreiben F600, Leerfahrt F18000, Stiftpause 0,3 s.
Nominale Kleinbuchstabenhöhe 18 mm; bei `--scale 0.23` etwa 4,14 mm.
Die Satzprobe verwendet für Leerfahrten F6000.

`U0020.gcode` hebt nur den Stift an und zeichnet nichts. Der Vorschub von 18 mm
steht in `strokes.json`. Leerzeichen und Zeilenumbrüche verwaltet das Textskript
selbst, deshalb steht das Leerzeichen nicht in `manifest.json`; Zeilenumbruch
ist ein Layoutbefehl und besitzt keine Zeichendatei.

Aus dem Projektverzeichnis:

```bash
python3 text_to_gcode.py handwritingfont/geselliger_barde/beispiel.txt \
  --font handwritingfont/geselliger_barde \
  -o examples/geselliger_barde.gcode \
  --paper a4 --orientation landscape --scale 0.23 \
  --x 15 --y 30 --character-spacing 0.4 --word-spacing 18 \
  --line-spacing 55 --travel-speed 6000
```

Die G-Code-Ausgabe verbindet passende Kleinbuchstaben innerhalb eines Wortes.
Punkte und Querstriche werden nach dem Wort ergänzt. Der TTF-Font enthält die
gleichen Zeichenformen; er bietet keine zufälligen Zeichenvarianten oder
kontextabhängigen Ligaturen. Schriftgröße und Laufweite beeinflussen die
Lesbarkeit, besonders bei Großbuchstabenfolgen.

## Erneut erzeugen

`generate_geselliger_barde.py` im Projekt enthält die editierbaren Kontrollpunkte.
Benötigt werden Python, fontTools und shapely; für PNG zusätzlich Pillow:

```bash
python3 generate_geselliger_barde.py --preview
```

Font und G-Code wurden softwareseitig geladen, gerendert und auf Zeichenumfang,
Vorschübe und Zusammensetzung geprüft. Ein physischer Plottertest steht aus.
