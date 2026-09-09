# Vogelspoor

De GPS van je track, in je foto's. Voor camera's zonder (werkende) GPS:
match de EXIF-opnametijd van elke foto met een GPX-track (horloge, telefoon,
fietscomputer — maakt niet uit) en schrijf de geïnterpoleerde coördinaten in
de foto's. Handig voor
waarneming.nl / eBird — die lezen de locatie dan vanzelf uit de foto.

## Gebruik

1. Exporteer de GPX van je wandeling (Garmin Connect → activiteit → ⚙ →
   "Exporteren naar GPX", of Strava, Suunto, …).
2. Open Vogelspoor, sleep de GPX en je foto's (of een hele map) het venster in.
3. Controleer op de kaart of de foto's op logische plekken vallen. Klopt de
   cameraklok niet? Gebruik de tijdcorrectie — de app stelt zelf een correctie
   voor als de foto's er precies een aantal uur naast zitten (zomertijd!).
4. Foto's buiten de track (bijv. na het stoppen van de opname)? Vink ze aan
   in de lijst (shift-klik selecteert een reeks), klik "Prik op de kaart" en
   klik de plek aan. De pin is naderhand te verslepen om te verfijnen.
   Stond de cameradatum verkeerd (dagje ernaast)? Selecteer de foto's en kies
   "Tijd aanpassen" — dat corrigeert de EXIF-tijd blijvend in de bestanden.
5. Klik "Schrijf GPS naar foto's". Standaard worden originelen eerst gekopieerd
   naar een map `vogelspoor-backup` naast de foto's (uit te zetten met het
   vinkje).

Werkt met JPEG én RAW (ARW, CR2/CR3, NEF, ORF, RAF, RW2, DNG, …) dankzij
[ExifTool](https://exiftool.org).

### Hoe de tijden gematcht worden

- GPX-tijden zijn UTC (GPS-tijd, altijd exact).
- De EXIF-tijd van de camera is lokale tijd. Staat er een
  `OffsetTimeOriginal` in de EXIF, dan gebruiken we die; anders nemen we de
  tijdzone van deze computer aan.
- Tussen twee trackpunten wordt lineair geïnterpoleerd; over gaten > 15 min
  wordt niet geïnterpoleerd. Foto's tot 10 min buiten de track worden aan het
  dichtstbijzijnde punt geplakt ("rand van track").

## Ontwikkelen

```powershell
python -m venv .venv
.venv\Scripts\pip install pywebview pyinstaller
# ExifTool (Windows, 64-bit) van https://exiftool.org uitpakken naar
# vendor\exiftool-<versie>\ en exiftool(-k).exe hernoemen naar exiftool.exe
.venv\Scripts\python app.py
```

UI los bekijken (met nepdata, zonder Python): serveer `ui/` en open `dev.html`.

## Bouwen & delen

```powershell
.\build.ps1     # -> dist\Vogelspoor.exe (één bestand, ~45 MB)
```

De exe is alles wat je pa nodig heeft. Windows SmartScreen kan bij de eerste
keer waarschuwen (onbekende uitgever): "Meer informatie" → "Toch uitvoeren".

## Opbouw

- `geotag.py` — GPX parsen, tijd→positie-interpolatie, ExifTool-aanroepen
- `app.py` — pywebview-venster + API voor de UI
- `ui/` — HTML/CSS/JS (Leaflet-kaart lokaal meegeleverd; alleen de
  kaarttegels komen van internet)

## Licentie

MIT, zie [LICENSE](LICENSE). De meegeleverde ExifTool in `vendor/` valt onder
zijn eigen licentie (zie `vendor/exiftool-13.59_64/`).
