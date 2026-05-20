from __future__ import annotations

import os
import re
import sys
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
    read_metadata, build_new_filename,
    SUPPORTED_EXTENSIONS,
    DATE_SOURCE_NONE, DATE_SOURCE_FILENAME, DATE_SOURCE_MODIFIED
)

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

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

_PLACEHOLDER_PHASE1 = ("--- Rename highlighted files before you move this file ---")
_PLACEHOLDER_PHASE2 = '--- Move file into chronological order ---'


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

    def __post_init__(self):
        if not self.proposed_filename:
            self.proposed_filename = self.filename


# ── Worker signals ──────────────────────────────────────────────────────────
# Each MetadataWorker owns its own WorkerSignals instance to avoid
# garbage-collection issues with shared signal objects in QThreadPool.
class WorkerSignals(QObject):
    file_ready  = Signal(int, object)  # (index, MediaFile)
    thumb_ready = Signal(int, object)  # (index, QImage) — converted to QPixmap on main thread
    finished    = Signal()


# ── Metadata + thumbnail worker ─────────────────────────────────────────────
class MetadataWorker(QRunnable):

    THUMB_W = 160  # always load at expanded size — delegates scale down for compact
    THUMB_H = 120

    def __init__(self, index: int, filepath: str):
        super().__init__()
        self.index    = index
        self.filepath = filepath
        self.signals  = WorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        meta     = read_metadata(self.filepath)
        ext      = Path(self.filepath).suffix.lower()
        is_video = ext in {'.mp4', '.mov', '.avi'}

        duration = None
        if is_video:
            duration = self._get_video_duration(self.filepath)

        mf = MediaFile(
            filepath             = meta['filepath'],
            filename             = meta['filename'],
            ext                  = meta['ext'],
            is_video             = meta['is_video'],
            is_already_formatted = meta['is_already_formatted'],
            date                 = meta['date'],
            date_source          = meta['date_source'],
            stripped_filename    = meta['stripped_filename'],
            duration_seconds     = duration,
        )
        self.signals.file_ready.emit(self.index, mf)

        # Generate thumbnail after emitting file data so the row appears fast
        qimage = self._make_thumbnail(self.filepath, is_video)
        if qimage is not None:
            self.signals.thumb_ready.emit(self.index, qimage)

        self.signals.finished.emit()

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

    def _get_video_duration(self, filepath: str) -> Optional[int]:
        """Return video duration in seconds via exiftool."""
        import subprocess
        exiftool = _vendor_path('exiftool.exe')
        try:
            result = subprocess.run(
                [exiftool, '-Duration#', '-s3', filepath],
                capture_output=True, text=True, timeout=10,
                creationflags=CREATE_NO_WINDOW
            )
            val = result.stdout.strip()
            if val:
                return int(float(val))
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
    folder_load_complete = Signal()
    file_progress        = Signal(int, int) # done, total
    attention_required   = Signal(int)      # emits count of flagged files
    rename_progress      = Signal(int, int) # done, total
    rename_complete      = Signal(int, int) # success_count, error_count
    phase_changed        = Signal(bool)     # True = Phase 1, False = Phase 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._files: List[MediaFile] = []
        self._pool  = QThreadPool.globalInstance()
        self._pending_workers = 0
        self._total_workers   = 0
        self._is_phase1 = False

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
                return f.proposed_filename

        if role == Qt.CheckStateRole and col == COL_CHECK:
            return Qt.Checked if f.selected else Qt.Unchecked

        if role == Qt.DecorationRole and col == COL_THUMB:
            return f.thumbnail

        if role == Qt.BackgroundRole:
            if f.is_already_formatted:
                return QColor('#FAFAFA' if row % 2 else '#FFFFFF')
            is_weak = f.date_source in (DATE_SOURCE_MODIFIED, DATE_SOURCE_NONE)
            alt = bool(row % 2)
            if self._is_phase1:
                return (QColor('#EEEEEE' if alt else '#F3F4F6') if is_weak
                        else QColor('#FFFBEB' if alt else '#FEF3C7'))
            else:
                return (QColor('#FFFBEB' if alt else '#FEF3C7') if is_weak
                        else QColor('#FAFAFA' if alt else '#FFFFFF'))

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

        return False

    def flags(self, index: QModelIndex):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == COL_CHECK:
            base |= Qt.ItemIsUserCheckable
        return base

    # ── Public API ───────────────────────────────────────────────────────────

    def load_folder(self, folder_path: str):
        self.beginResetModel()
        self._files = []
        self.endResetModel()

        path = Path(folder_path)
        filepaths = sorted([          # sorted() gives filename order
            str(p) for p in path.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        ])

        if not filepaths:
            return

        self.folder_load_started.emit(len(filepaths))
        self._total_workers   = len(filepaths)
        self._pending_workers = len(filepaths)

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

        for i, fp in enumerate(filepaths):
            worker = MetadataWorker(i, fp)
            worker.signals.file_ready.connect(self._on_file_ready)
            worker.signals.thumb_ready.connect(self._on_thumb_ready)
            worker.signals.finished.connect(self._on_worker_finished)
            self._pool.start(worker)

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
        placeholder = (_PLACEHOLDER_PHASE1
                       if self.has_pending_strong_renames()
                       else _PLACEHOLDER_PHASE2)

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
                        f.proposed_filename = build_new_filename(
                            f.filename, own_dt, is_interpolated=False)
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
                                f.filename, own_dt, is_interpolated=False)
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
        Rename files on disk based on proposed_filename.
        Checks existence before renaming, collects errors without stopping.
        Writes metadata timestamp for interpolated files only.
        """
        exiftool_path = _vendor_path('exiftool.exe')
        success = 0
        errors  = 0

        pending = [f for f in self._files
                   if f.proposed_filename != f.filename
                   and not f.proposed_filename.startswith('---')
                   and f.proposed_filename != '']
        total = len(pending)

        for done, f in enumerate(pending, 1):
            src  = Path(f.filepath)
            dest = src.parent / f.proposed_filename

            if not src.exists():
                errors += 1
            else:
                try:
                    src.rename(dest)
                    f.filepath  = str(dest)
                    f.filename  = f.proposed_filename

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

                    if f.is_interpolated:
                        self._write_metadata_date(str(dest), f, exiftool_path)

                    f.is_already_formatted = True
                    f.is_interpolated      = False
                    f.is_re_anchored       = False
                    f.user_moved           = False
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
        """Write interpolated timestamp back to file metadata via exiftool."""
        import re, subprocess
        m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})',
                     f.proposed_filename)
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
            f.proposed_filename != f.filename and
            not f.proposed_filename.startswith('---') and f.proposed_filename != ''
            for f in self._files
        )

    def has_pending_strong_renames(self) -> bool:
        return any(
            not f.is_already_formatted
            and f.date_source in ('metadata', 'filename')
            and not f.user_moved
            for f in self._files
        )

    def attention_count(self) -> int:
        return sum(1 for f in self._files if f.needs_attention)

    def files(self) -> List[MediaFile]:
        return self._files

    # ── Worker callbacks ─────────────────────────────────────────────────────

    def _on_file_ready(self, index: int, mf: MediaFile):
        """Metadata arrived for one file — update the row only."""
        if index >= len(self._files):
            return
        self._files[index] = mf
        tl = self.index(index, 0)
        br = self.index(index, COLUMN_COUNT - 1)
        self.dataChanged.emit(tl, br)

    def _on_thumb_ready(self, index: int, qimage):
        """Thumbnail arrived as QImage — convert to QPixmap on main thread."""
        if index >= len(self._files):
            return
        if qimage is not None:
            self._files[index].thumbnail = QPixmap.fromImage(qimage)
        idx = self.index(index, COL_THUMB)
        self.dataChanged.emit(idx, idx, [Qt.DecorationRole])

    def _on_worker_finished(self):
        """One worker done — sort and recalculate when all are finished."""
        self._pending_workers -= 1
        self.file_progress.emit(
            self._total_workers - self._pending_workers, self._total_workers)
        if self._pending_workers <= 0:
            self._sort_by_filename()
            self.recalculate_proposed_filenames()
            self.folder_load_complete.emit()

    def _sort_by_filename(self):
        """Sort file list alphabetically by filename."""
        self._files.sort(key=lambda f: f.filename.lower())
        self.layoutChanged.emit()