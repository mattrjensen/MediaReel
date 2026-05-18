# Media Reel

**Turn your photos and videos into a story**

**Assemble photos and videos from multiple people into a single chronological story.**

Media Reel is a desktop app for collating media files from an event — a party, a weekend trip, an afternoon — taken across multiple phones and cameras, and ordering them into a coherent chronological sequence.

It does this by renaming files with a prepended `YYYYMMDD_HHMMSS_` timestamp, making the filename the permanent, self-describing source of chronological truth — independent of any photo app, operating system, or platform. Even if a file is edited, cropped, or colour-corrected, it still sorts correctly, forever.

---

## Why

When multiple people photograph the same event, you end up with a pile of files from different devices, with different naming conventions, different metadata reliability, and no consistent ordering. Viewing them together is chaotic.

Media Reel brings them into a single timeline. Once ordered, you can compare competing captures of the same moment side by side — who got the better shot of the speeches, the cake, the dancing — and cull to the best photographic memory of the event.

The chronological filename is the output. No vendor lock-in, no database, no sidecar files. Any file manager, photo app, or operating system will sort them correctly.

---

## Features

- **Automatic metadata reading** — reads EXIF date from photos and `QuickTime:DateTimeOriginal` from iOS videos (correctly handling UTC offset), falls back to filename date parsing, then date modified
- **Smart sorting** — files sorted alphabetically on load; dated files get proposed renames immediately
- **Two-phase workflow** — rename dated files first to establish anchor points, then nudge undated files into position between them
- **Live filename preview** — see proposed new filenames before anything is written to disk
- **Interpolation** — undated files moved between dated anchors get evenly distributed timestamps
- **Hold-to-repeat** — hold Move up/down buttons for accelerating repeat, with a speed cap to prevent overshooting
- **Expand/compact view** — toggle between compact overview and expanded thumbnails for content-based ordering
- **Thumbnail click to open** — in expanded mode, click a thumbnail to open the file in its default app (Photos, video player)
- **Non-destructive** — nothing is written to disk until you click Apply rename
- **Video support** — first-frame thumbnails via ffmpeg, duration badges, correct iOS video timezone handling

### Supported file types
`.jpg` `.jpeg` `.png` `.heic` `.heif` `.mp4` `.mov` `.avi`

---

## How it works

### The filename is the source of truth

After renaming, a file like `IMG_4821.heic` becomes `20241215_184705_IMG_4821.heic`. The original filename is preserved. The date/time prefix makes it self-ordering in any context, permanently.

Files already named in this format are recognised and left unchanged.

### Five file states

| State | Condition | What happens |
|---|---|---|
| Hard anchor | Already named `YYYYMMDD_HHMMSS...` | No change |
| Strong anchor, not moved | Has metadata/filename date, untouched | Renamed with own date |
| Strong anchor, moved | Has metadata/filename date, repositioned by user | Renamed with interpolated date |
| Weak anchor, not moved | No reliable date, untouched | Flagged — no rename until positioned |
| Weak anchor, moved | No reliable date, repositioned by user | Renamed with interpolated date |

### Recommended workflow

1. Open a folder containing all media from the event
2. Apply rename to dated files first — this commits their timestamps as hard anchors
3. Nudge undated files (WhatsApp downloads, Facebook saves, etc.) into position between the anchors
4. Apply rename again — undated files get interpolated timestamps based on their position

---

## Installation

### Requirements
- Windows 10/11 (Mac support planned)
- Python 3.12+
- [exiftool](https://exiftool.org) — Windows executable (64-bit), placed in `vendor/`
- [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) — Windows executable, placed in `vendor/`

### Setup

```bash
git clone https://github.com/yourusername/mediareel.git
cd mediareel
python -m venv venv
venv\Scripts\activate
pip install PySide6 Pillow pyexiftool PyInstaller
```

Place `exiftool.exe` and its `exiftool_files/` folder in `vendor/`.
Place `ffmpeg.exe` in `vendor/`.

```bash
python main.py
```

### Build to .exe

```bash
# Coming soon — PyInstaller packaging
```

---

## Project structure

```
MediaReel/
    vendor/
        exiftool.exe
        exiftool_files/
        ffmpeg.exe
    metadata_reader.py    # metadata reading and filename logic
    media_model.py        # data model, file state logic, rename engine
    main.py               # UI — PySide6 main window, delegates, toolbar
    CLAUDE.md             # full design spec and decisions
    README.md             # this file
```

---

## Design decisions

Full design rationale, file state rules, colour coding, interpolation logic, and workflow spec are documented in [`CLAUDE.md`](CLAUDE.md).

Key decisions worth noting:

- **Filename over metadata** — metadata can be wrong (iOS video UTC offset), stripped by editing tools, or absent entirely. The filename is always there.
- **Non-destructive until Apply** — the staging/preview model means users can explore the ordering freely without risk.
- **Single session** — no database, no sidecar files, no save state. Open a folder, order it, apply, done. The renamed files are the output.
- **Interpolation, not guessing** — undated files get timestamps derived mathematically from their position between dated anchors, not from AI guessing or heuristics. The user's positioning decision is the input; the timestamp is the output.

---

## Status

MVP — actively used and tested on real event photo collections.

**Working:**
- Metadata reading (photos and iOS/Android video)
- Five-state file classification
- Live filename preview
- Apply rename with resort
- Thumbnail generation (photos and video via ffmpeg)
- Hold-to-repeat move buttons
- Expand/compact view toggle
- Thumbnail click to open in default app

**Planned:**
- PyInstaller `.exe` packaging
- Two-phase workflow UI (commit anchors modal)
- Padlock locking/unlocking per file
- Mac support
- Manual date/time editing per file
- AI-assisted ordering for undated files (vision API)
- Drag and drop reordering

---

## Background

Built as a personal tool and a portfolio case study in AI-assisted product design and development. The design process — from initial spec through UI mockups to working app — was conducted collaboratively with Claude (Anthropic).

The real purpose of the app only became clear through dogfooding: the chronological ordering isn't the end goal, it's the prerequisite for comparing competing captures of the same moment and culling to the best photographic memory of an event.

---

## License

MIT