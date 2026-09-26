from __future__ import annotations

import os
import re
import sys
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional


def _vendor_path(filename: str) -> str:
    """Resolve a vendor binary path for both source and PyInstaller builds."""
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent
    return str(base / 'vendor' / filename)

CREATE_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0

from PySide6.QtCore import (
    QAbstractTableModel, QModelIndex, QThreadPool,
    QRunnable, Qt, Signal, QObject, QCoreApplication
)
from PySide6.QtGui import QColor, QPixmap, QImage

from metadata_reader import (
    read_metadata_batch, build_new_filename,
    SUPPORTED_EXTENSIONS,
    DATE_SOURCE_NONE, DATE_SOURCE_FILENAME, DATE_SOURCE_MODIFIED,
    DATE_SOURCE_METADATA, DATE_SOURCE_MANUAL
)

# Without pillow-heif, Pillow can't open .heic/.heif files and their
# thumbnails silently fail. Warn at import (visible in a console run) and
# expose the flag so the UI can tell the user (see MainWindow).
HEIF_AVAILABLE = False
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_AVAILABLE = True
except ImportError:
    warnings.warn(
        'pillow-heif is not installed: .heic/.heif files will have no '
        'thumbnails. Install it with: pip install -r requirements.txt',
        RuntimeWarning)

# ── Column indices ──────────────────────────────────────────────────────────
COL_CHECK    = 0
COL_ORDER    = 1
COL_FILENAME = 2
COL_DATE     = 3
COL_PREVIEW  = 4
COL_THUMB    = 5
COL_MOVE     = 6
COLUMN_COUNT = 7

HEADERS = ['', '#', 'Filename', 'Date taken',
           'New filename (preview)', 'Preview', 'Move']

# ── Custom data roles ───────────────────────────────────────────────────────
MediaFileRole      = Qt.UserRole + 1
DateSourceRole     = Qt.UserRole + 2
IsInterpolatedRole = Qt.UserRole + 3
IsReAnchoredRole   = Qt.UserRole + 4
NeedsAttentionRole = Qt.UserRole + 5

# Shown on a weak, unmoved file's New filename box — there's no rename to
# propose until the user acts on it, one way or the other: reposition it
# (chevrons or drag) or give it a trusted date via the calendar icon. One
# message for every state — see "No operational phases" in CLAUDE.md for
# why this used to differ depending on whether a strong anchor was pending.
_PLACEHOLDER_NEEDS_ATTENTION = (
    "--- Put this file in the correct chronological order by moving it up or down, "
    "or selecting a date from the date picker ---"
)


# ── MediaFile dataclass ─────────────────────────────────────────────────────
@dataclass
class MediaFile:
    # Source fields (set on load, never changed)
    filepath: str
    filename: str
    ext: str
    is_video: bool
    is_already_formatted: bool
    date: Optional[datetime]
    date_source: str
    stripped_filename: str

    # Computed / live fields (updated by recalculate_proposed_filenames)
    proposed_filename: str = ''
    is_interpolated: bool = False
    is_re_anchored: bool = False
    needs_attention: bool = False
    selected: bool = False

    # The date this file will have after recalc — equals f.date for in-order
    # anchored files, the averaged value for re-anchored/interpolated files,
    # and the filename-parsed date for already-formatted files. Used so that
    # neighbour-anchor lookups don't return stale source dates.
    effective_date: Optional[datetime] = None

    # Set to True when the user explicitly moves this file via the chevrons
    # or toolbar. Cleared on apply_rename. Strong-source files are only ever
    # re-anchored when this is True — if the user hasn't touched a file, its
    # own metadata/filename date is always used unchanged.
    user_moved: bool = False

    # Async-loaded fields
    thumbnail: Optional[QPixmap] = field(default=None, repr=False)
    duration_seconds: Optional[int] = None

    # False until the thumbnail worker has finished with this file, whether
    # or not it produced an image — lets the UI tell "still loading" (pulsing
    # placeholder) apart from "no thumbnail available" (static placeholder).
    thumbnail_loaded: bool = False

    # User-typed override of proposed_filename, from the editable New
    # filename cell. None means "use the auto-computed name". Kept separate
    # from proposed_filename (rather than overwriting it) so the auto value
    # stays correct underneath — recalculate_proposed_filenames() never
    # looks at this field, and resetting it (the "x" button) needs no
    # recompute. Cleared on Apply (baked into the new f.filename) and
    # whenever the file's date is edited (a stale manual name from before
    # a date correction would no longer match).
    manual_filename: Optional[str] = None

    # This file's position the last time _sort_by_filename() ran (initial
    # load, or the resort after an Apply) — stamped there, never touched
    # anywhere else. It's the "undo move" baseline: restoring a moved weak
    # file to where it was is only ever meant to mean "since the last
    # Apply," not "since the folder was first opened," so this is reset
    # exactly when that baseline should move forward. See
    # MediaTableModel.undo_move().
    original_index: int = 0

    # (date, date_source, is_already_formatted) from just before the first
    # date-picker pick since the last Apply/load — None if no date has been
    # picked since then. Unlike a drag, picking a date overwrites real data
    # (and can demote a hard anchor), so undoing it needs this snapshot to
    # restore, not just a position to move back to. Set once and left alone
    # across repeated picks on the same file, so "x" always undoes back to
    # the true original rather than one step at a time. See
    # MediaTableModel.undo_manual_date().
    manual_date_undo: Optional[tuple] = None

    def __post_init__(self):
        if not self.proposed_filename:
            self.proposed_filename = self.filename

    @property
    def display_filename(self) -> str:
        """What to show and rename to — the manual override if the user set
        one, otherwise the auto-computed proposed_filename."""
        return self.manual_filename if self.manual_filename is not None else self.proposed_filename

    @property
    def gates_phase1(self) -> bool:
        """True for a strong anchor still waiting to be renamed — the files
        that hold the app in Phase 1 (Move buttons disabled) until Apply.
        The single definition of that test: MediaTableModel.has_pending_strong_renames()
        and the attention button / status counts in main.py use it directly,
        and can_clear_filename builds on it. Add a strong date source here
        and every one of them follows."""
        return (not self.is_already_formatted
                and self.date_source in (DATE_SOURCE_METADATA, DATE_SOURCE_FILENAME,
                                         DATE_SOURCE_MANUAL)
                and not self.user_moved)

    @property
    def can_clear_filename(self) -> bool:
        """Whether the New filename field's "x" is shown right now — the one
        rule both its painting and its click handling use.

        Whenever the box holds a real name (not blank, not a '---'
        instruction placeholder): with nothing there, there's nothing to
        clear. Clicking it always clears to blank, which means skip this
        file on Apply — uniform across every state, since there's no longer
        a Move-button gate a blank strong anchor could get stuck behind."""
        name = self.display_filename
        return name != '' and not name.startswith('---')


