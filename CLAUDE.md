# Media Reel — Project Spec & Design Decisions

## What this app is
Media Reel is a Windows desktop app (Python + PySide6) for collating photos and videos from multiple people taken at the same event into a single chronological sequence. It does this by renaming files with a prepended `YYYYMMDD_HHMMSS_` timestamp, making the filename the permanent, unchangeable source of chronological truth — so that even if a file is edited, cropped, or colour-corrected, it still sorts correctly.

## Tech stack
- Python 3.14
- PySide6 (UI framework)
- Pillow (image thumbnails)
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
    CLAUDE.md             ← this file
```

## Supported file types
`.jpg`, `.jpeg`, `.png`, `.heic`, `.heif`, `.mp4`, `.mov`, `.avi`

## Core design principle
The filename is the source of truth. The app is non-destructive until the user clicks Apply rename. Everything before that is a preview. No files are touched on disk until Apply. Files should be self-describing and self-ordering forever, independent of any app, platform, or cataloguing software.

---

## Date resolution — priority order on folder load

For each file, resolve its timestamp using this priority chain:

1. **Metadata** — EXIF `DateTimeOriginal`, then `CreateDate`, then `MediaCreateDate` (via exiftool). Most trusted source.
2. **Filename parsing** — if the filename contains a parseable date/time string (patterns: `YYYYMMDD_HHMMSS`, `YYYY-MM-DD-HHMMSS`, `YYYY-MM-DD_HH-MM-SS`, etc.)
3. **Date modified** — OS file modification timestamp. Unreliable (reflects download/copy time, not capture time). Treated as weak — same as no date for rename purposes.
4. **None** — no resolvable date.

The `date_source` field records which tier was used: `metadata`, `filename`, `date modified`, `none`.

**Strong date** = `date_source` is `metadata` or `filename`
**Weak/no date** = `date_source` is `date modified` or `none`

### iOS video timezone correction
iOS stores `QuickTime:CreateDate` in UTC, not local time. The correct local-time
tag for iOS videos is `QuickTime:DateTimeOriginal` which includes a timezone offset
(e.g. `2026:03:28 21:10:31+11:00`). This tag must appear first in the candidates
list for video metadata reading. The `[:19]` truncation in the parser strips the
offset suffix cleanly, giving correct local time. `QuickTime:CreateDate` should
remain in the list as a fallback for non-iOS video files (Android stores local
time there).

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

- **Hard anchor** — filename already starts `YYYYMMDD_HHMMSS`. No rename, no proposed filename change. Already the source of truth.

- **Strong anchor, not moved** — propose rename using own metadata/filename date, full seconds. Becomes `YYYYMMDD_HHMMSS_<stripped_name>`. On Apply, rename only — do not update metadata.

- **Strong anchor, moved** — user has deliberately repositioned this file, overriding its timestamp. Propose rename using interpolated date between nearest anchors, full seconds. On Apply, rename only — do NOT update metadata (preserve original metadata as a record of what the camera said).

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
date_source: str               # 'metadata' | 'filename' | 'date modified' | 'none'
stripped_filename: str         # filename with any embedded date string removed
proposed_filename: str         # live preview of new filename; equals filename if no change
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
selected: bool                 # checkbox state
```

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

| State | Rename | Update metadata |
|---|---|---|
| Hard anchor | skip | skip |
| Strong anchor, not moved | yes — prepend own date, full seconds | no |
| Strong anchor, moved (re-anchored) | yes — prepend averaged date, full seconds | no — preserve original metadata |
| Weak anchor, moved (interpolated) | yes — prepend interpolated date, full seconds | yes — write new timestamp to file metadata |
| Weak anchor, not moved | skip | skip |

After renaming, for each renamed file:
- Update `f.date` from the new filename prefix
- Set `f.date_source = 'filename'`
- Set `f.is_already_formatted = True`
- Reset `f.user_moved = False`
- Reset `f.is_interpolated = False`, `f.is_re_anchored = False`
- Update `f.effective_date = f.date`

Check file exists before renaming. Collect errors without stopping the batch. Emit `rename_complete(success_count, error_count)` when done.

### After Apply rename — resort
After apply_rename() completes (success or partial), the model should call
`_sort_by_filename()` followed by `recalculate_proposed_filenames()`. This
re-sorts the list so newly renamed files (now prefixed with `YYYYMMDD_HHMMSS`)
appear in correct alphabetical/chronological order alongside any other
already-formatted files. Remaining unmoved weak anchors stay flagged at the
end. This resort is triggered from `_on_rename_complete` in `main.py` after
the success/error dialog is dismissed.
After the resort and recalculate, scroll the table back to the top via
`self._table.scrollToTop()` so the user sees the newly renamed files
from the beginning of the list.

### Apply rename confirmation dialog
The confirmation dialog is a single prompt combining all relevant information.
No separate "files need attention" pre-prompt — everything in one dialog.

Format:
Make sure you have a backup first.
{n} file(s) will be renamed.
{x} file(s) have no date info and will be skipped.
Continue?

If there are no files being skipped, omit the skipped line entirely:
Make sure you have a backup first.
{n} file(s) will be renamed.
Continue?

If there are no files to rename at all, the Apply button should be disabled
so this dialog is never reached.

Implementation: in `MainWindow._apply_rename()`, replace the two separate
`QMessageBox` calls with a single combined message built from
`model.has_pending_renames()` count and `model.attention_count()`.

