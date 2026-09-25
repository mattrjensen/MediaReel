# Media Reel — Project Spec & Design Decisions

## What this app is
Media Reel is a desktop app (Python + PySide6) (currently just Windows) for assembling photos and videos from multiple people and devices into a single chronological sequence — telling the visual story of a shared experience.

The app works by renaming files with a prepended `YYYYMMDD_HHMMSS_` timestamp, making the filename the permanent, self-describing source of chronological truth — independent of any photo app, operating system, or platform. Even if a file is edited, cropped, or colour-corrected, it still sorts correctly forever.

The primary use case is post-event curation: once files are in order, you can compare competing captures of the same moment side by side and choose the best photo or video of each. The chronological sequence turns an ambiguous pile of files into a navigable story, ready to be culled into the best photographic memory of the event.

The app is intentionally single-session and non-destructive — no files are touched on disk until the user explicitly applies changes.

## Tech stack
- Python 3.14
- PySide6 (UI framework)
- Pillow (image thumbnails)
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
    tests/                ← pytest tests (test_*.py) + standalone diagnostic_*.py scripts
    assets/               ← icons and images
    requirements.txt      ← pinned runtime dependencies (incl. pillow-heif)
    MediaReel.spec        ← PyInstaller spec
    CLAUDE.md             ← this file
    DEVELOPMENT.md        ← how to run, build, and test
    README.md             ← GitHub readme
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

The `[:19]` truncation in the parser strips the timezone offset suffix cleanly,
giving correct local time. `QuickTime:CreateDate` remains in the list as a
fallback for non-iOS video files (Android stores local time there).

