# Development

## Run from source

```bat
D:
cd D:\Documents\Projects\MediaReel
venv\Scripts\activate
python main.py
```

## Build

```bat
pyinstaller --onedir --windowed --name "MediaReel" --icon assets/icon.ico main.py
xcopy /E /I vendor dist\MediaReel\vendor
```

## Tests

### Automated tests (no files required)

```bat
python -m pytest tests/test_metadata_reader.py tests/test_recalculate.py -v
```

- **`test_metadata_reader.py`** — pure logic: filename date parsing, date stripping,
  filename construction. No Qt, no exiftool, no real files.
- **`test_recalculate.py`** — model logic: file state classification, interpolation,
  collision resolution, phase detection. Uses Qt but no real files.

### Interactive diagnostic (real folder)

Run against an actual event folder to verify end-to-end behaviour with real
EXIF/video metadata:

```bat
python tests/test_model.py "D:\path\to\event\folder"
```

For good coverage use a folder that contains a mix of:
- JPEGs/HEICs with EXIF dates (DSLR, iPhone photos)
- iOS and Android videos (`.mp4`, `.mov`) — tests UTC timezone handling
- Files with dates embedded in the filename (Signal, WhatsApp, screenshots)
- Files with no useful date (downloads, received files)
- Files already renamed with the `YYYYMMDD_HHMMSS_` prefix