---

## Colour coding

### New filename (preview) column — text colour

| Colour | Meaning |
|---|---|
| Grey | No change will happen (hard anchor, or unmoved weak anchor) |
| Blue | Will be renamed using own trusted date (strong anchor, not moved) |
| Amber | Will be renamed using a derived/approximated date (any moved file — re-anchored or interpolated) |

The distinction between re-anchored and interpolated does not need separate colours — both result in an approximated timestamp and both warrant the same amber treatment.

### Date taken column — source badge

| Badge | Colour | Meaning |
|---|---|---|
| `metadata` | Green | Date from EXIF/video metadata — most trusted |
| `filename` | Grey | Date parsed from filename string |
| `date modified` | Amber | OS modification timestamp — unreliable, treat as weak |
| `interpolated` | Orange | Date derived from neighbours |
| `none` | Red | No date found |

### Row background

| Background | Meaning |
|---|---|
| White / faint grey alternating | Normal |
| Blue tint | Selected |

The amber `needs_attention` highlight is applied only to the **New filename (preview)** cell, not the full row.

---

## Table columns (in order)

| # | Column | Notes |
|---|---|---|
| 0 | Checkbox | Selection |
| 1 | # | 1-based row order, always reflects current staged order |
| 2 | Filename | Original filename on disk |
| 3 | Date taken | Source badge (top) + formatted datetime (below) |
| 4 | New filename (preview) | Grey = no change. Blue = own date rename. Amber = derived date rename. |
| 5 | Preview | Thumbnail. Videos show first frame + duration badge. |
| 6 | Move | Up/down chevron buttons — routes through MainWindow._move() via Signal |

---

## UI behaviour

### Toolbar
- **Open folder** — opens file picker, loads folder, shows spinner overlay while metadata reads
- **Move up / Move down** — act on all selected rows as a group, maintaining relative order within group. Selection follows the moved rows.
- **⊞ Expand / ⊟ Compact** — toggles between compact (default, 68px rows, 80x60 thumbnails) and expanded (140px rows, 160x120 thumbnails) row height mode. Useful when nudging undated files into position by image content. Sits to the left of Apply rename. In expanded mode, clicking a thumbnail opens the file in its default app via `os.startfile(filepath)`.
- **Apply rename** — enabled as soon as any file has a pending rename. Warns if any files still need attention. Confirms before proceeding.
- **N files need attention** — amber warning button, visible when any file has `needs_attention=True`. Clicking jumps to first such row.

### Row height toggle state
- Default: `self._expanded = False`
- `MetadataWorker.THUMB_W = 160`, `MetadataWorker.THUMB_H = 120` — always load at full size
- Compact display: scale to 80x60 in delegate
- Expanded display: scale to 160x120 in delegate

### Move behaviour
- Toolbar buttons and per-row chevrons both route through `MainWindow._move(direction, clicked_row)`
- Selection is retained after move — selected rows follow their files to the new position
- `user_moved = True` is set on moved files in `move_rows()`

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

### Thumbnail click — open in default app
In expanded mode only, a single click on a thumbnail cell opens the file in
its default application via `os.startfile(filepath)`. On Windows this opens
photos in Photos and videos in the default video player. Because the actual
file path is passed, Photos loads the file in folder context — left/right
arrow keys in Photos then navigate through the other files in the same folder,
which is useful for determining correct ordering of undated files.

In compact mode, thumbnail clicks are ignored — the click target is too small
and too easy to trigger accidentally.

Implementation: `ThumbnailDelegate` detects `QEvent.MouseButtonRelease` in
`editorEvent()` only when `self._expanded` is True. It emits a signal
`open_file_requested = Signal(str)` with the filepath, connected to a slot
in `MainWindow` that calls `os.startfile(filepath)`. The `_expanded` state
is passed to `ThumbnailDelegate` via a method `set_expanded(bool)` called
from the toolbar toggle handler. Do not use double-click — single click is
the right interaction in expanded mode.

### On folder load
- Stub rows inserted immediately (filename only) so table appears instantly
- Metadata and thumbnails loaded on worker threads, rows update progressively
- Spinner overlay shown during load
- On completion: sort alphabetically by filename, then `recalculate_proposed_filenames()`

### Threading
- One `MetadataWorker` (QRunnable) per file, each owns its own `WorkerSignals` instance
- `QImage` created on worker thread, converted to `QPixmap` on main thread in `_on_thumb_ready`
- `folder_load_complete` emitted only after all workers finish

---

## Signals

```python
folder_load_started = Signal(int)       # file count
folder_load_complete = Signal()
attention_required = Signal(int)        # count of files needing attention
rename_complete = Signal(int, int)      # success_count, error_count
```

---

## Future features (not MVP — but don't make them impossible to add)
- Timezone offset per source folder
- Manual date/time editing per file (with metadata write on apply)
- Drag and drop reordering
- Undo/redo
- Mac support (same codebase, build on Mac)
- AI-assisted ordering suggestion for undated files (vision API)

---

## What's done
- `metadata_reader.py` — tested and working
- `media_model.py` — MediaFile dataclass + MediaTableModel, full five-state logic with user_moved, effective_date, interpolation, apply_rename
- `main.py` — main window, toolbar, table view, all delegates, loading overlay, selection retention on move

## What's next
1. PyInstaller packaging to `MediaReel.exe`