**Full candidates list order:**
```python
candidates = [
    'QuickTime:CreationDate',      # iOS .mov — local time with tz offset
    'QuickTime:DateTimeOriginal',  # iOS .mp4 — local time with tz offset
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
selected: bool                 # checkbox state
manual_filename: str | None    # user override from the editable New filename
                               # field. None = use proposed_filename; '' = skip
                               # this file on Apply; any other string = rename
                               # to exactly that. See display_filename and
                               # "Editable New filename column" under UI
                               # behaviour.
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
| Manually dated (`date_source == 'manual'`) | yes — prepend the entered date, full seconds | yes — write new timestamp to file metadata |

The manually-dated row isn't really a sixth state in the five-state sense —
`_is_strong()` already treats `'manual'` as strong, so such a file falls into
whichever of the first two rows its `user_moved` value puts it in. It's
listed separately here because it's the one case in those two rows where
metadata *is* written: the metadata write is decided by
`f.is_interpolated or f.date_source == DATE_SOURCE_MANUAL`, captured in
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
end. This resort is triggered from `_on_rename_complete` in `main.py` after
the success/error dialog is dismissed.
After the resort and recalculate, scroll the table back to the top via
`self._table.scrollToTop()` so the user sees the newly renamed files
from the beginning of the list.

### Apply rename confirmation dialog
One message, always:
{n} file(s) will be renamed.

Make sure you have a backup.

Continue?

`{n}` counts files where `display_filename` differs from `filename` (not
`proposed_filename` — a manual override or a blanked/skipped box must count
the same way `apply_rename()` itself decides what to rename; see "Editable
New filename column"). Implementation: a single `QMessageBox` call in
`MainWindow._apply_rename()`.

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
| 3 | Date taken | Source badge (top) + formatted datetime (below), with a calendar icon at the right — on every row, including hard anchors, since a renamed-from-wrong-metadata file needs a way back — that opens a date-and-time picker popup. Nothing else in the cell is clickable for editing. |
| 4 | New filename (preview) | Grey = no change or placeholder instruction. Amber = will be renamed. Painted as a ~40px input box; a single click anywhere in it starts editing. Empty (no box, no text) on hard anchors — nothing will change, so there's nothing to show. Shows a clear ("x") whenever the box holds a real name; clicking it always blanks the box, which always means skip this file on Apply. |
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

- Hard-anchor rows are empty, not editable (`MediaTableModel.flags()` and
  `mousePressEvent` both check): the filename is already the source of
  truth, and allowing an edit would let a hard anchor get renamed, which
  "hard anchor never renames" forbids. (The one way back is the calendar
  icon, which demotes it out of hard-anchor status first — see "Wrong-
  metadata hard anchors" under Per-file date/time editing.)
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
  or showing a `---` instruction placeholder (not a name). Clicking it always
  sends `''` to the model (`setData(index, '', Qt.EditRole)` →
  `MediaTableModel.set_manual_filename()`), and blank always means the same
  thing regardless of the file's state: **skip this file on Apply** —
  `apply_rename()`'s pending filter, `has_pending_renames()`, and the status-
  bar/dialog counts in `main.py` (`_refresh_status`, `_apply_rename`) all
  read `display_filename`, which is blank once skipped. Emptying the field
  by hand in the editor does the same thing as clicking the "x". Paint
  (`PreviewDelegate.paint`) and hit-test (`MediaTableView.mousePressEvent`)
  share `can_clear_filename` and `_reset_icon_rect`, so an unpainted icon
  can't be clicked and the two can't drift apart.

  This used to special-case a strong anchor still waiting to be renamed —
  blanking it would leave it stuck forever, since Move was disabled while
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
displays — with the current hour/min/sec scrolled into view (`show_near()`
calls `_center_selected()` after `show()`, since it needs the final size),
and shows below the icon (above if there's no room, never off-screen).
"Set date and time" emits `committed(datetime)`; Cancel, Escape or clicking
away changes nothing. Selections (calendar day, list rows) use the app's solid
blue: the app palette's `Highlight` is a very pale blue that made them
nearly invisible.

`MediaTableView._open_date_picker` opens it, and holds a
`QPersistentModelIndex` so the commit still lands on the right row if rows
move while the popup is open. Committing calls
`model.setData(index, dt, Qt.EditRole)` → `MediaTableModel.set_manual_date(row, dt)`,
which:
1. Sets `f.date = dt`, `f.date_source = 'manual'`.
2. Clears `f.user_moved = False` — the chosen date is now the file's
   authoritative position, so Pass 1 takes the simple "untouched strong
   anchor" branch rather than re-anchoring/averaging against neighbours.
3. Clears `f.manual_filename = None` — a filename typed before the date
   correction was based on the old, wrong date and would otherwise sit
   there unchanged and stale.
4. Runs a full `recalculate_proposed_filenames()` — unlike the filename
   reset above, this can legitimately change other rows too (this file may
   now anchor its neighbours' interpolation), so it isn't a targeted,
   single-cell update.

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
A double-click on a thumbnail cell opens the file in its default application
via `os.startfile(filepath)`, in both compact and expanded mode. On Windows
this opens photos in Photos and videos in the default video player. Because
the actual file path is passed, Photos loads the file in folder context —
left/right arrow keys in Photos then navigate through the other files in the
same folder, which is useful for determining correct ordering of undated
files.

Double-click, not single click, in both modes — a single click is too easy
to trigger accidentally, especially on the small compact-mode thumbnail.

Implementation: `ThumbnailDelegate` detects `QEvent.MouseButtonDblClick` in
`editorEvent()`. It emits a signal `open_file_requested = Signal(str)` with
the filepath, connected to a slot in `MainWindow` that calls
`os.startfile(filepath)`. `self._expanded` (set via `set_expanded(bool)` from
the toolbar toggle handler) still controls thumbnail scaling in `paint()`; it
no longer gates whether a click opens the file.

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
- Editable New filename column, its per-file reset, and per-file manual
  date/time editing — see "Editable New filename column" and "Per-file
  date/time editing" under UI behaviour

## What's next
1. PyInstaller packaging to `MediaReel.exe`