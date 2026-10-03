# Media Reel — Project Spec & Design Decisions

## What this app is
Media Reel is a desktop app (Python + PySide6) (currently just Windows) for assembling photos and videos from multiple people and devices into a single chronological sequence — telling the visual story of a shared experience.

The app works by renaming files with a prepended `YYYYMMDD_HHMMSS_` timestamp, making the filename the permanent, self-describing source of chronological truth — independent of any photo app, operating system, or platform. Even if a file is edited, cropped, or colour-corrected, it still sorts correctly forever.

The primary use case is post-event curation: once files are in order, you can compare competing captures of the same moment side by side and choose the best photo or video of each. The chronological sequence turns an ambiguous pile of files into a navigable story, ready to be culled into the best photographic memory of the event.

The app is intentionally single-session and non-destructive — no files are touched on disk until the user explicitly applies changes. The one exception is the per-row Delete button, which acts immediately (after a confirmation) but only ever moves the file to the Recycle Bin; see "File size and deleting a file".

## Tech stack
- Python 3.14
- PySide6 (UI framework)
- Pillow (image thumbnails, and the preview's screen-size photo decode)
- QtMultimedia (`QMediaPlayer`, `QVideoWidget`; video playback in the full-screen preview — ships with PySide6)
- pillow-heif (HEIC/HEIF thumbnail decoding — registers a Pillow opener;
  without it, `.heic`/`.heif` files silently get no thumbnail. Listed in
  `requirements.txt`. `media_model.py` imports it in a `try/except
  ImportError`: if it's missing, it emits a `RuntimeWarning`, sets
  `HEIF_AVAILABLE = False`, and `MainWindow` shows a one-time dialog when a
  loaded folder contains HEIC files)
- pyexiftool (metadata reading, shells out to vendor/exiftool.exe)
- PyInstaller (packaging to .exe)
- exiftool.exe is in `vendor/exiftool_files/` — the path is `vendor/exiftool.exe`
- ffmpeg.exe is in `vendor/` — the path is `vendor/ffmpeg.exe`

## Project structure
```
MediaReel/
    vendor/
        exiftool.exe
        exiftool_files/   ← required alongside exiftool.exe
        ffmpeg.exe
    metadata_reader.py    ← done and tested
    media_model.py        ← done
    main.py               ← done
    preview.py            ← full-screen preview window (photos + video)
    metadata_panel.py     ← full raw-metadata view (table's Info button + preview's Metadata panel)
    tests/                ← pytest tests (test_*.py) + standalone diagnostic_*.py scripts
    assets/               ← icons and images
    requirements.txt      ← pinned runtime dependencies (incl. pillow-heif)
    MediaReel.spec        ← PyInstaller spec
    CLAUDE.md             ← this file
    DEVELOPMENT.md        ← how to run, build, and test
    README.md             ← GitHub readme
```

## Supported file types
`.jpg`, `.jpeg`, `.png`, `.heic`, `.heif`, `.mp4`, `.mov`, `.m4v`, `.avi`

## Core design principle
The filename is the source of truth. The app is non-destructive until the user clicks Rename files. Everything before that is a preview. No files are touched on disk until Apply — except Delete, which is deliberately immediate rather than staged (after a confirmation, and into the Recycle Bin, so it stays recoverable). Files should be self-describing and self-ordering forever, independent of any app, platform, or cataloguing software.

---

## Date resolution — priority order on folder load

For each file, resolve its timestamp using this priority chain:

1. **Metadata** — EXIF `DateTimeOriginal`, then `CreateDate`, then `MediaCreateDate` (via exiftool). Most trusted source.
2. **Filename parsing** — if the filename contains a parseable date/time string (patterns: `YYYYMMDD_HHMMSS`, `YYYY-MM-DD-HHMMSS`, `YYYY-MM-DD_HH-MM-SS`, etc.)
3. **Date modified** — OS file modification timestamp. Unreliable (reflects download/copy time, not capture time). Treated as weak — same as no date for rename purposes.
4. **None** — no resolvable date.

A fifth tier, **manual**, doesn't come from this chain at all — it's set only by
the user, via the calendar icon in the Date taken cell (see "Per-file
date/time editing" under UI behaviour). It ranks as strong, above the automatic tiers in
trust even though it isn't checked as part of this priority order: the user
is knowingly overriding whatever this chain would have picked, typically
because it's wrong (a WhatsApp/shared file whose metadata reflects the share
date, not the capture date).

The `date_source` field records which tier was used: `metadata`, `filename`, `date modified`, `none`, `manual`.

**Strong date** = `date_source` is `metadata`, `filename`, or `manual`
**Weak/no date** = `date_source` is `date modified` or `none`

### iOS video timezone correction
iOS stores `QuickTime:CreateDate` in UTC, not local time. Two iOS-specific tags
contain the correct local time with timezone offset embedded — both must appear
at the top of the candidates list, above `QuickTime:CreateDate`:

1. **`QuickTime:CreationDate`** — used by iOS `.mov` files (e.g. `2026:07:04 14:48:46+12:00`).
   Shown as `Keys:CreationDate` in the exiftool CLI but returned as
   `QuickTime:CreationDate` by pyexiftool. Must be first in the candidates list.

2. **`QuickTime:DateTimeOriginal`** — used by iOS `.mp4` files (e.g. `2026:03:28 21:10:31+11:00`).
   Shown as `UserData:DateTimeOriginal` in the exiftool CLI but returned as
   `QuickTime:DateTimeOriginal` by pyexiftool.

A third tag covers videos that have been through an editor or export:

3. **`QuickTime:ContentCreateDate`** — e.g. a `.m4v` exported from iMovie
   (`2017:06:24 09:55:46+10:00`). Shown as `ItemList:ContentCreateDate` in the
   exiftool CLI, returned as `QuickTime:ContentCreateDate` by pyexiftool. On
   these files `QuickTime:CreateDate` (and the media/track create dates) hold
   the *export* date — one real file read `2018:06:08 12:57:46` there against a
   `ContentCreateDate` of `2017:06:24` — so without this tag the app would
   trust the export date and label it `metadata`. It sits after the two iOS
   tags and before the EXIF ones, and is simply absent on photos.

The `[:19]` truncation in the parser strips the timezone offset suffix cleanly,
giving correct local time. `QuickTime:CreateDate` remains in the list as a
fallback for non-iOS video files (Android stores local time there).

**Full candidates list order:**
```python
candidates = [
    'QuickTime:CreationDate',      # iOS .mov — local time with tz offset
    'QuickTime:DateTimeOriginal',  # iOS .mp4 — local time with tz offset
    'QuickTime:ContentCreateDate', # edited/exported video — original capture time
    'EXIF:DateTimeOriginal',
    'EXIF:CreateDate',
    'QuickTime:CreateDate',
    'QuickTime:MediaCreateDate',
    'XMP:DateTimeOriginal',
    'XMP:CreateDate',
]
```

---

## File state rules

Every file is classified into one of five states. These drive the rename logic, the preview column, and the colour coding. The gate between states is `user_moved` — whether the user has explicitly repositioned the file via the up/down controls.

### State definitions

| State | Condition | Rename? | Serves as interpolation anchor? |
|---|---|---|---|
| Hard anchor | `is_already_formatted=True` | Never | Yes |
| Strong anchor, not moved | Strong date, `user_moved=False` | Yes — own date, full seconds | Yes |
| Strong anchor, moved | Strong date, `user_moved=True` | Yes — interpolated date, full seconds | No |
| Weak anchor, not moved | Weak/no date, `user_moved=False` | Never | No |
| Weak anchor, moved | Weak/no date, `user_moved=True` | Yes — interpolated date, full seconds | No |

### Rename rules

- **Hard anchor** — filename already starts `YYYYMMDD_HHMMSS`. No rename, no proposed filename change. Already the source of truth — unless that date is itself wrong (e.g. WhatsApp/iOS share-date metadata), in which case picking a corrected date via the Date taken cell's calendar icon demotes it back to an ordinary strong anchor; see "Wrong-metadata hard anchors" under Per-file date/time editing.

- **Strong anchor, not moved** — propose rename using own metadata/filename date, full seconds. Becomes `YYYYMMDD_HHMMSS_<stripped_name>`. On Apply, rename only — do not update metadata.

- **Strong anchor, moved** — user has deliberately repositioned this file, overriding its timestamp. Propose rename using interpolated date between nearest anchors, full seconds. On Apply, if the move gave it a new date (re-anchored), rename AND write that date to file metadata — its own date was overridden because it was wrong for this position (e.g. an iOS-edited photo saved as a new file with the edit time), so leaving the old one in the metadata would keep it disagreeing with the filename. If its own date still fits its new position nothing is re-derived: it keeps its name and its metadata is left alone.

- **Weak anchor, not moved** — no rename proposed. Show original filename in grey. Flag as `needs_attention`. No change until user moves the file deliberately.

- **Weak anchor, moved** — user has deliberately positioned this file. Propose rename using interpolated date between nearest anchors, full seconds. On Apply, rename AND write new timestamp to file metadata.

### Interpolation rules

- **Anchor sources**: only hard anchors and unmoved strong anchors serve as interpolation anchor points. Moved files of any kind do not serve as anchors — their position is user-overridden and not trustworthy for deriving timestamps.
- **Even distribution**: N files between anchor A (datetime) and anchor B (datetime) get timestamps evenly distributed at 1/(N+1), 2/(N+1) ... N/(N+1) of the gap.
- **Extrapolation at list start**: if a group of weak/moved files sits before any anchor, extrapolate backward using the delta between the first two anchors below.
- **Extrapolation at list end**: if a group sits after all anchors, extrapolate forward using the delta between the last two anchors above.
- **No anchors at all**: file remains `needs_attention`, no proposed rename.
- **Collision handling**: if two files resolve to the same timestamp, append counter suffix before extension: `20241215_185400_01.jpg`, `20241215_185400_02.jpg`.

### Filename construction

Prepend the resolved timestamp to the original filename, stripping any parseable date string already embedded in the filename to avoid duplication:

- `IMG_4821.heic` → `20241215_184705_IMG_4821.heic`
- `signal-2024-12-15-190122.mov` → `20241215_190122_signal.mov` (date stripped)
- `received_img_882746.jpg` → `20241215_185423_received_img_882746.jpg` (interpolated)
- `20241215_183042.jpg` → unchanged (hard anchor)

---

## MediaFile dataclass fields

```python
filepath: str
filename: str                  # original filename on disk
ext: str                       # lowercase extension
is_video: bool
is_already_formatted: bool     # filename already starts with YYYYMMDD_HHMMSS
date: datetime | None          # resolved source date
date_source: str               # 'metadata' | 'filename' | 'date modified' | 'none' | 'manual'
stripped_filename: str         # filename with any embedded date string removed
proposed_filename: str         # AUTO-computed preview of new filename, from
                               # recalculate_proposed_filenames() only — never
                               # touched by a manual filename override. Equals
                               # filename if no change.
is_interpolated: bool          # date derived from neighbours (weak anchor, moved)
is_re_anchored: bool           # strong anchor moved out of chronological order
needs_attention: bool          # weak anchor, not yet moved into position
user_moved: bool               # True if user has explicitly repositioned this file;
                               # cleared to False on Apply
effective_date: datetime | None # the date this file will carry after rename;
                                # used by anchor-finding functions so they don't
                                # return stale source dates after recalculate
thumbnail: QPixmap | None      # loaded async after initial metadata
duration_seconds: int | None   # video only
size_bytes: int | None         # file size; None until the metadata worker has read it
                               # (shown blank meanwhile). Re-read after a rename that
                               # rewrites the file's metadata.
looks_like_uuid: bool          # original filename (checked against the stripped stem,
                               # so this survives a rename) is a bare UUID — advisory
                               # warning badge only, no effect on rename logic. See
                               # "Date taken column — source badge".
latitude: float | None         # GPS location in decimal degrees, from the same
longitude: float | None        # metadata read as the date; None/None if the file has
                               # no GPS tags. Informational only — the Info column's
                               # location icon and its tooltip. See "Location icon".
selected: bool                 # checkbox state
manual_filename: str | None    # user override from the editable New filename
                               # field. None = use proposed_filename; '' = skip
                               # this file on Apply; any other string = rename
                               # to exactly that. See display_filename and
                               # "Editable New filename column" under UI
                               # behaviour.
original_index: int            # this file's position the last time
                               # _sort_by_filename() ran (initial load, or the
                               # resort after Apply). The "undo move" baseline
                               # for a moved weak file's "x" — see
                               # MediaTableModel.undo_move().
manual_date_undo: tuple | None  # (date, date_source, is_already_formatted)
                               # from just before the first date-picker pick
                               # since the last Apply/load, or None if no
                               # pick has happened since then. The "undo a
                               # date correction" snapshot for a manually-
                               # dated file's "x" — see
                               # MediaTableModel.undo_manual_date().
```

`display_filename` (property, not a stored field) is what's actually shown and
renamed: `manual_filename` if set, else `proposed_filename`. Reading through
this property — not `proposed_filename` directly — is what lets the reset
("x") button be an O(1) field clear with no recompute: the auto value was
never overwritten in the first place.

`gates_phase1` (also a property) — "a strong anchor still waiting to be
renamed": `not is_already_formatted`, a strong `date_source` (`metadata` /
`filename` / `manual`), and `not user_moved`. Informational only (see "No
operational phases") — nothing in `main.py` uses it to gate anything.

`can_clear_filename` (another property) is the single rule for whether the
New filename field's "x" is shown: the box holds a real name (not blank, not a
`---` instruction placeholder). Painting and click handling both use it.