# ── Worker signals ──────────────────────────────────────────────────────────
# Each worker owns its own signals instance to avoid garbage-collection
# issues with shared signal objects in QThreadPool.
class ThumbnailSignals(QObject):
    # Always emitted once per file, with qimage=None if generation failed —
    # so the row can leave its "loading" state either way.
    thumb_ready = Signal(int, object, int, str)  # (index, QImage | None, generation, filepath)


class BatchSignals(QObject):
    chunk_ready = Signal(int, object, int)  # (start_index, list[dict], generation)
    error       = Signal(str, int)          # (message, generation)
    finished    = Signal(int)               # (generation)


# ── Metadata batch worker ───────────────────────────────────────────────────
# A single persistent exiftool process parses files serially (~13s for 2000
# files), so load_folder() shards the file list across several of these
# workers, each with its own persistent process (~3s). Few process launches,
# and real parallelism. Each shard is a contiguous slice; start_index is its
# offset into the full file list.
class MetadataBatchWorker(QRunnable):

    def __init__(self, filepaths: List[str], generation: int, start_index: int = 0):
        super().__init__()
        self.filepaths   = filepaths
        self.generation  = generation
        self.start_index = start_index
        self.signals     = BatchSignals()
        self._cancelled  = False
        self.setAutoDelete(True)

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return  # cancelled before a thread even picked this up
        try:
            read_metadata_batch(
                self.filepaths,
                on_chunk=lambda start, results:
                    self.signals.chunk_ready.emit(self.start_index + start, results, self.generation),
                is_cancelled=lambda: self._cancelled,
            )
        except Exception as e:
            self.signals.error.emit(str(e), self.generation)
        finally:
            self.signals.finished.emit(self.generation)


# ── Thumbnail worker ────────────────────────────────────────────────────────
# One instance per file, still parallel across the thread pool — decoupled
# from metadata reading so a folder full of videos doesn't serialize
# thumbnail generation behind (or in front of) the metadata batch.
class ThumbnailWorker(QRunnable):

    THUMB_W = 160  # always load at expanded size — delegates scale down for compact
    THUMB_H = 120

    def __init__(self, index: int, filepath: str, is_video: bool, generation: int,
                 current_generation_ref: List[int]):
        super().__init__()
        self.index      = index
        self.filepath   = filepath
        self.is_video   = is_video
        self.generation = generation
        # Shared with MediaTableModel: current_generation_ref[0] is the
        # model's live _load_generation. A worker queued for an abandoned
        # load can sit behind thousands of others before a thread picks it
        # up — checking this before doing any decode work means a reload
        # doesn't leave stale work competing for CPU with the new load.
        self._current_generation_ref = current_generation_ref
        self.signals    = ThumbnailSignals()
        self.setAutoDelete(True)

    def run(self):
        if self._current_generation_ref[0] != self.generation:
            return  # superseded by a newer load — nothing to do, nothing to emit
        qimage = self._make_thumbnail(self.filepath, self.is_video)
        self.signals.thumb_ready.emit(self.index, qimage, self.generation, self.filepath)

    def _make_thumbnail(self, filepath: str, is_video: bool) -> Optional[QImage]:
        try:
            if is_video:
                return self._video_thumbnail(filepath)
            else:
                return self._image_thumbnail(filepath)
        except Exception:
            return None

    def _image_thumbnail(self, filepath: str) -> Optional[QImage]:
        """Return a QImage — must not create QPixmap on a worker thread."""
        from PIL import Image, ImageOps
        img = Image.open(filepath)
        ImageOps.exif_transpose(img, in_place=True)
        img.thumbnail((self.THUMB_W, self.THUMB_H), Image.LANCZOS)
        img = img.convert('RGB')
        data = img.tobytes('raw', 'RGB')
        # Keep 'data' alive by storing it on the QImage
        qimg = QImage(data, img.width, img.height, img.width * 3,
                      QImage.Format_RGB888)
        qimg._keep_alive = data  # prevents garbage collection
        return qimg

    def _video_thumbnail(self, filepath: str) -> Optional[QImage]:
        """Extract first frame via ffmpeg into a persistent temp file."""
        import subprocess
        ffmpeg = _vendor_path('ffmpeg.exe')
        # Use a named temp file that persists until we explicitly delete it
        import tempfile, os
        fd, out = tempfile.mkstemp(suffix='.jpg')
        os.close(fd)
        try:
            subprocess.run(
                [ffmpeg, '-y', '-i', filepath,
                 '-ss', '00:00:01',
                 '-vframes', '1',
                 '-q:v', '4',
                 out],
                capture_output=True, timeout=15,
                creationflags=CREATE_NO_WINDOW
            )
            if os.path.getsize(out) > 0:
                img = self._image_thumbnail(out)
                return img
        except Exception:
            pass
        finally:
            try:
                os.unlink(out)
            except Exception:
                pass
        return None


# ── Interpolation helpers ───────────────────────────────────────────────────

