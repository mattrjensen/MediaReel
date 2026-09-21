# Development

## Run from source

```bat
D:
cd D:\Documents\Work\Projects\MediaReel
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

### Metadata batching diagnostics (real folder)

Standalone scripts, not collected by pytest, used to develop and verify
batched metadata reading. Each takes a folder path; use a large mixed one
(2000+ files, photos and videos) to see the differences.

```bat
python tests/diagnostic_batch_baseline.py "D:\path\to\folder"
python tests/diagnostic_batch_diff.py "D:\path\to\folder" [original_metadata_reader.py]
python tests/diagnostic_duration_tag.py "D:\path\to\folder"
python tests/diagnostic_shard_timing.py "D:\path\to\folder" 4 8 16
```

- **`diagnostic_batch_baseline.py`** — times metadata reading, thumbnail
  generation, and a full `load_folder()` separately.
- **`diagnostic_batch_diff.py`** — compares the original one-process-per-file
  `read_metadata()` against `read_metadata_batch()` for every file (dates,
  sources, proposed filenames). It fetches the *original* `metadata_reader.py`
  from git itself (`ORIGINAL_COMMIT` at the top of the script, the last
  commit before batching), or takes a path to a copy as a second argument.
  Files whose metadata contains non-ASCII text are expected to differ in
  `date_source` only (`filename` → `metadata`): the original decoded
  exiftool output as cp1252 and silently lost the metadata date for them.
  Reading the original per file is slow, so use a small folder unless you
  want to wait.
- **`diagnostic_duration_tag.py`** — shows which exiftool tag matches the old
  `-Duration# -s3` output, so duration can be read in the same call as dates.
- **`diagnostic_shard_timing.py`** — times metadata reading across N
  concurrent exiftool processes.

If a run is much slower than expected, check for chunks raising and falling
back to one exiftool launch per file (see "exiftool output must be decoded
as UTF-8" in `CLAUDE.md`) before looking at scheduling.