`name_locked` (another property) is `is_already_formatted and not user_moved`
— whether the New filename cell is empty and uneditable. It is *not* just
`is_already_formatted`: moving an already-named file makes Pass 1 treat it
like any moved strong anchor, which can propose a real (re-anchored) rename,
so it must get a visible, editable, skippable box like every other file with
a pending rename. Painting (`PreviewDelegate.paint`/`createEditor`), click
handling (`mousePressEvent`), the "Reset" tooltip and
`MediaTableModel.flags()` all read it, so they can't drift apart.
`apply_rename()` clears `user_moved` on every already-formatted file at the
end so the unlock doesn't outlive an Apply.

---

## recalculate_proposed_filenames — three-pass logic

Runs after every reorder. Resets `effective_date` for all files before starting.

### Pass 1 — classify each file

```
if is_already_formatted (hard anchor):
    proposed = filename (no change)
    effective_date = date parsed from filename prefix
    → serves as anchor

else if strong date AND not user_moved (strong anchor, not moved):
    proposed = build_new_filename(filename, date, is_interpolated=False)
    effective_date = date
    → serves as anchor

else if strong date AND user_moved (strong anchor, moved):
    if own date is still in chronological order with neighbours:
        proposed = build_new_filename(filename, date, is_interpolated=False)
        effective_date = date
        is_re_anchored = False
    else:
        dt = average of nearest anchors before and after
        proposed = build_new_filename(filename, dt, is_interpolated=True, force=True)
        effective_date = dt
        is_re_anchored = True
    → does NOT serve as anchor

else (weak anchor — date_source is 'date modified' or 'none'):
    if user_moved:
        is_interpolated = True   ← resolved in Pass 2
    else:
        needs_attention = True
        proposed = '— nudge into position →'
    → does NOT serve as anchor
```

### Pass 2 — group interpolation

Walk the list finding contiguous runs of `is_interpolated=True` files (NOT `needs_attention` files — those are left alone entirely). For each run, find the nearest anchor before and after, then assign evenly spaced timestamps. Unmoved weak anchors (`needs_attention=True`) are skipped in this pass.

### Pass 3 — collision resolution

If two files would get the same proposed filename, append `_01`, `_02` suffixes before the extension.

---

## Apply rename logic

*Naming: the toolbar button (and its confirmation dialog) is labelled
**"Rename files"**. This document and the code still call the operation
"Apply" / "Apply rename" (`apply_rename()`, `MainWindow._apply_rename`,
`_btn_apply`) — those are internal names, not something the user sees.*

| State | Rename | Update metadata |
|---|---|---|
| Hard anchor | skip | skip |
| Strong anchor, not moved | yes — prepend own date, full seconds | no |
| Strong anchor, moved (re-anchored) | yes — prepend averaged date, full seconds | yes — write new timestamp to file metadata |
| Weak anchor, moved (interpolated) | yes — prepend interpolated date, full seconds | yes — write new timestamp to file metadata |
| Weak anchor, not moved | skip | skip |
| Manually dated (`date_source == 'manual'`) | yes — prepend the entered date, full seconds | yes — write new timestamp to file metadata |

The manually-dated row isn't really a sixth state in the five-state sense —
`_is_strong()` already treats `'manual'` as strong, so such a file falls into
whichever of the first two rows its `user_moved` value puts it in. It's
listed separately because its metadata is written even when it isn't
re-anchored. In short, metadata is written whenever Apply gives a file a
date other than its own: the decision is
`f.is_interpolated or f.is_re_anchored or f.date_source == DATE_SOURCE_MANUAL`, captured in
`apply_rename()` before the immediately-following filename-prefix parse can
overwrite `date_source` to `'filename'`. Rename destination and target for
that decision are always `f.display_filename` (the manual filename override
if one is set, else `f.proposed_filename`), not `f.proposed_filename`
directly — see "Editable New filename column" under UI behaviour.

After renaming, for each renamed file:
- Update `f.date` from the new filename prefix
- Set `f.date_source = 'filename'`
- Set `f.is_already_formatted = True`
- Reset `f.user_moved = False`
- Reset `f.is_interpolated = False`, `f.is_re_anchored = False`
- Reset `f.manual_filename = None` — it's been applied, `f.filename` is now that value
- Update `f.effective_date = f.date`

Check file exists before renaming. Collect errors without stopping the batch. Emit `rename_complete(success_count, error_count)` when done.

### After Apply rename — resort
After apply_rename() completes (success or partial), the model should call
`_sort_by_filename()` followed by `recalculate_proposed_filenames()`. This
re-sorts the list so newly renamed files (now prefixed with `YYYYMMDD_HHMMSS`)
appear in correct alphabetical/chronological order alongside any other
already-formatted files. Remaining unmoved weak anchors stay flagged at the
end. This resort is triggered from `_on_rename_complete` in `main.py` —
immediately on a clean run, or after the error dialog is dismissed if any
file failed (see "Apply rename confirmation dialog" below for why a clean
run has no dialog of its own to wait for).
The vertical scroll position is preserved across all of this:
`_on_rename_complete` reads `verticalScrollBar().value()` before anything
else and sets it back after the resort and recalculate. (It used to
`scrollToTop()` so newly renamed files were visible from the start of the
list, but that threw away wherever the user was working. Note this restores
the scrollbar *position*, not the *rows* under it — the resort can move
renamed files elsewhere, so the same offset may now show different files.)

### Apply rename confirmation dialog
One message, always:
{n} file(s) will be renamed.

Continue?