def _ordering_date(f: MediaFile) -> Optional[datetime]:
    """
    Date to use for chronological-order comparisons. For already-formatted
    files the filename prefix is the source of truth — EXIF may be in a
    different timezone and would give false out-of-order readings.
    """
    if f.is_already_formatted:
        m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', f.filename)
        if m:
            g = m.groups()
            try:
                return datetime(int(g[0]), int(g[1]), int(g[2]),
                                int(g[3]), int(g[4]), int(g[5]))
            except ValueError:
                pass
    return f.date


def _is_strong(f: MediaFile) -> bool:
    """True if this file has a trustworthy, user-visible date (not just mtime)."""
    return f.is_already_formatted or bool(
        f.date and f.date_source not in (DATE_SOURCE_NONE, DATE_SOURCE_MODIFIED)
    )


def _find_anchor_before(files: List[MediaFile], idx: int) -> Optional[datetime]:
    """
    Find the nearest anchor before idx and return its effective date.
    Only hard anchors and unmoved strong anchors qualify — moved files of any
    kind are excluded because their position is user-overridden and must not
    be used to derive timestamps for other files.
    """
    for i in range(idx - 1, -1, -1):
        f = files[i]
        if f.needs_attention or f.user_moved:
            continue
        if f.effective_date is not None:
            return f.effective_date
        if _is_strong(f):
            return _ordering_date(f)
    return None


def _find_anchor_after(files: List[MediaFile], idx: int) -> Optional[datetime]:
    """Find the nearest anchor after idx. See _find_anchor_before."""
    for i in range(idx + 1, len(files)):
        f = files[i]
        if f.needs_attention or f.user_moved:
            continue
        if f.effective_date is not None:
            return f.effective_date
        if _is_strong(f):
            return _ordering_date(f)
    return None


def _interpolate(dt_a: datetime, dt_b: datetime,
                 numerator: int, denominator: int) -> datetime:
    """Return a timestamp evenly spaced between dt_a and dt_b."""
    delta  = (dt_b - dt_a) / denominator
    result = dt_a + delta * numerator
    return result.replace(microsecond=0)


def _extrapolate_before(dt_a: datetime, dt_b: datetime) -> datetime:
    """Extrapolate a timestamp before dt_a using the delta a→b."""
    return (dt_a - (dt_b - dt_a)).replace(microsecond=0)


def _extrapolate_after(dt_a: datetime, dt_b: datetime) -> datetime:
    """Extrapolate a timestamp after dt_b using the delta a→b."""
    return (dt_b + (dt_b - dt_a)).replace(microsecond=0)


def _is_in_chronological_order(files: List[MediaFile], idx: int) -> bool:
    """
    Return True if the dated file at idx is in chronological order
    relative to its nearest dated neighbours.
    """
    f = files[idx]
    od = _ordering_date(f)
    if od is None:
        return True

    before = _find_anchor_before(files, idx)
    after  = _find_anchor_after(files, idx)

    if before and od < before:
        return False
    if after and od > after:
        return False
    return True