`{n}` counts files where `display_filename` differs from `filename` (not
`proposed_filename` — a manual override or a blanked/skipped box must count
the same way `apply_rename()` itself decides what to rename; see "Editable
New filename column"). Implementation: a single `QMessageBox` call in
`MainWindow._apply_rename()`.

**No dialog afterward on a clean run.** `_on_rename_complete` used to follow
up with a "Done — {n} file(s) renamed successfully" box; it's gone — the
table updating live (row count, New filename column, badges) already says
it worked, and clicking through a second confirmation that only restates
that added a step without new information. A run with failures still gets
`QMessageBox.warning` with the success/error counts, since a silently
failed rename (e.g. a file open in another program) is exactly the kind of
thing that needs surfacing, not something already visible elsewhere.

---

## Colour coding

### New filename (preview) column — text colour

| Colour | Meaning |
|---|---|
| (empty) | Hard anchor — nothing will change, so nothing is shown |
| Grey | No change will happen, or placeholder instruction text |
| Amber | Will be renamed — any file getting a new name (own date, re-anchored, or interpolated) |

Placeholder instruction text (starting with `'---'`) is always grey — it is a
status instruction, not a proposed rename.

### Date taken column — source badge

| Badge | Colour | Meaning |
|---|---|---|
| `metadata` | Green | Date from EXIF/video metadata — most trusted |
| `filename` | Grey | Date parsed from filename string |
| `date modified` | Amber | OS modification timestamp — unreliable, treat as weak |
| `interpolated` | Orange | Date derived from neighbours |
| `none` | Red | No date found |
| `manual` | Purple | User-entered date (calendar icon) |

**UUID-filename warning badge.** A small amber circle with "!", right after
the source badge (`DateDelegate._warning_badge_rect`, positioned off
`_source_badge_rect` so the two can't drift apart), shown when
`MediaFile.looks_like_uuid` is set — the file's name (its original name;
see below) is a bare UUID like `5c4ec94a-0ccb-465f-bb89-99dde3e458a7`, the
kind of name some sync/share pipelines (seen on WhatsApp/iOS-shared video)
substitute for the real filename. Hovering it shows a tooltip: "Auto-
generated filename — metadata may reflect when this file was shared or
exported, not when it was captured" (`MediaTableView.viewportEvent`, same
pattern as the New filename column's "Reset" tooltip). **Purely advisory —
a nudge to double-check the date via the calendar icon, nothing more: it's
not a `date_source`, and has no effect on the five-state rename logic.**

Detected once at load (`metadata_reader.is_uuid_filename`), from the
*stripped* stem — `stripped_filename` already has any `YYYYMMDD_HHMMSS_`
prefix removed, so a hard anchor already renamed from a UUID name still
gets flagged; the point is a warning that survives the rename within the
session, not just a property of today's on-disk name. Stored as a
persistent `MediaFile.looks_like_uuid` field (not recomputed), the same
reason `original_index`/`manual_date_undo` are stamped once rather than
derived live.

### Row background

| Row type | Background |
|---|---|
| `needs_attention` (weak, unmoved, no value) | Light amber (`#FEF3C7`) / very light amber (`#FFFBEB`) alternating |
| Everything else (hard anchor, strong anchor, moved weak anchor) | White / faint grey alternating (normal) |
| Selected | 1px blue border (`#2563EB`) around row — background colour unchanged |

Amber row means exactly one thing: this file has no value in its New
filename box and hasn't been positioned yet — it's the only row state not
already self-explanatory from the box's own contents/colour (a filled box
already shows, via its text colour, whether it'll be renamed). There's no
separate "not actionable yet" row state — see "No operational phases" below
for why.

---

## No operational phases
Earlier versions of this app gated the whole table behind two sequential
phases — Move disabled and most colour coding suppressed until every strong
anchor was renamed, only unlocking weak-file positioning afterwards. That's
gone. Every row is independently actionable at all times: the New filename
box always reflects what will happen on the next Apply, "x" always clears it
to blank (skip), and Move is never disabled.

**What phases were protecting against, and why it's an acceptable trade to
drop:** a strong anchor's own metadata is trustworthy, but only for that
file's *own* date — if the user drags it to a new position before it's
renamed and that position turns out chronologically inconsistent, Pass 1
re-anchors it (averages its neighbours) instead of trusting its own date,
exactly the same as any other moved strong anchor already does. Phase 1
existed to prevent this happening *by accident*, before the user had a
chance to lock the trustworthy date in via Apply. But nothing touches disk
until Apply, the proposed filename updates live as the file moves, and the
badge visibly flips from green "metadata" to orange "interpolated" if it
happens — a low-stakes, fully visible, easily-undone (just drag it back)
mistake, not one that justifies disabling Move for the whole table.

**`MediaFile.gates_phase1` and `MediaTableModel.has_pending_strong_renames()`
still exist**, but purely as informational counts (e.g. for the standalone
diagnostic script in `tests/test_model.py`) — nothing in `main.py` reads
them any more. `MoveDelegate`, `DateDelegate` and the row-background logic
in `BaseDelegate._draw_bg` / `MediaTableModel.data()` no longer take a phase
into account at all; the only per-row distinction left is `needs_attention`.

---

## Table columns (in order)

| # | Column | Notes |
|---|---|---|
| 0 | Checkbox | Selection |
| 1 | # | 1-based row order, always reflects current staged order |
| 2 | Filename | Original filename on disk |
| 3 | Info | An info-circle button that opens the file's full raw metadata, and, when the file has GPS data, a blue location-pin icon beside it (hover for coordinates). See "Viewing a file's full metadata" and "Location icon". |
| 4 | Date taken | Source badge (top) + formatted datetime (below), with a calendar icon at the right — on every row, including hard anchors, since a renamed-from-wrong-metadata file needs a way back — that opens a date-and-time picker popup. Nothing else in the cell is clickable for editing. |
| 5 | Size | Size in MB to one decimal place (`format_file_size`), right-aligned in a narrow column. A small non-empty file reads `<0.1 MB` rather than a misleading `0.0 MB`; blank until read. See "File size and deleting a file". |
| 6 | New filename (preview) | Grey = no change or placeholder instruction. Amber = will be renamed. Painted as a ~40px input box; a single click anywhere in it starts editing. Empty (no box, no text) on unmoved hard anchors — nothing will change, so there's nothing to show. Shows a clear ("x") whenever the box holds a real name; clicking it always blanks the box, which always means skip this file on Apply. |
| 7 | Preview | Thumbnail. Videos show first frame + duration badge. |
| 8 | Move | Up/down chevron buttons — routes through MainWindow._move() via Signal |
| 9 | Delete | Trash-can button that deletes that row's file (after a confirmation). See "File size and deleting a file". |

---

## UI behaviour

### Toolbar
Left to right (separators between the groups). **Every control is always on the
toolbar; the ones that act on loaded files are just greyed out until there's
something for them to act on** — no folder yet, nothing flagged, nothing
selected — rather than appearing and disappearing, which would shift the rest
of the row around. On first launch that means Reload, Expand View, Prev,
Next, Move up, Move down and Rename files are all visible but disabled; only
Open folder is live.

- **Open folder** — opens file picker, loads folder, shows spinner overlay while metadata reads. Sets the window title to `Media Reel — {full folder path}` (not just the folder name) — the window title bar is the one place a path this long doesn't crowd anything else out, and it's useful to be able to see exactly which folder (e.g. distinguishing two same-named event folders in different years) without reopening the file picker.
- **Reload this folder** — icon-only, next to Open folder: a clockwise circular-arrow icon (`_refresh_icon()`), drawn with `QPainter` like the calendar/trash icons rather than a Unicode glyph such as `↻` — one was tried and rendered as a missing-glyph box on a real machine, exactly the failure those other icons were already built to avoid. Built with an explicit `QIcon.Disabled` pixmap in `#9CA3AF` (the same grey `_btn_style()`'s disabled text uses), not left to Qt's own graying, which barely dimmed it. Re-runs `load_folder()` on whatever folder path `_open_folder` last recorded (`MainWindow._current_folder`), so new files on disk show up and ones removed outside the app disappear. Discards every bit of in-memory staging (moves, manual dates/filenames, selection) exactly the way opening a *different* folder already does, silently — no confirmation, for the same reason (deliberate: a user may well click Reload specifically to discard staged edits, same as a browser refresh). The one thing it does carry over is scroll position — unlike opening a different folder, it's still the folder you were looking at, so `_reload_folder()` records `verticalScrollBar().value()` into `MainWindow._pending_scroll_restore` before calling `load_folder()`, and `_on_load_complete()` restores it (clamped automatically if the reload left fewer rows) and clears the field. `_open_folder()` never sets that field, so opening a genuinely different folder is unaffected and still starts at the top. Disabled alongside Expand View until a folder with at least one file is loaded.
- **⊞ Expand View / ⊟ Compact View** — toggles between compact (default, 68px rows, 80x60 thumbnails) and expanded (140px rows, 160x120 thumbnails) row height mode. Useful when nudging undated files into position by image content. Disabled until a folder with at least one file is loaded (`_refresh_status` / `_on_load_started`). Double-clicking a thumbnail (either mode) opens the full-screen preview — see "Full-screen preview".
- **`⚠ {n} file(s) need ordering`** + **Prev** / **Next** — a status *label* (deliberately not a button, so no border/background) with two buttons beside it. The label is amber with a ⚠ while any file has `needs_attention=True`, and plain grey `0 file(s) need ordering` otherwise — including before a folder is loaded (`_refresh_attention_controls` swaps the style only when that state changes, since `setStyleSheet` is slow-ish). Prev/Next step backward/forward through the flagged files (`MainWindow._step_attention`), scrolling the row to the centre and making it current. They work from the table's *current row* rather than a remembered position in the flagged list — flagged files move and stop being flagged as you work on them, which would leave a stored position pointing at the wrong file. They **don't wrap**: past the last flagged file Next is greyed out, and before the first Prev is (`_attention_target` returns `None` there, and `_refresh_attention_controls` disables the button rather than leaving it to silently do nothing). Because that depends on the current row as well as the flagged set, `_refresh_attention_controls` also runs on the selection model's `currentChanged`, not just from `_refresh_status`. With no current row, both are enabled — Next goes to the first flagged file and Prev to the last. With nothing flagged there's nowhere to go, so both are disabled. (Making the row current also selects it, collapsing any multi-selection to that row.)
- **`{n} selected`** + **▲ Move up** / **▼ Move down** — the Move buttons act on all selected rows as a group, maintaining relative order within the group; selection follows the moved rows. The count before them says how many rows a click is about to move. It's the compact form of the status bar's "file(s) selected" because the toolbar is nearly full. Set from `_refresh_status`; reads `0 selected` before a folder has loaded and at the start of a load. Up comes before Down.
- **`{n} file(s) to be renamed.`** — grey text just left of Rename files; empty when nothing's pending. (Also in the status bar, below.)
- **Rename files** (`✓  Rename files`; was "Apply rename") — enabled as soon as any file has a pending rename. Warns if any files still need attention. Confirms before proceeding.

**Toolbar width:** the toolbar needs ~1220px (the "need ordering" controls are always showing) with typical counts (~1280px with 4-digit counts everywhere — measured: fits from 1280px, overflows at 1240px and below). A `QToolBar` that runs out of room doesn't stop the window shrinking — it pushes its *last* items, i.e. Rename files, into a » overflow menu — so the window's minimum width is 1200 (it was 1100 before the Move buttons and selection count joined this row, 1200 before the Reload button added ~40px) and the default is 1280. Only a narrow window *and* 4-digit counts in all three texts at once can still overflow — the same accepted edge case as before, just ~40px further out since Reload joined the row; it wasn't considered worth raising the minimum width for.

### Status bar
Left to right: `{n} files`, `{y} file(s) to be renamed`, `{z} file(s) need
ordering`, `{x} file(s) selected` — always all four once a folder is
loaded, **including zeros** (unlike the toolbar's copy of the rename count
and the attention button, which only appear when there's something to act
on). Each after the first carries its own leading `  ·  ` separator in its
label text. All are set from `MainWindow._refresh_status()`, and blanked in
`_on_load_started` so a previous folder's counts don't linger during a load.

`{y}` counts files whose `display_filename` differs from `filename` (same
rule as the Apply dialog and the toolbar text). `{z}` is the same
`needs_attention` count as the toolbar button and uses the same phrase, so
the two always read alike.

### Row height toggle state
- Default: `self._expanded = False`
- `ThumbnailWorker.THUMB_W = 160`, `ThumbnailWorker.THUMB_H = 120` — always load at full size
- Compact display: scale to 80x60 in delegate
- Expanded display: scale to 160x120 in delegate

### Move behaviour
- Toolbar buttons and per-row chevrons both route through `MainWindow._move(direction, clicked_row)`
- Selection is retained after move — selected rows follow their files to the new position
- `user_moved = True` is set on moved files in `move_rows()`

### Editable New filename column
`PreviewDelegate` paints the cell as a text-input box — fixed ~40px tall
(`_BOX_H`) and vertically centred whatever the row height, so it reads as a
normal input in both compact and expanded mode; `#93C5FD` blue border if a
manual override is active, `#E5E7EB` grey otherwise. It does not give every
row a real, live `QLineEdit`: with up to 2000 rows, that many always-alive
widgets would be a real performance cost, directly against everything the
batching work optimised for. A real `QLineEdit` is created only for the one
cell being edited (`createEditor`, sized to the painted box by
`updateEditorGeometry` so opening it doesn't jump).

**A single click anywhere in the box starts editing** — no double-click.
Qt's `editTriggers` can't express this per column (the Date taken column
needs different behaviour), so `MainWindow` sets `NoEditTriggers` and
`MediaTableView.mousePressEvent` routes every click on the two special
columns itself, calling `edit(index)` directly. Consequences: F2/Enter no
longer start an edit, and a click in this column doesn't do normal
row-selection handling.

- Unmoved hard-anchor rows (`MediaFile.name_locked`) are empty, not
  editable (`MediaTableModel.flags()` and `mousePressEvent` both check): the
  filename is already the source of truth, and allowing an edit would let a
  hard anchor get renamed, which "hard anchor never renames" forbids. (The
  ways back are the calendar icon, which demotes it out of hard-anchor
  status first — see "Wrong-metadata hard anchors" under Per-file date/time
  editing — and moving it, below.) **A hard anchor the user moves is
  unlocked**: it may be re-anchored to a new name, and that pending rename
  must be visible and skippable (the "x" blanks it = skip), never silent.
- Editing commits through `model.setData(index, text, Qt.EditRole)` →
  `MediaTableModel.set_manual_filename()`, which sets `f.manual_filename`
  and emits `dataChanged` for just that cell (`Qt.DisplayRole`) — this is
  what `MainWindow._on_model_data_changed` treats as a normal (not
  thumbnail-only) change, so `_refresh_status()` runs and the Apply button's
  enabled state updates. Nothing touches disk until Apply.
- The editor pre-selects just the filename stem (`Path(text).stem`) when it
  opens, Explorer-rename style. (This differs from a plain input, where a
  click places the caret; because `edit()` is called from the press event,
  the click position can't be forwarded to the editor anyway.)
- **The "x"** is shown whenever the box holds a real name
  (`MediaFile.can_clear_filename`) — not when it's blank (nothing to clear)
  or showing a `---` instruction placeholder (not a name). What clicking it
  does depends on *why* the box has a value (`MediaTableView.mousePressEvent`):
  - **A weak file that's been moved** (`is_interpolated` — its name only
    exists *because* it was moved) — calls `MediaTableModel.undo_move()`
    instead of blanking: puts the file back where it sat since the last
    Apply or load, without disturbing any other file's own move, and clears
    `user_moved`/`manual_filename` so it falls back to `needs_attention`
    (the placeholder) exactly as if it had never been moved. See "Undoing a
    weak file's move" below.
  - **A file whose date came from the picker** (`manual_date_undo` is set)
    — calls `MediaTableModel.undo_manual_date()` instead of blanking:
    blanking would leave a corrected date/badge with no proposed name to
    go with them, which isn't a useful state, so "x" backs the correction
    out entirely instead — date, date_source and (for a hard anchor)
    is_already_formatted all revert, and the file repositions back the
    same way a moved file does. See "Undoing a date-picker correction"
    below.
  - **Everything else** (strong anchors from metadata/filename, anything
    not covered above) sends `''` to the model
    (`setData(index, '', Qt.EditRole)` → `set_manual_filename()`), which
    means **skip this file on Apply** — `apply_rename()`'s pending filter,
    `has_pending_renames()`, and the status-bar/dialog counts in `main.py`
    (`_refresh_status`, `_apply_rename`) all read `display_filename`, which
    is blank once skipped. Emptying the field by hand in the editor does
    the same thing as clicking the "x" here.

  Paint (`PreviewDelegate.paint`) and hit-test (`MediaTableView.mousePressEvent`)
  share `can_clear_filename` and `_reset_icon_rect`, so an unpainted icon
  can't be clicked and the two can't drift apart.

  Blanking used to special-case a strong anchor still waiting to be renamed
  — blanking it would leave it stuck forever, since Move was disabled while
  any such file was pending and Apply skips a blank box, so there'd be no
  route left to resolve it. That's no longer true (see "No operational
  phases"): Move is never disabled, so the user can always reposition a
  skipped strong anchor to change its outcome, the same as any other file.
- **No way back from a blank** except typing a name: the "x" is hidden on an
  empty box, so it can't restore the suggested one. (For an unmoved weak
  file there's nothing to restore — the suggestion is just the placeholder.)
- Not handled: two files landing on the same `display_filename` (whether
  from a manual filename, a manual date, or a collision with an
  auto-computed name). Collision resolution (Pass 3) only runs over the
  auto `proposed_filename`. In practice this fails safe:
  `apply_rename()`'s per-file `try/except` means the second rename to an
  already-taken path raises, gets caught, and counts as one of the errors
  in the existing "N failed" dialog — not silent data loss, just not
  proactively prevented or explained as a collision.

#### Undoing a weak file's move
`MediaTableModel.undo_move(row)` puts a moved weak file back where it was
*since the last Apply or load* — not since the folder was first opened.
That baseline is `MediaFile.original_index`, stamped 0..N-1 by
`_sort_by_filename()` every time it runs, which is exactly the two moments
that should reset it: right after the initial load, and right after
`_on_rename_complete`'s resort. Nothing else touches it.

Relocating the file uses that baseline, not its absolute row position at
the time it was moved, and it only ever repositions *this* file — done by
the shared helper `_reinsert_at_original_index(row)` (also used by
`undo_manual_date`, below):
1. Remove it from the list.
2. Scan the remaining files for the first one that (a) hasn't itself been
   `user_moved` and (b) has a larger `original_index` — insert right
   before it (append at the end if none qualify). Scanning left to right
   and stopping at the first qualifying file is safe here specifically
   because unmoved files' *relative* order to each other never changes —
   nothing ever reorders two unmoved files against one another, only moves
   insert other files around them — so among unmoved files, list order and
   original_index order are always the same thing. (`_reposition_by_date`,
   used for a date-picker pick, can't rely on this same shortcut — see its
   own note on why it has to find the true nearest anchor instead of the
   first one encountered.)
3. Tell Qt about the move (`_remap_persistent_indices`, next) before the
   caller clears `user_moved`/`manual_filename` and calls
   `recalculate_proposed_filenames()` — with `user_moved` cleared and the
   file still weak, Pass 1 puts it back in `needs_attention` (the
   placeholder), exactly as if it had never been moved.

Step 2 deliberately **skips other moved files** when looking for the
insertion point, even though it doesn't touch them: a file some other move
has *also* displaced isn't sitting at its own original position any more,
so its current position isn't a trustworthy reference for where *this*
file used to be relative to it. Anchoring only on still-unmoved files is
what makes "restore this one file" well-defined independent of whatever
other reordering the user has done — the earlier design this replaced
would have needed to track and restore *everyone's* history to answer "put
it back," which is exactly the complexity this sidesteps.

**`_remap_persistent_indices(from_row, to_row)`** — bracketed by
`layoutAboutToBeChanged`/`layoutChanged` around the actual `pop`/`insert` in
`_reinsert_at_original_index` and `_reposition_by_date` — is what makes this
safe for an active selection. A bare `layoutChanged.emit()` after manually
mutating `self._files` tells the view "something changed, repaint," but
doesn't tell Qt's selection model *which rows moved where* — the
selection's own persistent indices keep referencing whatever row numbers
they used to, and after the reorder those numbers belong to different
files. In practice this surfaced as several unrelated files appearing
selected (blue border, checked box) after a single-row undo. The fix builds
an old-row → new-row mapping for every row shifted by the move, then calls
`self.changePersistentIndexList()` so every persistent index Qt is
tracking — selection, current index, an open editor — travels with its
item instead of being left behind pointing at a stale row number.
`move_rows()` doesn't need this: `main.py`'s `_move()` already reconstructs
selection manually after every call (block signals, move, re-select at the
new positions) as its own, independent safeguard, so a bare
`layoutChanged.emit()` there hasn't been a problem in practice — but this
proper remap is the more direct fix, not a special case that only some
reorders get.

Only reachable for `is_interpolated` files (weak + moved) — a moved strong
anchor's re-anchoring is the separate `is_re_anchored` flag and isn't
affected by this. `MediaTableView.mousePressEvent` calls it (instead of
blanking) when the "x" is clicked on such a file, then follows the file to
its new row (`scrollTo` + `setCurrentIndex`) since undoing a move can send
it a long way up the list.

#### Undoing a date-picker correction
A file whose date came from the picker can't be "undone" by just blanking
its name the way a dragged file can — picking a date overwrites real data
(`date`, `date_source`, and for a hard anchor `is_already_formatted` too),
and blanking the box wouldn't put any of that back. `MediaTableModel.
undo_manual_date(row)` does instead: it restores those three fields from
`MediaFile.manual_date_undo` and repositions the file with the same
`_reinsert_at_original_index` helper `undo_move` uses.

`manual_date_undo` is a `(date, date_source, is_already_formatted)`
snapshot taken by `set_manual_date`, but **only the first time** it's
called on a file since the last Apply or load (`if f.manual_date_undo is
None:` before overwriting it) — a second pick before the next Apply must
not overwrite the snapshot with the *first* pick's result, or "x" would
only ever undo one step instead of all the way back to how the file
actually started. `_sort_by_filename()` clears it for every file (whether
or not that particular file was touched), the same "since the last Apply"
checkpoint `original_index` resets on.

Deliberately doesn't require `user_moved` the way `undo_move` does:
`set_manual_date` leaves `user_moved = False`, specifically so a corrected
date still counts as a real anchor for its neighbours' interpolation
(`_find_anchor_before`/`_after` skip anything `user_moved`) rather than
being treated as suspect the way a dragged file is — setting `user_moved =
True` here to reuse the drag-undo path outright was considered and
rejected for exactly that reason.

The icon itself stays "x" for every state — it doesn't grow a second glyph
or swap its shape depending on what it's about to do. What it says is
covered by a tooltip instead: hovering it shows "Reset", via a
`QEvent.ToolTip` handler in `MediaTableView.viewportEvent()` (the icon has
no real widget of its own to hang a native tooltip off, so this is the
targeted equivalent — only the icon's own rect within the cell claims the
event; everywhere else in the box falls through to no tooltip).

### Per-file date/time editing
The Date taken cell shows a small calendar icon at its right edge (drawn with
`QPainter` primitives, `DateDelegate._draw_calendar_icon`, so it's a flat
single colour rather than a Unicode/emoji glyph). Clicking it opens
`DateTimePickerPopup` directly — **the cell never turns into an input field,
and double-clicking the date does nothing.** Any click in the cell outside
the icon is an ordinary row-selection click.

**Shown and clickable on every row, including hard anchors** — this is
deliberate, not an oversight: a hard anchor is only trustworthy because
renaming stamped it with a real date, but that date can itself be wrong
(WhatsApp/iOS-shared media whose metadata reflects the share date, not
capture) — see "Wrong-metadata hard anchors" below for what picking a date
there does.

`DateTimePickerPopup` (a `QDialog` with the `Qt.Popup` flag, so it closes on
any click outside it or on Escape) is a calendar with three clickable columns
beside it — Hour (00-23), Min and Sec (00-59), each a `QListWidget` where
you click the value you want (the mouse wheel scrolls them). It is
deliberately a *picker*, not a time field you type into: typing a time would
be no better than typing the filename. (`QDateTimeEdit`'s built-in calendar
popup is date-only, so it couldn't be used anyway.) A summary line under the
calendar shows exactly what "Set date and time" will apply. The row index in
each list *is* its value, so `currentRow()` reads it back directly.

It is pre-filled with `f.effective_date or f.date` — the value the cell
displays, or an unfinished draft for this same file if one exists (see
"Resuming an interrupted selection" below) — with the current hour/min/sec
scrolled into view (`show_near()` calls `_center_selected()` after `show()`,
since it needs the final size), and shows below the icon (above if there's
no room, never off-screen). "Set date and time" emits `committed(datetime)`
and applies the change; Cancel, Escape or clicking away applies nothing but
isn't a full discard either — see below. Selections (calendar day, list
rows) use the app's solid blue: the app palette's `Highlight` is a very pale
blue that made them nearly invisible.

#### Resuming an interrupted selection
`Qt.Popup` closes the picker on any click outside it, on Escape, and — less
obviously — on the whole *application* losing active focus (a notification
from another app, for instance), which used to silently discard whatever
was mid-selection with no way back. `DateTimePickerPopup.closeEvent()` now
catches every non-"Set date and time" close and, if the value actually
changed from what the popup opened with, emits `draft_changed(datetime)`
instead of just discarding it. The "actually changed" check matters: without
it, merely opening a file's picker to glance at it and closing again would
count as a draft too, and could silently evict a different file's real one
(see below) for nothing.

`MediaTableView._date_draft` holds at most one `(filepath, datetime)` pair —
deliberately just one slot, not a dict of every file ever opened. A draft
only matters while that one file is still mid-edit; opening the picker on a
*different* file has nothing to do with it and starts fresh from that
file's own current date, same as if no draft existed. Committing (or
opening a fresh, no-op popup and closing it again) clears the slot, so nothing
lingers once it's no longer relevant. This means only the single
most-recently-touched file's draft ever survives — starting a second
unfinished edit on another file before returning to the first will lose the
first's draft, which is an accepted trade for not having to track history
for files nobody's mid-edit on.

`MediaTableView._open_date_picker` opens it, and holds a
`QPersistentModelIndex` so the commit still lands on the right row if rows
move while the popup is open. Committing calls
`model.setData(index, dt, Qt.EditRole)` → `MediaTableModel.set_manual_date(row, dt)`,
which:
1. Snapshots `(f.date, f.date_source, f.is_already_formatted)` into
   `f.manual_date_undo` — but only if it's still `None`, i.e. only on the
   first pick since the last Apply or load. See "Undoing a date-picker
   correction" above.
2. Sets `f.date = dt`, `f.date_source = 'manual'`.
3. Clears `f.user_moved = False` — the chosen date is now the file's
   authoritative position, so Pass 1 takes the simple "untouched strong
   anchor" branch rather than re-anchoring/averaging against neighbours.
4. Clears `f.manual_filename = None` — a filename typed before the date
   correction was based on the old, wrong date and would otherwise sit
   there unchanged and stale.
5. Clears `f.is_already_formatted = False` — see "Wrong-metadata hard
   anchors" above.
6. Calls `_reposition_by_date(row, dt)`: moves the file to sit between the
   two files whose *current* displayed date (`effective_date or date`)
   brackets `dt` — the same outcome as dragging it there by hand, just
   driven by the date instead. Only hard anchors and strong-sourced files
   (`_is_strong`) count as reference points — a weak file's displayed date
   is either nonexistent (`needs_attention`) or itself interpolated from
   its neighbours, so it's skipped either way when looking for where `dt`
   belongs, same as `_find_anchor_before`/`_find_anchor_after` already
   treat weak files as unusable anchors. Finds the qualifying anchor with
   the *smallest* date that's still later than `dt`, not just the first
   one encountered scanning the list — unlike `_reinsert_at_original_index`
   (used by `undo_move`/`undo_manual_date`), there's no guarantee the list
   is already in date order (a fresh alphabetical load routinely isn't),
   so stopping at the first later-dated anchor found can land on one much
   further away than the true nearest one, which might sit later in the
   list.
7. Runs a full `recalculate_proposed_filenames()` — this can legitimately
   change other rows too (this file may now anchor its neighbours'
   interpolation, or no longer anchor ones it used to), so it isn't a
   targeted, single-cell update.

Since the file can end up anywhere in the list, `_open_date_picker`'s
`commit()` callback follows it after `setData()` returns — finds its new
row by identity and calls `scrollTo(..., QAbstractItemView.PositionAtCenter)`
plus `setCurrentIndex()`, the same pattern `undo_move`'s caller in
`mousePressEvent` uses for the same reason.

`_is_strong()` needed no change to treat `'manual'` as strong: it already
returns strong for any `date_source` that isn't `'date modified'` or
`'none'`.

#### Wrong-metadata hard anchors
`set_manual_date` also clears `f.is_already_formatted`, demoting a hard
anchor back to an ordinary strong anchor. Without this, "hard anchor never
renames" — meant for a file that was already correctly named before the app
touched it — would also permanently lock in a file the app itself renamed
from *wrong* metadata, with no way to fix it. Once demoted the file re-enters
Pass 1 as a strong-anchor-not-moved file like any other: `build_new_filename`
strips the old (wrong) `YYYYMMDD_HHMMSS` prefix from `f.filename` and
proposes a new one from the corrected date, and the New filename box and its
"x" reappear automatically (they're driven by the same `is_already_formatted`
flag, not a separate switch) so the file can be renamed for real on the next
Apply. Nothing on disk changes until then — `f.filename` still reads the old
(wrong) name in the meantime, which is exactly why Pass 1 must pass
`force=True` into `build_new_filename` for this branch: without it,
`build_new_filename`'s own already-formatted guard would see that old name
still matches the `YYYYMMDD_HHMMSS` pattern and hand it back unchanged,
silently undoing the correction. (This branch's `f.filename` could never look
already-formatted before this feature existed, so `force=True` is a no-op
for every other case that reaches it — only a just-demoted hard anchor hits
the guard.)

It's also relocated to its correct chronological position (`_reposition_by_date`,
below) — the same `set_manual_date` call handles both, since a hard anchor
wrong enough to need correcting is exactly the file most likely to be
sitting somewhere badly wrong in the list.

### File size and deleting a file

**Size column** (`COL_SIZE`, right after Date taken). `MediaFile.size_bytes`
is read with `os.path.getsize` in `metadata_reader._new_result`, i.e. on the
metadata worker threads, not when the stub rows are created — a stat per file
on the main thread would delay the table appearing on a big or network folder.
So it fills in with the dates and is blank until then. It's shown in MB to one
decimal place using 1 MB = 1024 × 1024 bytes (what Explorer shows); anything
non-empty that would round to `0.0` reads `<0.1 MB` instead. Values and header
are right-aligned (`_SizeDelegate`, and `headerData`'s `TextAlignmentRole`).
`apply_rename()` re-reads the size after a rename that rewrites the file's
metadata (interpolated / manually dated files), since that changes it slightly.

**Delete column** (`COL_DELETE`, last — after Move). A trash-can button drawn
with painter primitives (`DeleteDelegate`, like the calendar icon). Clicking it:

1. `MediaTableView.mousePressEvent` hit-tests the button rect and emits
   `file_delete_requested(MediaFile)` from the next event-loop turn
   (`QTimer.singleShot(0, …)`) so the modal confirmation doesn't run from inside
   the press handler. It's handled in the view, not `editorEvent`, so pressing
   the button doesn't run the normal selection handling — the existing selection
   is left alone (the answer may well be No). The file is passed, not a row
   number, because rows can move before the signal is handled.
2. `MainWindow._delete_file` asks `Delete "<name>"? It will be moved to the
   Recycle Bin.` (Yes/No, **No** default). It deletes **only the clicked row's
   file**, never the whole selection — a stray click shouldn't be able to delete
   several photos.
3. `MediaTableModel.delete_file(row)` moves the file to the **Recycle Bin**
   (`QFile.moveToTrash`) rather than deleting it permanently — it's the one
   action here that can't be previewed first, and these are photos the user may
   want back. Only after that succeeds does it `beginRemoveRows`/`endRemoveRows`
   (so Qt adjusts selection and current row itself) and re-run
   `recalculate_proposed_filenames()`, since the deleted file may have been an
   anchor for its neighbours. A file already missing from disk just loses its
   row. If the trash fails (open in another program; a network drive has no
   Recycle Bin) nothing changes and the user gets a "could not delete" message.
4. It refuses while metadata is still loading (`_pending_metadata_shards > 0`):
   the shards write results back by fixed row index, so removing a row under
   them would land results on the wrong files. (The loading overlay blocks the
   table until then anyway.)

There's no in-app undo: restoring a deleted file is done from the Recycle Bin,
and it won't reappear in the table until the folder is reloaded.
`tests/test_file_size_delete.py` covers this without ever reaching the real
Recycle Bin (missing files, or `QFile.moveToTrash` patched).

### Viewing a file's full metadata
Two entry points, sharing one implementation (`metadata_panel.py`) so there's
one place that reads and displays raw metadata rather than two:

- **The table's Info column** (`COL_METADATA`) — an info-circle icon
  (`MetadataDelegate`, drawn with `QPainter` primitives like the trash and
  calendar icons, not the Unicode `ⓘ` character — see Reload's icon for why
  that risk isn't worth it), painted with no button box around it — a first
  version drew one (matching `DeleteDelegate`'s button chrome) and it read as
  visual noise next to the plain "i"; `_button_rect` still defines the
  click/hit-test area, it's just never painted. Clicking it opens
  `MetadataDialog`, a modal dialog for that one file. Clicks are routed by
  `MediaTableView.mousePressEvent` (`metadata_requested = Signal(object)`),
  the same `_button_rect` hit-test pattern as `COL_DELETE`.
- **The preview's Metadata button** — next to "Open in default app" (a plain
  text button, not icon-only — unlike Play/Pause/Mute, there's no
  obvious-at-a-glance shape for "show metadata"). Toggles `MetadataPanel` as
  a side panel docked to the **left** of the photo/video (so it doesn't end
  up sitting underneath the Delete/Close buttons at the top right), inside a
  `QHBoxLayout` where the panel comes first with a fixed width (380px) and
  the photo/video stack has the stretch factor — showing or hiding the panel
  just changes how much width the stack gets, no extra layout code needed
  for the "shrink to make room" behaviour. Stepping to another file while
  the panel is open (`_show_file`) refreshes it for the new file; closing it
  again (re-clicking Metadata) is the only way to dismiss it — Esc still
  closes the whole preview, not just the panel. Constructed with
  `MetadataPanel(dark=True)` — see "Light/dark theming" below.

**Light/dark theming.** `MetadataPanel` is the one piece of UI shared between
a light context (the table's dialogs/rows) and a dark one (the preview), so
it takes a `dark: bool = False` constructor flag rather than hardcoding
either look. `_PANEL_STYLE_LIGHT` (default, used by `MetadataDialog`) matches
the table's own palette; `_PANEL_STYLE_DARK` (the preview's panel) reuses
`preview.py`'s `_BAR_STYLE` palette exactly (`#111827` background, `#F9FAFB`/
`#9CA3AF` text, `#1F2937` panel chrome, `#374151` borders) rather than
inventing a third set of colours.

**What it shows**: every tag `exiftool` reports for the file — not just the
handful (date, duration, size) the rename pipeline keeps — as a two-column
tag/value table, in exiftool's own order (not re-sorted: it already groups
related tags sensibly). `metadata_reader.read_all_metadata()` is `et.
get_metadata(filepath)[0]` with `SourceFile` dropped (redundant — the panel
already shows the filename), reusing the same `ExifToolHelper`/
`_vendor_path`/`EXIFTOOL_ENCODING` plumbing as `read_metadata()`.

**Read on demand, not cached at load time.** `read_metadata_batch()` already
asks exiftool for the full tag set per file and then throws away everything
except the few tags `_date_from_tags`/`duration_seconds` need — this feature
exposes what was already being discarded rather than reading anything new in
bulk. Stashing the full tag dict on every `MediaFile` at load time was
considered and rejected: real cost (extra memory × up to 2000 files) for a
feature only a few files per folder will realistically ever have opened, and
it would show stale data for a file whose metadata changes later (e.g. after
Apply rewrites it).

**Threading.** A dedicated one-thread `QThreadPool` (`metadata_panel.
_METADATA_POOL`), not `QThreadPool.globalInstance()` — the same reasoning
as `preview.py`'s `_PREVIEW_POOL`: the global pool is handed every file in
the folder for thumbnail generation as soon as it loads, so a read dispatched
onto it while that's still in progress would queue behind however much of
that backlog was outstanding, the exact bug `_PREVIEW_POOL` already exists
to avoid. One thread is enough — this is a single on-demand read per click,
never a batch. `_MetadataLoader` (`QRunnable`) holds its `_MetadataSignals`
(`loaded = Signal(str, dict)`, `failed = Signal(str, str)`) the same way
`preview.py`'s `_ImageLoader` holds `_LoadSignals` — so the signals object
can't be garbage-collected while a queued cross-thread emit is still in
flight (see `MediaTableModel._active_workers`'s note on this same gotcha).

**Staleness guard.** `MetadataPanel._pending_filepath` is set by every
`show_file()` call; `_on_loaded`/`_on_failed` drop a result whose filepath
doesn't match it — covers both re-opening the dialog for a different row
(table) and stepping to another file while the panel is open (preview)
before a slower read has come back. `tests/test_metadata_panel.py` covers
this and the three display states (loading / tag table / error) without a
real exiftool call, by calling `_on_loaded`/`_on_failed` directly.

**The status label and the table live in a `QStackedWidget` (`self._body`),
not two widgets toggled with `setVisible()`.** The first version did that,
and had only the table marked as the layout's stretchy item
(`layout.addWidget(self._table, 1)`) — once the table was hidden, nothing
was left to absorb the panel's extra height, and the title label and the
status label ended up splitting it 50/50 between them instead, stranding
"Loading…" roughly in the middle of the panel rather than right under the
title (a real user saw exactly this). A stack always sizes to whichever
page is current, so this can't happen — the same reason `preview.py`'s
`_pages` already uses one for stage/video. `_status` is also explicitly
top-aligned (`Qt.AlignLeft | Qt.AlignTop`): a `QLabel` vertically centres by
default, which read fine in a small box but stranded the text again once
the status page was sized to fill the same large area the table occupies.

### Location icon
A blue map-pin icon (`MetadataDelegate._draw_location_pin`, painter-drawn
like every other icon in this app, not a Unicode/emoji glyph), shown in the
Info column right after the info-circle icon, when a file has GPS data
(`MediaFile.latitude`/`longitude` are not `None`). Hover shows the actual
coordinates, e.g. `38.342577° S, 144.307189° E` (`_format_coordinates`,
decimal degrees — exiftool's `Composite:GPS*` tags already give decimal, no
DMS conversion needed). Purely informational, same as the UUID warning
badge next to it conceptually but with no warning attached — it's a nice-
to-know, not a flag that something might be wrong.

**Data source, and why it costs nothing extra:**
`read_metadata()`/`read_metadata_batch()` already fetch every tag exiftool
has for each file to pull the date out of it (`_date_from_tags`) — this
reads the same already-fetched `tags` dict for `Composite:GPSLatitude`/
`Composite:GPSLongitude` (`_location_from_tags`), one more dictionary
lookup, no extra exiftool calls, no load-time cost. `Composite:GPS*` is
exiftool's own already-signed, already-decimal value, computed the same way
regardless of where the file actually stores it (EXIF GPS on photos,
QuickTime GPS on videos) — confirmed against the author's library: 212 of
1720 photos scanned had it, and so did video — so one check covers both
file types. Falls back to the unsigned `EXIF:`/`GPS:GPSLatitude` +
`GPSLatitudeRef` ('S'/'W' negative) for the rare case exiftool didn't
compute the Composite tag.

**Layout:** the Info column is 64px — wide enough for the info-circle
button (left half) and the location pin (right half) side by side, each
centred in its half (`MetadataDelegate._button_rect`/`_location_icon_rect`).
The location pin isn't clickable, only hovered — unlike the info icon
beside it, which opens the metadata dialog.

**A real bug, caught by zooming into a real render, not by eye on the
painted icon itself:** the pin is built from a circle (the "head") plus a
triangular point, meant to overlap so they read as one silhouette. The
first version's point never actually extended past the circle's own bottom
edge — it rendered as a plain circle with no visible point at all. Fixing
the proportions (a smaller head sized to leave the bottom third of the
icon for the point) surfaced a second bug: `QPainterPath`'s default
`OddEvenFill` XORs overlapping subpaths, so the circle/triangle overlap was
punching a notch out of the shape instead of merging into one — fixed with
`path.setFillRule(Qt.WindingFill)`, which unions them as intended.

### Hold to repeat — Move up/down buttons
When the Move up/down toolbar buttons are held down, the move action repeats
automatically. Behaviour:

- **Initial delay** — 500ms before repeat starts (prevents accidental triggers
  on a normal click)
- **Repeat interval** — starts at 150ms, accelerates gradually to a minimum
  of 60ms after ~10 repeat cycles
- **Speed cap** — 60ms is the hard floor. Never accelerates beyond this,
  regardless of how long the button is held. This prevents the list from
  scrolling to the end uncontrollably.
- **Release** — repeat stops immediately on mouse button release

Implementation:
- Use a `QTimer` (`self._repeat_timer`) on `MainWindow`
- Use a counter `self._repeat_count` that increments on each tick
- On `pressed` signal of `_btn_up` / `_btn_down`: record direction, start
  timer with initial interval of 500ms, fire first move immediately
- On first timer tick: reset interval to 150ms, start incrementing
  `_repeat_count`, recalculate interval as
  `max(60, 150 - self._repeat_count * 9)` on each tick
- On `released` signal: stop timer, reset `_repeat_count` to 0
- Connect to `pressed` and `released` signals, not `clicked`

Do not apply hold-to-repeat to the per-row chevrons — toolbar buttons only.

### Full-screen preview
A double-click on a thumbnail cell (compact or expanded) opens `PreviewWindow`
(`preview.py`) — a larger look at one file than Expanded View gives, for the
times you need to actually review it. It replaces the old behaviour of handing
the file to the default app (`os.startfile`); that survives as the preview's
**Open in default app** button. Double-click, not single click: a single click
is too easy to trigger accidentally, especially on the small compact-mode
thumbnail.

Implementation: `ThumbnailDelegate` detects `QEvent.MouseButtonDblClick` in
`editorEvent()` and emits `open_file_requested = Signal(str)` (the filepath),
connected to `MainWindow._open_preview`, which finds the `MediaFile` and shows
the window with `open_on_screen()` (a modal `QDialog`, kept in
`MainWindow._preview` while it's up). **It fills `screen.availableGeometry()` —
everything except the taskbar — rather than using `showFullScreen()`.** On
Windows `showFullScreen()` didn't cover the taskbar, which then sat on top of the
bottom of the window: exactly where the video controls are, so a real user saw no
Play button or seek slider at all. It also left a strip of border down the right
edge. Sizing to the usable area keeps every control on screen wherever the
taskbar is (or isn't); the cost is that the taskbar stays visible. `self._expanded` only controls thumbnail
scaling in `paint()`.

**It has a normal title bar**, not `Qt.FramelessWindowHint` — looks and
behaves like an ordinary window (with its own system menu/close control),
matching `MainWindow`, rather than a borderless overlay. That costs some
vertical space the content area used to have, and `open_on_screen()` has to
account for it: `setGeometry()` positions the window's *content* area, and
on Windows the title bar is added above it, not below — sizing content to
the *full* `availableGeometry()` was tried first and pushed the title bar
itself off the top of the physical screen (verified: `frameGeometry().top()`
ended up above `screen().geometry().top()`). Frame margins aren't reliably
known before the window has actually been shown once (a Qt/platform
limitation, not something settable up front), so `open_on_screen()` shows at
the naive geometry first, measures the real title-bar height from the
now-realized `frameGeometry()`, and corrects — shrinking content height by
that amount and shifting it down, so the *frame* (title bar included) ends
up matching `availableGeometry()` exactly: nothing above the screen, nothing
over the taskbar.

**A second, immersive full-screen mode sits on top of this windowed one.**
Double-clicking the photo/video itself (not the top/bottom bars, not the
metadata panel) toggles it — `PreviewWindow.mouseDoubleClickEvent` checks
the click against `self._pages.geometry()`, which only covers the actual
photo/video area. Entering it hides `_top`, `_controls` and
`_metadata_panel` and calls `showFullScreen()` — genuinely full screen,
covering the taskbar too, since with the bars gone there's no video
controls bar left to be hidden under it (the problem `availableGeometry()`
sizing exists to avoid for the windowed mode). From there it's **keyboard
only**: Left/Right/Delete/Space all already work unchanged (they're in
`keyPressEvent` regardless of window state), and Esc exits back to the
*windowed* preview rather than closing it outright — a second Esc, now from
windowed, closes it as before. Double-clicking the photo/video again also
exits, the same gesture reversed. The metadata panel, if it was open, is
not reopened on exit — Metadata is right there on the now-visible top bar.

Two bugs surfaced building this, both now covered by
`tests/test_preview.py::TestFullScreen`:
- **Exiting via a second double-click looked like it did nothing.**
  `_exit_fullscreen()` originally just repositioned the window
  (`open_on_screen()`'s `setGeometry()` calls); that only *moves* a window,
  it doesn't clear Qt's internal full-screen window state, so
  `isFullScreen()` stayed `True` and the next toggle's geometry fight
  with the still-active full-screen state produced visibly wrong results.
  Fixed by calling `self.showNormal()` first.
- **The controls bar stayed visible at the bottom of an otherwise
  full-screen video.** `_enter_fullscreen()` called
  `_refresh_controls_visibility()` (which reads `isFullScreen()`) *before*
  `showFullScreen()` had actually changed the window state — so the check
  ran against the still-`False` old state. This was invisible for a photo
  (`is_video=False` already hides the bar regardless of that term), which
  is exactly why it shipped — only a video exposed it. Fixed by swapping
  the call order.

**Navigation.** Left / Right (or the Prev / Next buttons) step to the previous
/ next file *in the table's current order* — up / down the list — not the
folder's order, so it follows any moves the user has made. Like the toolbar's
Prev/Next it dead-ends rather than wrapping: the button is greyed at each end
and the key does nothing. The current file is tracked by identity
(`_index()` searches `model.files()` for the `MediaFile`), never by a stored
row number, since rows move and disappear. The info line shows `n / total`,
the date and its source badge, the size, and — when one is pending — the name
the file will be renamed to.

**On close** (Esc or the Close button), `MainWindow._on_preview_closed`
selects the file the preview ended on and scrolls it to the centre of the table
(`scrollTo` + `selectRow` + `setCurrentIndex`), since stepping may have taken
it a long way from where the table was.

**Delete** (Delete key or button) goes through the same
`MainWindow._delete_file(f, parent, before_delete)` as the row's trash button —
so the same confirmation, the same Recycle Bin, the same "could not delete"
message — with the confirmation parented to the preview (a window covering the
screen would otherwise hide it). Afterwards the preview lands on the next file, or the
one above if that was the last, or closes if it was the only file.
`before_delete` runs after the user confirms and before the file is touched:
a playing video has the file open, and **Windows won't move an open file to the
Recycle Bin**, so the preview releases the player there (`_release_media()`:
`stop()` + `setSource(QUrl())`) rather than up front — declining the
confirmation must not stop the video. If the trash then fails, the file is put
back on screen.

**Photos** are decoded on a worker thread (`_ImageLoader`, results delivered
through one `_LoadSignals` object owned by the window) at *screen size* —
never full resolution; this is a fit-to-screen viewer with no zoom. JPEGs use
Pillow's `draft()` so libjpeg decodes at a reduced scale, EXIF orientation is
applied, and the result is a `QImage` that owns its pixels (never a `QPixmap`
off the GUI thread). A small file is enlarged to fill the view, capped at
`MAX_UPSCALE` (3x). The neighbours on either side are prefetched into a small
LRU (`CACHE_SIZE`) so stepping is quick; a photo that hasn't arrived yet shows
"Loading…", one that can't be read

**`_PREVIEW_POOL`, a dedicated two-thread `QThreadPool`, not
`QThreadPool.globalInstance()`.** A real user hit a "Loading…" that stuck
around for tens of seconds on a ~1700-file folder — traced (via temporary
timing prints, since nothing in this app's own testing reproduced it: window
construction and `open_on_screen()` together took under 300ms) to the photo
decode queuing behind `media_model.py`'s `ThumbnailWorker` backlog, which is
handed every file in the folder as soon as it loads and, per Threading above,
can take up to ~54s for 2000 files — both were submitting to the same global
pool. A preview opened before thumbnails finished had its own decode stuck
behind however much of that backlog was still outstanding. `_PREVIEW_POOL`
gives the preview its own, separate queue so it's never behind a folder's
worth of thumbnail jobs; two threads is enough since this only ever services
the file on screen plus its two prefetched neighbours, not a whole folder.
`tests/test_preview.py::TestDecodeUsesItsOwnThreadPool` saturates the global
pool with blocking jobs and asserts a decode still completes promptly.
shows "Can't display this file".

**Video** plays through `QMediaPlayer` + `QAudioOutput` + `QVideoWidget`, and
starts playing when it's shown. The bottom bar (hidden for photos) has
Play/Pause, a seek slider (a click jumps to that point), `m:ss / m:ss`, and
Mute (which persists across files). Play/Pause and Mute are icon-only —
a triangle/two bars, and a speaker that gains a cross when muted
(`_draw_play_triangle`/`_draw_pause_bars`/`_draw_speaker`/`_draw_speaker_muted`),
the same `QPainter`-drawn treatment as the trash and refresh icons, with a
tooltip ("Play"/"Pause", "Mute"/"Unmute") standing in for the label. Space toggles play/pause. When a clip
reaches its end it rewinds to the start and waits there *paused* (`_on_media_status`:
`setPosition(0)` + `pause()`), so the first frame is showing and Play watches it
again — left to itself the video surface goes blank at the end. A file that can't be played shows
the reason and points at "Open in default app". Moving to another file always
releases the player first, so one clip's sound never carries on under the next.

**Stepping quickly through several videos before one finishes opening is
expected** and cancels the still-opening previous source — reproduced by
calling `PreviewWindow._step()` back-to-back with no event-loop pumping
between calls, which lands correctly on the final file and plays/shows it
fine every time. Qt's FFmpeg backend logs the cancelled opens through
`qt.multimedia.ffmpeg.mediadataholder` as `Could not open media... Immediate
exit requested` — a real user hit this and (reasonably) read it as something
breaking. It's silenced in `main()` via
`QLoggingCategory.setFilterRules('qt.multimedia.ffmpeg.mediadataholder.warning=false')`,
set before the `QApplication` is constructed. This is a different, unrelated
reporting path from `QMediaPlayer.errorOccurred`/`_on_player_error` above —
a genuinely broken file (verified with a truncated `.mp4`, moov atom
missing) still shows "Can't play this video" correctly with the category
silenced, since that comes from the player's own signal, not this log line.

`tests/diagnostic_video_playback.py` is the standalone check that Qt can decode
real clips (H.264, HEVC, MPEG-4, MJPEG, XVID: all passed against the author's
library). Two things it can't tell you: whether playback is *smooth* or
audio/video *in sync* (needs a person watching), and it prints
`Failed setup for format d3d11: hwaccel initialisation returned error` for some
H.264 clips — Qt falls back to software decoding and they still play, but heavy
4K clips may be less smooth on that path. Not yet built: the packaged `.exe`'s
size with QtMultimedia and its FFmpeg libraries (expected: tens of MB).

**Testing gotcha:** don't wait on the preview's background photo load with
`QTest.qWait` in a script — it starves the worker threads (the decode "takes"
exactly as long as the wait). Use `app.exec()` with a `QTimer`, or a Python
loop of `processEvents()` + `time.sleep()`. The real app's event loop is fine.

### On folder load
- Stub rows inserted immediately (filename only) so table appears instantly
- Metadata (dates, duration) is read for all files via several concurrent
  sharded exiftool batch calls; thumbnails are generated separately, one per
  file, in parallel
- Spinner overlay and its progress ring track metadata only, and hide when
  all metadata has been read (`folder_load_complete`) — a few seconds for
  2000 files. The UI is usable from that point.
- Thumbnails keep loading afterwards. Thumbnail cells that haven't loaded
  yet show a pulsing grey placeholder (see Thumbnail loading state below);
  it stops when `thumbnails_complete` fires.
- On metadata completion: sort alphabetically by filename, then `recalculate_proposed_filenames()`

### Thumbnail loading state
`MediaFile.thumbnail_loaded` is `False` until the thumbnail worker has
finished with that file, *whether or not it produced an image*
(`ThumbnailWorker` always emits `thumb_ready`, with `qimage=None` on
failure). `ThumbnailDelegate.paint` uses it to tell three states apart:
image present → draw it; not yet loaded → grey placeholder pulsing between
two greys (phase from `time.monotonic()`, so all loading cells pulse in
step) with a small static "Loading…" label (centred for photos; at the
bottom for videos, which keep their ▶ glyph in the centre); loaded but no
image (corrupt file, unsupported format) → static grey placeholder, no
label. The pulse alone is a subtle contrast, hence the text; if the label
proves too busy with many rows visible, drop it and strengthen the pulse. `MainWindow._thumb_pulse_timer` (80ms) repaints only the
thumbnail column, started in `_on_load_started` and stopped by
`thumbnails_complete`. Metadata chunks replace the row's `MediaFile`, so
`_on_metadata_chunk_ready` must carry `thumbnail` and `thumbnail_loaded`
across from the old object.

### Threading
Metadata reading and thumbnail generation are decoupled, because launching
one `exiftool.exe` process per file was the dominant cost for large folders
(2000+ files) — see `metadata_reader.read_metadata_batch()`. Measured on a
real 2000-file folder (mixed photos/videos, 16 threads): ~209ms/file with
the original one-process-per-file approach (~418s sequential, ~87.5s with
the old per-file worker's 16-way concurrency, metadata + thumbnails
together). Current design: all metadata in ~2.5-5s, everything including
thumbnails in ~54s — thumbnails are now the dominant cost of a load.
Single exiftool process, no sharding: ~13s for all metadata.

- **exiftool output must be decoded as UTF-8** (`EXIFTOOL_ENCODING` in
  `metadata_reader.py`, passed to every `ExifToolHelper`). pyexiftool
  otherwise decodes with the platform default (cp1252 on Windows), and one
  non-ASCII byte in any file's metadata raises `UnicodeDecodeError` for the
  whole call. In a batch that took out the entire 100-file chunk, which
  then fell back to one exiftool launch per file (~200ms each) — this, not
  file clustering or CPU contention, was the cause of the shards that sat
  at "3 shards remaining" for ~90s (metadata took ~103s in the app vs ~2.5s
  once fixed). It also affected the original `read_metadata()`: an affected
  file silently lost its metadata date and fell through to filename / date
  modified (5 `.HEIC` files in the test folder showed a `filename` badge
  instead of `metadata`; the dates themselves happened to match). Diagnosing
  this took several wrong turns (thread starvation, straggler shards,
  progress-signal semantics) because the fallback path hid the exception —
  if a shard is ever again dramatically slower than the others, look for a
  chunk raising and falling back before looking at scheduling.

- **Metadata is sharded across several exiftool processes**: one persistent
  exiftool process parses files serially (~13s for 2000 files), so
  `load_folder()` splits the file list into
  `min(pool.maxThreadCount(), file_count)` contiguous slices, each read by
  its own `MetadataBatchWorker` with its own persistent process
  (`start_index` is the slice's offset, so `chunk_ready` reports global row
  indices) — ~2.5-5s. This is an optimisation, not a necessity: a single
  worker would be simpler (no shard counter / `start_index` / per-shard
  bookkeeping) at the cost of ~13s instead of ~3s to a usable UI. The
  shard design was originally justified by a measurement of a lone worker
  taking ~270s, but that number was inflated by the UTF-8 fallback bug
  above and should be disregarded.
- Each `MetadataBatchWorker` shard calls `read_metadata_batch()`, which
  reads its slice of files (including video duration, via the
  `QuickTime:Duration` tag) in chunks of `METADATA_CHUNK_SIZE` (100).
  Chunking is for progress reporting and cancellation, not command-line
  length — pyexiftool sends arguments over stdin. Emits `chunk_ready` after
  each chunk so rows update progressively rather than all at once at the end.
  If exiftool can't start at all, this raises once per shard and the UI
  shows one error dialog (`MediaTableModel.metadata_load_error`, deduped
  across shards) — unlike `read_metadata()`'s single-file path, which
  silently falls back to date-modified for that one file. The load
  completes (sort + `recalculate_proposed_filenames()`) once every shard's
  `finished` signal has arrived (`_pending_metadata_shards` counter).
- **Thumbnails**: one `ThumbnailWorker` (QRunnable) per file, still parallel
  across the thread pool, generating a thumbnail (and, for video, extracting
  a frame via ffmpeg) — decoupled from metadata so a video-heavy folder
  doesn't serialize thumbnail generation behind (or in front of) the
  metadata batch. Deliberately still eager (not limited to visible rows) —
  see "Lazy thumbnail loading" in Future features for why this wasn't taken
  further.
- Each worker owns its own signals instance (`BatchSignals` /
  `ThumbnailSignals`).
- **`MediaTableModel._active_workers` is a permanent keep-alive registry,
  not an oversight to clean up**: `QThreadPool.start()` does not keep a
  Python reference to the runnable it's given. Once `run()` returns,
  nothing stops the GC from collecting the worker — and its unparented
  `signals` QObject along with it — before a still-queued cross-thread
  signal emission has been delivered to the main thread, which surfaces as
  `RuntimeError: Signal source has been deleted` and silently drops
  whatever that emit carried (observed dropping ~1200 of 2000 thumbnails
  before this was found). This is a real PySide6 gotcha, not specific to
  this codebase, and it predates the batching work — the original
  `MetadataWorker` had the same unguarded pattern; it just wasn't
  triggered reliably until sharding made completion fast enough to expose
  the race. Every worker is appended to `_active_workers` before
  `_pool.start()` and the list is *never* cleared on reload — an old
  generation's worker may still be mid-`run()` when a new load starts
  (`cancel()` only stops new work from starting, it doesn't interrupt
  in-flight work), so dropping references early would reintroduce the same
  race. The list keeps every worker from every load alive for the life of
  the model; the per-load `_load_generation` check in the callbacks is
  what makes stale results inert, not removal from this list.
- `QImage` created on worker thread, converted to `QPixmap` on main thread in `_on_thumb_ready`
- **Cancel-on-reload**: `load_folder()` bumps `_load_generation` on every
  call. Workers carry the generation they were created with; a superseded
  worker's results are dropped by the generation check in the callbacks
  (`_on_metadata_chunk_ready`, `_on_thumb_ready`, etc.) instead of being
  spliced into the new folder's rows. In-flight work isn't interrupted
  (a chunk in progress can't be cancelled mid-call, and a running
  `ThumbnailWorker` finishes its current file) — cancellation only stops
  work from *starting* for the old generation.
- **The overlay's progress and `folder_load_complete` are about metadata
  only; thumbnails are signalled separately.** `file_progress` is a running
  count of metadata files done (shards complete chunks concurrently, so it
  is a total, not any one shard's high-water mark). `folder_load_complete`
  fires when the last metadata shard finishes and
  `recalculate_proposed_filenames()` has run, and the UI is usable from
  then. `thumbnails_complete` fires when every `ThumbnailWorker` has
  reported (success or failure), typically well after. This was a
  deliberate choice for an async, usable-early feel; the alternatives were
  tried and rejected: gating the spinner on thumbnails too (needs a
  "fully-done" progress count with per-file flags, keeps the UI blocked for
  the ~50s thumbnail phase). The gap is covered by the thumbnail loading
  state above rather than by keeping the overlay up.
- **`MainWindow._on_model_data_changed` skips `_refresh_status()` for
  thumbnail-only `dataChanged` (`roles == [Qt.DecorationRole]`)**.
  `_refresh_status()` does several O(n) scans over every file and depends
  only on dates/rename state, never on thumbnails; without the skip, ~2000
  thumbnail arrivals each re-ran those scans, saturating the main thread
  and making the UI look frozen during the thumbnail phase.
- Thumbnails are matched back to their row by filepath, not just index
  (`_resolve_index`): the one-time sort in `_on_metadata_shard_finished`
  (once every shard has finished) can reorder rows while thumbnails for
  that same load are still arriving (e.g. mixed-case original filenames
  sort differently case-sensitive vs. case-insensitive), so a thumbnail's
  original dispatch index can go stale mid-load.

### Dialog styling
All `QMessageBox` dialogs are styled globally via `app.setStyleSheet()` in
`main()` to match the app's visual language — white background, clean
typography, rounded corners, blue primary button (Yes/OK), grey secondary
button (No/Cancel). No separate styling needed per dialog. Consistent with
the toolbar button styles defined in `_btn_style()` and `_btn_style_primary()`.

Key styles applied:
- `QMessageBox` — white background, `font-size: 13px`, `color: #111827`
- `QMessageBox QLabel` — `padding: 8px`
- `QMessageBox QPushButton` — rounded, grey border, white background
- `QMessageBox QPushButton[text="Yes"/"OK"]` — blue (`#2563EB`), white text, bold

---

## Signals

```python
folder_load_started = Signal(int)       # file count
folder_load_complete = Signal()         # all metadata read — UI usable
thumbnails_complete = Signal()          # every thumbnail attempted (usually after the above)
file_progress = Signal(int, int)        # metadata files done, total
attention_required = Signal(int)        # count of files needing attention
rename_progress = Signal(int, int)      # done, total
rename_complete = Signal(int, int)      # success_count, error_count
phase_changed = Signal(bool)            # True = strong anchors still pending rename.
                                         # Informational only (see "No operational
                                         # phases") — main.py doesn't connect to it.
metadata_load_error = Signal(str)       # exiftool could not start at all
```

---

## Future features (not MVP — but don't make them impossible to add)
- Timezone offset per source folder
- Drag and drop reordering
- Undo/redo
- Mac support (same codebase, build on Mac)
- AI-assisted ordering suggestion for undated files (vision API)
- Lazy thumbnail loading (only visible rows) — deferred; thumbnail
  generation is eager today (see Threading). With metadata now reading in a
  few seconds, thumbnails are the dominant cost of a load (~54s of the
  ~54s total for 2000 files), so this is the main remaining lever for load
  time. It was deliberately not taken on alongside the exiftool-batching
  work to avoid adding viewport-tracking complexity in the same change; the
  pulsing "Loading…" placeholder covers the wait in the meantime.

---

## What's done
- `metadata_reader.py` — tested and working; metadata reading is batched
  (`read_metadata_batch()`) so large folders launch one exiftool process
  instead of one per file — see Threading
- `media_model.py` — MediaFile dataclass + MediaTableModel, full five-state logic with user_moved, effective_date, interpolation, apply_rename
- `main.py` — main window, toolbar, table view, all delegates, loading overlay, selection retention on move
- `preview.py` — the full-screen preview window: photos (async, screen-size decode, neighbour prefetch) and video, with Left/Right navigation and delete — see "Full-screen preview"
- `metadata_panel.py` — full raw-metadata view shared by the table's Info button and the preview's Metadata side panel — see "Viewing a file's full metadata"
- Editable New filename column, its per-file reset, and per-file manual
  date/time editing — see "Editable New filename column" and "Per-file
  date/time editing" under UI behaviour

## What's next
1. PyInstaller packaging to `MediaReel.exe`