# ── Main table model ────────────────────────────────────────────────────────
class MediaTableModel(QAbstractTableModel):
    """
    Qt table model holding the list of MediaFile objects.

    Key design decisions:
    - load_folder() populates stub rows immediately, fills metadata async
    - recalculate_proposed_filenames() runs after every reorder
    - apply_rename() is the only function that touches files on disk
    - No undo for MVP, but mutations are clean enough to add it later
    """

    folder_load_started  = Signal(int)      # emits file count
    folder_load_complete = Signal()         # all metadata read — UI is usable
    thumbnails_complete  = Signal()         # every thumbnail attempted (may be after the above)
    file_progress        = Signal(int, int) # metadata files done, total
    attention_required   = Signal(int)      # emits count of flagged files
    rename_progress      = Signal(int, int) # done, total
    rename_complete      = Signal(int, int) # success_count, error_count
    phase_changed        = Signal(bool)     # True = Phase 1, False = Phase 2
    metadata_load_error  = Signal(str)      # exiftool could not start at all

    def __init__(self, parent=None):
        super().__init__(parent)
        self._files: List[MediaFile] = []
        self._pool  = QThreadPool.globalInstance()
        self._is_phase1 = False

        # Loading state for the current folder load. _load_generation is
        # bumped on every load_folder() call; workers from a superseded load
        # carry the old generation and are ignored by the callbacks below —
        # this is what makes it safe to load a new folder while a previous
        # one is still loading, instead of splicing stale rows into the
        # new list.
        self._load_generation      = 0
        # Shared with every ThumbnailWorker so a queued-but-not-yet-started
        # worker from an abandoned load can tell it's stale before doing any
        # decode work, without needing a reference back to the model itself.
        self._current_generation_ref: List[int] = [0]
        self._total_files          = 0
        self._metadata_done_count  = 0
        self._metadata_error_shown = False
        self._pending_thumbnails   = 0
        self._pending_metadata_shards = 0
        self._current_batch_workers: List[MetadataBatchWorker] = []

        # QThreadPool.start() does not keep a Python reference to the
        # runnable — once run() returns, nothing stops the GC from
        # collecting the worker (and its unparented `signals` QObject)
        # before a still-queued cross-thread signal has been delivered to
        # the main thread, which surfaces as "RuntimeError: Signal source
        # has been deleted" and silently drops whatever that emit carried.
        # Keeping every in-flight worker referenced here for the life of
        # its generation is what prevents that.
        self._active_workers: List[QRunnable] = []

    # ── Qt model interface ───────────────────────────────────────────────────

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._files)

    def columnCount(self, parent=QModelIndex()) -> int:
        return COLUMN_COUNT

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if row >= len(self._files):
            return None
        f = self._files[row]

        if role == Qt.DisplayRole:
            if col == COL_ORDER:
                return str(row + 1)
            if col == COL_FILENAME:
                return f.filename
            if col == COL_DATE:
                dt = f.effective_date or f.date
                if dt:
                    return dt.strftime('%Y-%m-%d %H:%M:%S')
                return 'No date found'
            if col == COL_PREVIEW:
                return f.display_filename

        if role == Qt.CheckStateRole and col == COL_CHECK:
            return Qt.Checked if f.selected else Qt.Unchecked

        if role == Qt.DecorationRole and col == COL_THUMB:
            return f.thumbnail

        if role == Qt.BackgroundRole:
            alt = bool(row % 2)
            # Amber marks "needs a decision" (weak, unmoved, no value) —
            # the only row state that's not already self-explanatory from
            # the New filename box's own contents/colour. Every other row,
            # hard anchor or not, gets the plain alternating background.
            if f.needs_attention:
                return QColor('#FFFBEB' if alt else '#FEF3C7')
            return QColor('#FAFAFA' if alt else '#FFFFFF')

        # Custom roles used by delegates
        if role == MediaFileRole:      return f
        if role == DateSourceRole:     return f.date_source
        if role == IsInterpolatedRole: return f.is_interpolated
        if role == IsReAnchoredRole:   return f.is_re_anchored
        if role == NeedsAttentionRole: return f.needs_attention

        return None

    def setData(self, index: QModelIndex, value, role=Qt.EditRole) -> bool:
        if not index.isValid():
            return False
        row, col = index.row(), index.column()
        f = self._files[row]

        if role == Qt.CheckStateRole and col == COL_CHECK:
            f.selected = (value == Qt.Checked)
            self.dataChanged.emit(index, index, [role])
            return True

        if role == Qt.EditRole and col == COL_PREVIEW:
            # value is passed through as-is, not "value or None" — '' and
            # None are different requests (skip this file vs. no override
            # at all) and collapsing them here would silently turn every
            # "clear to skip" into a "revert to auto" instead. See
            # set_manual_filename.
            self.set_manual_filename(row, value)
            return True

        if role == Qt.EditRole and col == COL_DATE:
            self.set_manual_date(row, value)
            return True

        return False

    def flags(self, index: QModelIndex):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        col = index.column()
        if col == COL_CHECK:
            base |= Qt.ItemIsUserCheckable
        elif col == COL_PREVIEW:
            row = index.row()
            # Hard anchors are already renamed — the filename is the source
            # of truth, so it isn't editable: letting it be edited would
            # violate "hard anchor never renames". (The Date taken column
            # isn't an editor at all — its calendar icon opens a popup that
            # calls setData directly; see MediaTableView.)
            if row < len(self._files) and not self._files[row].is_already_formatted:
                base |= Qt.ItemIsEditable
        return base

    def set_manual_filename(self, row: int, name: Optional[str]):
        """Set a per-file override of the proposed filename, from the
        editable New filename field or its clear ('x') icon (which just
        sends '').

        name=None means "no override" — display falls back to the
        auto-computed proposed_filename (see display_filename). Any other
        string is an explicit override and is kept as typed. '' in
        particular means "skip this file on Apply" (apply_rename()'s
        pending filter already excludes an empty display_filename) —
        uniformly, for every non-hard-anchor file: there's no longer a
        Move-button gate a blank strong anchor could get stuck behind (see
        MainWindow._move), so there's nothing left to protect it from.

        Doesn't touch proposed_filename itself, so clearing back to None
        needs no recompute — that's the whole point of keeping the two
        separate."""
        if row < 0 or row >= len(self._files):
            return
        f = self._files[row]
        name = name.strip() if name is not None else None
        if f.manual_filename == name:
            return
        f.manual_filename = name
        idx = self.index(row, COL_PREVIEW)
        self.dataChanged.emit(idx, idx, [Qt.DisplayRole])

    def set_manual_date(self, row: int, dt: datetime):
        """Apply a user-entered date/time from the Date taken date-time
        picker. Treated as a strong anchor (see _is_strong) sourced from
        the user rather than metadata — for files like WhatsApp/shared
        media whose embedded metadata is wrong. Clears user_moved, since
        the chosen date is now the authoritative position — and, being
        not-moved, it still counts as a real anchor for its neighbours'
        interpolation, unlike a dragged file. Clears any manual filename
        override too, since it would otherwise show a stale name.

        Also clears is_already_formatted, demoting a hard anchor back to an
        ordinary strong anchor. A hard anchor is only trustworthy because
        renaming stamped it with a real date — but that date can itself be
        wrong (WhatsApp/iOS-shared media whose metadata reflects the share
        date, not capture), and "hard anchor never renames" would otherwise
        make that permanent with no way back. Demoting it re-admits it to
        Pass 1 as a strong-anchor-not-moved file: build_new_filename strips
        the old (wrong) YYYYMMDD_HHMMSS prefix and proposes a new one from
        the corrected date, and the New filename box reappears (it's driven
        by the same flag) so it can be renamed for real on the next Apply.

        Also relocates the file to sit chronologically among the others,
        the same as if the user had dragged it there — Pass 1 treats a
        not-moved strong anchor as already trustworthy and already in a
        reasonable position, an assumption a picked date shouldn't leave
        false. See _reposition_by_date().

        Before any of that, snapshots (date, date_source, is_already_formatted)
        into manual_date_undo, but only if nothing's snapshotted there
        already — a second pick on the same file (before the next Apply)
        shouldn't overwrite the snapshot with the *first* pick's result,
        or "x" would only ever undo one step instead of back to how the
        file actually started. See undo_manual_date()."""
        if row < 0 or row >= len(self._files) or dt is None:
            return
        f = self._files[row]
        if f.manual_date_undo is None:
            f.manual_date_undo = (f.date, f.date_source, f.is_already_formatted)
        f.date                 = dt
        f.date_source          = DATE_SOURCE_MANUAL
        f.user_moved           = False
        f.manual_filename      = None
        f.is_already_formatted = False
        self._reposition_by_date(row, dt)   # emits its own layoutChanged
        self.recalculate_proposed_filenames()

    def _reposition_by_date(self, row: int, dt: datetime):
        """Move self._files[row] to sit between the two files whose current
        displayed date brackets dt — same idea as a manual drag, just
        driven by the picked date instead of a chevron click. Only hard
        anchors and strong-sourced files (_is_strong) count as reference
        points: a weak file's displayed date is either nonexistent
        (needs_attention) or itself interpolated from its neighbours, so
        it's not a trustworthy place to measure from — the same reasoning
        _find_anchor_before/_after already apply when picking anchors.

        Finds the qualifying anchor with the *smallest* date that's still
        later than dt, not just the first one encountered in list order:
        the list isn't guaranteed to already be in chronological order (a
        fresh alphabetical load, for instance, routinely isn't), so
        stopping at the first later-dated anchor can land on one that's
        much further away than the true nearest one, which might sit
        later in the list.

        Wrapped in layoutAboutToBeChanged/_remap_persistent_indices/
        layoutChanged rather than a bare layoutChanged.emit(), so the
        selection model's own bookkeeping travels with the file instead of
        being left pointing at whatever row number it used to be — see
        _remap_persistent_indices for what goes wrong without this."""
        self.layoutAboutToBeChanged.emit()
        f = self._files.pop(row)
        insert_at = len(self._files)
        best_dt = None
        for i, other in enumerate(self._files):
            if not _is_strong(other):
                continue
            other_dt = other.effective_date or other.date
            if other_dt is not None and other_dt > dt and (best_dt is None or other_dt < best_dt):
                best_dt = other_dt
                insert_at = i
        self._files.insert(insert_at, f)
        self._remap_persistent_indices(row, insert_at)
        self.layoutChanged.emit()

    # ── Public API ───────────────────────────────────────────────────────────

    def load_folder(self, folder_path: str):
        # Cancel any load already in flight. Workers from that generation
        # keep running to completion (a chunk in progress can't be
        # interrupted mid-call), but their results are now stale and get
        # dropped by the generation check in the callbacks below — nothing
        # from the old folder can land in the new file list.
        for worker in self._current_batch_workers:
            worker.cancel()
        self._current_batch_workers = []
        self._load_generation += 1
        generation = self._load_generation
        self._current_generation_ref[0] = generation
        # Drop any not-yet-started runnables still queued from the old
        # generation — safe since this app is the only user of the global
        # pool. Ones already running can't be interrupted (see
        # ThumbnailWorker.run() and MetadataBatchWorker.run()'s early-exit
        # checks for how those avoid wasted work instead).
        self._pool.clear()
        # Deliberately not clearing _active_workers here: an old-generation
        # worker may still be mid-run() (cancel() only stops new work from
        # starting), and dropping its Python reference while a queued
        # cross-thread signal from it is still in flight would reintroduce
        # the exact "Signal source has been deleted" race this list exists
        # to prevent. It stays a permanent keep-alive registry for the life
        # of the model — negligible memory cost for what it buys.

        self.beginResetModel()
        self._files = []
        self.endResetModel()

        path = Path(folder_path)
        filepaths = sorted([          # sorted() gives filename order
            str(p) for p in path.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        ])

        self._total_files           = len(filepaths)
        self._metadata_done_count   = 0
        self._metadata_error_shown  = False
        self._pending_thumbnails    = len(filepaths)

        if not filepaths:
            self.folder_load_started.emit(0)
            self.folder_load_complete.emit()
            self.thumbnails_complete.emit()
            return

        self.folder_load_started.emit(len(filepaths))

        self.beginInsertRows(QModelIndex(), 0, len(filepaths) - 1)
        for fp in filepaths:
            stub = MediaFile(
                filepath             = fp,
                filename             = Path(fp).name,
                ext                  = Path(fp).suffix.lower(),
                is_video             = Path(fp).suffix.lower() in {'.mp4', '.mov', '.avi'},
                is_already_formatted = False,
                date                 = None,
                date_source          = DATE_SOURCE_NONE,
                stripped_filename    = Path(fp).name,
            )
            self._files.append(stub)
        self.endInsertRows()

        # Shard metadata reading across several concurrent persistent exiftool
        # processes — see the MetadataBatchWorker comment for why a single
        # process can't use more than one CPU thread.
        shard_count = min(self._pool.maxThreadCount(), len(filepaths))
        shard_size  = -(-len(filepaths) // shard_count)  # ceil division

        self._pending_metadata_shards = 0
        for shard_start in range(0, len(filepaths), shard_size):
            shard_files = filepaths[shard_start:shard_start + shard_size]
            batch_worker = MetadataBatchWorker(shard_files, generation, start_index=shard_start)
            batch_worker.signals.chunk_ready.connect(self._on_metadata_chunk_ready)
            batch_worker.signals.error.connect(self._on_metadata_error)
            batch_worker.signals.finished.connect(self._on_metadata_shard_finished)
            self._current_batch_workers.append(batch_worker)
            self._active_workers.append(batch_worker)
            self._pending_metadata_shards += 1
            self._pool.start(batch_worker)

        for i, fp in enumerate(filepaths):
            is_video = Path(fp).suffix.lower() in {'.mp4', '.mov', '.avi'}
            thumb_worker = ThumbnailWorker(i, fp, is_video, generation, self._current_generation_ref)
            thumb_worker.signals.thumb_ready.connect(self._on_thumb_ready)
            self._active_workers.append(thumb_worker)
            self._pool.start(thumb_worker)

    def move_rows(self, indices: List[int], direction: int):
        """
        Move selected rows up (direction=-1) or down (direction=1) as a group,
        preserving relative order within the group.
        """
        if not indices:
            return
        indices = sorted(set(indices))

        if direction == -1 and indices[0] == 0:
            return
        if direction == 1 and indices[-1] == len(self._files) - 1:
            return

        if direction == -1:
            for i in indices:
                self._files[i - 1], self._files[i] = \
                    self._files[i], self._files[i - 1]
            moved_positions = [i - 1 for i in indices]
        else:
            for i in reversed(indices):
                self._files[i], self._files[i + 1] = \
                    self._files[i + 1], self._files[i]
            moved_positions = [i + 1 for i in indices]

        for pos in moved_positions:
            self._files[pos].user_moved = True

        self.layoutChanged.emit()
        self.recalculate_proposed_filenames()

    def _remap_persistent_indices(self, from_row: int, to_row: int):
        """Tell Qt how row numbers just shifted after moving one file from
        from_row to to_row in self._files, so persistent indices — the
        selection model's own bookkeeping, the current index, any open
        editor — travel with their items instead of being left pointing at
        whatever row number they used to be. Call after mutating
        self._files, bracketed by layoutAboutToBeChanged/layoutChanged.

        This matters: a bare layoutChanged.emit() after a manual pop+insert
        (which _reinsert_at_original_index and _reposition_by_date used to
        do) leaves the selection model's persistent indices referencing
        their old row numbers. Whatever file now happens to sit at each of
        those row numbers reads back as "selected" even though nobody
        selected it — this is what caused several unrelated files to show
        up selected (blue border, checked box) after a single-row undo."""
        if from_row == to_row:
            return
        if from_row < to_row:
            remap = {r: r - 1 for r in range(from_row + 1, to_row + 1)}
        else:
            remap = {r: r + 1 for r in range(to_row, from_row)}
        remap[from_row] = to_row
        old_list = self.persistentIndexList()
        new_list = [self.index(remap.get(idx.row(), idx.row()), idx.column())
                    for idx in old_list]
        self.changePersistentIndexList(old_list, new_list)

    def _reinsert_at_original_index(self, row: int):
        """Move self._files[row] to the position its original_index implies
        relative to the other *unmoved* files. A file some other move has
        also displaced isn't a reliable reference point for where it used
        to be, so it's skipped when looking for the insertion point (though
        it isn't touched — it just isn't used to decide where this one
        lands). Shared by undo_move() and undo_manual_date(), whose only
        difference is what else about the file they restore before
        repositioning it."""
        self.layoutAboutToBeChanged.emit()
        f = self._files.pop(row)
        insert_at = len(self._files)
        for i, other in enumerate(self._files):
            if other.user_moved:
                continue
            if other.original_index > f.original_index:
                insert_at = i
                break
        self._files.insert(insert_at, f)
        self._remap_persistent_indices(row, insert_at)
        self.layoutChanged.emit()

    def undo_move(self, row: int):
        """Put a moved weak file back where it was (since the last Apply or
        load), without disturbing any other file's position — the "x" on an
        interpolated (weak, moved) file's New filename box calls this
        instead of just blanking it, since for this state "undo" means undo
        the move that produced the interpolated name in the first place.

        Only ever called on is_interpolated files (weak + moved); a moved
        strong anchor's re-anchoring is a different flag (is_re_anchored)
        and isn't affected by this."""
        if row < 0 or row >= len(self._files):
            return
        f = self._files[row]
        if not (f.is_interpolated and f.user_moved):
            return
        f.user_moved = False
        f.manual_filename = None
        self._reinsert_at_original_index(row)
        self.recalculate_proposed_filenames()

    def undo_manual_date(self, row: int):
        """Undo a date-picker correction — the "x" on a file whose date came
        from the picker calls this instead of just blanking it. Blanking
        would leave the file in an odd, not-really-useful state: a
        corrected date and badge with no proposed name to go with them.
        What "undo" should mean here is backing out of the correction
        entirely, the same as it does for a dragged file.

        Unlike undo_move, this doesn't require user_moved — set_manual_date
        deliberately leaves it False, so a corrected date still counts as a
        real anchor for its neighbours' interpolation instead of being
        treated as suspect the way a dragged file is (see
        MediaFile.manual_date_undo). Restores date/date_source/
        is_already_formatted from that snapshot, then repositions the file
        back the same way undo_move does."""
        if row < 0 or row >= len(self._files):
            return
        f = self._files[row]
        if f.manual_date_undo is None:
            return
        f.date, f.date_source, f.is_already_formatted = f.manual_date_undo
        f.manual_date_undo = None
        f.manual_filename = None
        self._reinsert_at_original_index(row)
        self.recalculate_proposed_filenames()

    def get_selected_indices(self) -> List[int]:
        return [i for i, f in enumerate(self._files) if f.selected]

    def select_all(self, selected: bool):
        for f in self._files:
            f.selected = selected
        if self._files:
            tl = self.index(0, COL_CHECK)
            br = self.index(len(self._files) - 1, COL_CHECK)
            self.dataChanged.emit(tl, br, [Qt.CheckStateRole])

    def recalculate_proposed_filenames(self):
        """
        Recalculate proposed_filename, is_interpolated, is_re_anchored,
        and needs_attention for every file. Three passes:
          1. Classify each file
          2. Group interpolation for runs of undated files
          3. Collision resolution
        """
        files       = self._files
        n           = len(files)
        if n == 0:
            return
        placeholder = _PLACEHOLDER_NEEDS_ATTENTION

        # Reset effective_date so anchor lookups during this pass only see
        # values set in this pass (forward neighbours fall back to f.date).
        for f in files:
            f.effective_date = None

        # ── Pass 1: classify each file ───────────────────────────────────────
        # Rule: strong-source files (metadata / filename / already_formatted)
        # are NEVER re-anchored unless the user explicitly moved them.
        # Weak-source files (date_modified / none) are always interpolated
        # from the nearest strong anchors — their mtime is ignored entirely.
        for i, f in enumerate(files):
            f.is_interpolated = False
            f.is_re_anchored  = False
            f.needs_attention = False

            if _is_strong(f):
                own_dt = _ordering_date(f) if f.is_already_formatted else f.date

                if not f.user_moved:
                    # Untouched strong file — always use its own date.
                    if f.is_already_formatted:
                        f.proposed_filename = f.filename
                    else:
                        # force=True: f.filename can still look formatted
                        # here (a hard anchor just demoted by
                        # set_manual_date, ahead of its actual on-disk
                        # rename) — without it, build_new_filename's own
                        # already-formatted guard would hand back the old,
                        # wrong-dated name unchanged.
                        f.proposed_filename = build_new_filename(
                            f.filename, own_dt, is_interpolated=False, force=True)
                    f.effective_date = own_dt

                else:
                    # User moved this file — fit it to its new position.
                    in_order = _is_in_chronological_order(files, i)
                    if in_order:
                        # Own date still fits — keep it, no badge change.
                        if f.is_already_formatted:
                            f.proposed_filename = f.filename
                        else:
                            f.proposed_filename = build_new_filename(
                                f.filename, own_dt, is_interpolated=False, force=True)
                        f.effective_date = own_dt
                    else:
                        # Out of order — average between neighbours.
                        before = _find_anchor_before(files, i)
                        after  = _find_anchor_after(files, i)
                        if before and after:
                            dt = (before + (after - before) / 2).replace(microsecond=0)
                        elif before:
                            dt = before + timedelta(seconds=60)
                        elif after:
                            dt = after - timedelta(seconds=60)
                        else:
                            dt = own_dt
                        f.is_re_anchored = True
                        f.proposed_filename = build_new_filename(
                            f.filename, dt, is_interpolated=True, force=True)
                        f.effective_date = dt

            else:
                # Weak/no source — no rename until the user moves the file.
                # date_modified reflects download time, not capture time.
                # Moved weak files get interpolated in Pass 2.
                if f.user_moved:
                    f.is_interpolated = True  # resolved in Pass 2
                else:
                    f.needs_attention   = True
                    f.proposed_filename = placeholder

        # ── Pass 2: group interpolation ──────────────────────────────────────
        # Only processes is_interpolated=True files (moved weak/strong files).
        # needs_attention files are left entirely alone here.
        i = 0
        while i < n:
            f = files[i]
            if f.is_interpolated:
                run_start = i
                while i < n and files[i].is_interpolated:
                    i += 1
                run_end  = i
                run      = files[run_start:run_end]
                before_dt = _find_anchor_before(files, run_start)
                after_dt  = _find_anchor_after(files, run_end - 1)
                denom     = len(run) + 1

                for j, rf in enumerate(run):
                    if before_dt and after_dt:
                        dt = _interpolate(before_dt, after_dt, j + 1, denom)

                    elif before_dt and not after_dt:
                        second = (_find_anchor_before(files, run_start - 1)
                                  if run_start > 0 else None)
                        if second:
                            base = _extrapolate_after(second, before_dt)
                        else:
                            base = before_dt
                        dt = base + timedelta(seconds=60) * (j + 1)

                    elif after_dt and not before_dt:
                        second = (_find_anchor_after(files, run_end)
                                  if run_end < n else None)
                        if second:
                            base = _extrapolate_before(after_dt, second)
                        else:
                            base = after_dt
                        dt = base - timedelta(seconds=60) * (denom - j - 1)

                    else:
                        rf.needs_attention   = True
                        rf.is_interpolated   = False
                        rf.proposed_filename = placeholder
                        continue

                    rf.proposed_filename = build_new_filename(
                        rf.filename, dt, is_interpolated=True)
                    rf.effective_date = dt
            else:
                i += 1

        # ── Pass 3: collision resolution ─────────────────────────────────────
        seen: dict = {}
        for f in files:
            name = f.proposed_filename
            if name.startswith('---') or name == f.filename:
                continue
            seen.setdefault(name, []).append(f)

        for name, group in seen.items():
            if len(group) > 1:
                stem = Path(name).stem
                ext  = Path(name).suffix
                for k, gf in enumerate(group):
                    gf.proposed_filename = f'{stem}_{k + 1:02d}{ext}'

        if files:
            tl = self.index(0, 0)
            br = self.index(n - 1, COLUMN_COUNT - 1)
            self.dataChanged.emit(tl, br,
                                  [Qt.DisplayRole, Qt.BackgroundRole,
                                   IsInterpolatedRole, IsReAnchoredRole,
                                   NeedsAttentionRole])

        attention_count = sum(1 for f in files if f.needs_attention)
        self.attention_required.emit(attention_count)
        self._is_phase1 = self.has_pending_strong_renames()
        self.phase_changed.emit(self._is_phase1)

    def apply_rename(self):
        """
        Rename files on disk based on display_filename (the manual override
        if one is set, otherwise the auto-computed proposed_filename).
        Checks existence before renaming, collects errors without stopping.
        Writes metadata timestamp for interpolated files, and for files
        with a manually-entered date — the point of a manual date is
        usually that the file's own metadata is wrong (e.g. WhatsApp/shared
        files carry the share date), so Apply corrects it on disk too.
        """
        exiftool_path = _vendor_path('exiftool.exe')
        success = 0
        errors  = 0

        pending = [f for f in self._files
                   if f.display_filename != f.filename
                   and not f.display_filename.startswith('---')
                   and f.display_filename != '']
        total = len(pending)

        for done, f in enumerate(pending, 1):
            src  = Path(f.filepath)
            dest = src.parent / f.display_filename
            # Captured before the filename-parse below can overwrite
            # date_source to 'filename'.
            write_metadata = f.is_interpolated or f.date_source == DATE_SOURCE_MANUAL

            if not src.exists():
                errors += 1
            else:
                try:
                    src.rename(dest)
                    f.filepath  = str(dest)
                    f.filename  = f.display_filename

                    m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})',
                                 f.filename)
                    if m:
                        g = m.groups()
                        try:
                            f.date = datetime(
                                int(g[0]), int(g[1]), int(g[2]),
                                int(g[3]), int(g[4]), int(g[5]))
                            f.date_source = DATE_SOURCE_FILENAME
                        except ValueError:
                            pass

                    if write_metadata:
                        self._write_metadata_date(str(dest), f, exiftool_path)

                    f.is_already_formatted = True
                    f.is_interpolated      = False
                    f.is_re_anchored       = False
                    f.user_moved           = False
                    f.manual_filename      = None
                    f.proposed_filename    = f.filename
                    f.effective_date       = f.date
                    success += 1

                except Exception:
                    errors += 1

            self.rename_progress.emit(done, total)
            QCoreApplication.processEvents()

        if self._files:
            tl = self.index(0, 0)
            br = self.index(len(self._files) - 1, COLUMN_COUNT - 1)
            self.dataChanged.emit(tl, br)

        self._is_phase1 = self.has_pending_strong_renames()
        self.phase_changed.emit(self._is_phase1)
        self.rename_complete.emit(success, errors)

    def _write_metadata_date(self, filepath: str, f: MediaFile,
                              exiftool_path: str):
        """Write the new timestamp back to file metadata via exiftool.
        Called for interpolated files and manually-dated files (see
        apply_rename). f.filename is already the new name at this point —
        parsed from there, not proposed_filename, so this reflects a
        manual filename override too, not just the auto-computed one."""
        import re, subprocess
        m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})',
                     f.filename)
        if not m:
            return
        g      = m.groups()
        dt_str = f'{g[0]}:{g[1]}:{g[2]} {g[3]}:{g[4]}:{g[5]}'
        tags   = [
            '-DateTimeOriginal=' + dt_str,
            '-CreateDate='       + dt_str,
            '-ModifyDate='       + dt_str,
            '-overwrite_original',
        ]
        try:
            import subprocess
            subprocess.run([exiftool_path] + tags + [filepath],
                           capture_output=True, timeout=15,
                           creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass

    def has_pending_renames(self) -> bool:
        return any(
            f.display_filename != f.filename and
            not f.display_filename.startswith('---') and f.display_filename != ''
            for f in self._files
        )

    def has_pending_strong_renames(self) -> bool:
        return any(f.gates_phase1 for f in self._files)

    def attention_count(self) -> int:
        return sum(1 for f in self._files if f.needs_attention)

    def files(self) -> List[MediaFile]:
        return self._files

    # ── Worker callbacks ─────────────────────────────────────────────────────

    def _on_metadata_chunk_ready(self, start_index: int, results: list, generation: int):
        """A chunk of metadata (dates, duration) arrived from one shard —
        update those rows.

        Each shard owns a disjoint, fixed range of global indices (its
        start_index offset), and the one-time sort only happens once every
        shard has finished — so concurrent shards never write overlapping
        indices, and start_index is always still pre-sort here.
        """
        if generation != self._load_generation:
            return
        end = start_index
        for i, meta in enumerate(results):
            idx = start_index + i
            if idx >= len(self._files):
                break
            old = self._files[idx]
            self._files[idx] = MediaFile(
                filepath             = meta['filepath'],
                filename             = meta['filename'],
                ext                  = meta['ext'],
                is_video             = meta['is_video'],
                is_already_formatted = meta['is_already_formatted'],
                date                 = meta['date'],
                date_source          = meta['date_source'],
                stripped_filename    = meta['stripped_filename'],
                duration_seconds     = meta.get('duration_seconds'),
                # A thumbnail may already have arrived for this row — keep it.
                thumbnail            = old.thumbnail,
                thumbnail_loaded     = old.thumbnail_loaded,
            )
            end = idx + 1

        if end > start_index:
            tl = self.index(start_index, 0)
            br = self.index(end - 1, COLUMN_COUNT - 1)
            self.dataChanged.emit(tl, br)

        # Shards complete chunks concurrently, so progress is a running
        # total, not any one shard's own high-water mark.
        self._metadata_done_count += end - start_index
        self.file_progress.emit(self._metadata_done_count, self._total_files)

    def _on_metadata_error(self, message: str, generation: int):
        """exiftool couldn't start at all — surface it once, even if several
        shards fail simultaneously. Unlike read_metadata()'s silent per-file
        fallback, this is the one intentional behaviour change: a broken
        exiftool.exe is no longer swallowed."""
        if generation != self._load_generation:
            return
        if self._metadata_error_shown:
            return
        self._metadata_error_shown = True
        self.metadata_load_error.emit(message)

    def _on_metadata_shard_finished(self, generation: int):
        """One metadata shard done (success or error). Once every shard has
        finished: sort, recalculate, and complete the load. Thumbnails keep
        arriving after this — see thumbnails_complete."""
        if generation != self._load_generation:
            return
        self._pending_metadata_shards -= 1
        if self._pending_metadata_shards > 0:
            return
        self._current_batch_workers = []
        self._sort_by_filename()
        self.recalculate_proposed_filenames()
        self.folder_load_complete.emit()

    def _on_thumb_ready(self, index: int, qimage, generation: int, filepath: str):
        """Thumbnail arrived (or failed, qimage=None) — convert to QPixmap on
        the main thread and take the row out of its "loading" state."""
        if generation != self._load_generation:
            return
        row = self._resolve_index(index, filepath)
        if row is not None:
            f = self._files[row]
            if qimage is not None:
                f.thumbnail = QPixmap.fromImage(qimage)
            f.thumbnail_loaded = True
            idx = self.index(row, COL_THUMB)
            self.dataChanged.emit(idx, idx, [Qt.DecorationRole])
        self._pending_thumbnails -= 1
        if self._pending_thumbnails == 0:
            self.thumbnails_complete.emit()

    def _resolve_index(self, index: int, filepath: str) -> Optional[int]:
        """
        A row's position can change once (the post-metadata sort in
        _on_metadata_shard_finished, after the last shard completes) while
        thumbnails for the same load are still arriving. Trust the given
        index only if it still points at the expected file; otherwise fall
        back to a linear search by filepath.
        """
        if 0 <= index < len(self._files) and self._files[index].filepath == filepath:
            return index
        for i, f in enumerate(self._files):
            if f.filepath == filepath:
                return i
        return None

    def _sort_by_filename(self):
        """Sort file list alphabetically by filename. Also stamps the new
        "since the last Apply (or load)" baseline that undo_move() restores
        a moved weak file to (MediaFile.original_index), and clears
        manual_date_undo for every file: a date-picker correction is only
        undoable up to the next Apply, the same "between Apply clicks"
        scope as an undone move — once Apply has run, that's the new
        checkpoint, whether or not this particular file was touched by it."""
        self._files.sort(key=lambda f: f.filename.lower())
        for i, f in enumerate(self._files):
            f.original_index = i
            f.manual_date_undo = None
        self.layoutChanged.emit()