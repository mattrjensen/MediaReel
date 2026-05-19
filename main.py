from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import (
    Qt, QSize, QRect, QEvent, QTimer, QRectF, QStandardPaths, Signal
)
from PySide6.QtGui import (
    QColor, QPainter, QPen, QPixmap, QImage, QPalette
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QToolBar, QTableView, QAbstractItemView,
    QHeaderView, QLabel, QStatusBar, QPushButton,
    QFileDialog, QMessageBox, QProgressDialog, QStyledItemDelegate,
    QStyleOptionViewItem, QStyleOptionButton, QProgressBar, QStyle,
    QSizePolicy
)

from media_model import (
    MediaTableModel, MediaFile,
    COL_CHECK, COL_ORDER, COL_FILENAME, COL_DATE,
    COL_PREVIEW, COL_THUMB, COL_MOVE,
    MediaFileRole, DateSourceRole,
    IsInterpolatedRole, IsReAnchoredRole, NeedsAttentionRole
)

# ── Size constants (mutable — updated by the expand/compact toggle) ──────────
COMPACT_THUMB_W  = 80
COMPACT_THUMB_H  = 60
COMPACT_ROW_H    = 68
EXPANDED_THUMB_W = 160
EXPANDED_THUMB_H = 120
EXPANDED_ROW_H   = 140

THUMB_W = COMPACT_THUMB_W
THUMB_H = COMPACT_THUMB_H
ROW_H   = COMPACT_ROW_H

# ── Colour palette ───────────────────────────────────────────────────────────
# Preview column text colours:
#   Amber — will be renamed (any file getting a new name)
#   Grey  — no rename will happen (hard anchor, or unmoved weak anchor)
CLR_DERIVED   = QColor('#D97706')   # amber
CLR_NO_CHANGE = QColor('#9CA3AF')   # grey

SOURCE_BADGE = {
    'metadata':      ('#D1FAE5', '#065F46'),
    'filename':      ('#E5E7EB', '#374151'),
    'date modified': ('#FEF3C7', '#92400E'),
    'interpolated':  ('#FED7AA', '#9A3412'),
    'none':          ('#FEE2E2', '#991B1B'),
}


# ── Base delegate ─────────────────────────────────────────────────────────────
class BaseDelegate(QStyledItemDelegate):

    _is_phase1: bool = False

    def set_phase1(self, is_phase1: bool):
        self._is_phase1 = is_phase1

    def _draw_bg(self, painter: QPainter, option, f: MediaFile = None):
        alt = bool(option.features & QStyleOptionViewItem.Alternate)
        if f is None or f.is_already_formatted:
            painter.fillRect(option.rect, QColor('#FAFAFA' if alt else '#FFFFFF'))
            return
        is_weak = f.date_source in ('date modified', 'none')
        if self._is_phase1:
            if is_weak:
                painter.fillRect(option.rect, QColor('#EEEEEE' if alt else '#F3F4F6'))
            else:
                painter.fillRect(option.rect, QColor('#FFFBEB' if alt else '#FEF3C7'))
        else:
            if is_weak:
                painter.fillRect(option.rect, QColor('#FFFBEB' if alt else '#FEF3C7'))
            else:
                painter.fillRect(option.rect, QColor('#FAFAFA' if alt else '#FFFFFF'))

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), ROW_H)


# ── Preview delegate ──────────────────────────────────────────────────────────
class PreviewDelegate(BaseDelegate):

    def paint(self, painter: QPainter, option, index):
        f: MediaFile = index.data(MediaFileRole)
        if f is None:
            super().paint(painter, option, index)
            return

        painter.save()
        self._draw_bg(painter, option, f)

        if f.proposed_filename == f.filename or f.proposed_filename.startswith('---'):
            colour = CLR_NO_CHANGE
        else:
            colour = CLR_DERIVED

        painter.setPen(colour)
        rect = option.rect.adjusted(8, 0, -8, 0)
        painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft,
                         f.proposed_filename)
        painter.restore()


# ── Date delegate ─────────────────────────────────────────────────────────────
class DateDelegate(BaseDelegate):

    BADGE_H = 15

    def paint(self, painter: QPainter, option, index):
        f: MediaFile = index.data(MediaFileRole)
        if f is None:
            super().paint(painter, option, index)
            return

        painter.save()
        self._draw_bg(painter, option, f)

        # Source badge — drawn first, at top
        source = ('interpolated'
                  if (f.is_interpolated or f.is_re_anchored)
                  else f.date_source)
        if self._is_phase1 and source in ('date modified', 'none'):
            bg_hex, fg_hex = '#E5E7EB', '#6B7280'
        else:
            bg_hex, fg_hex = SOURCE_BADGE.get(source, SOURCE_BADGE['none'])

        small = painter.font()
        small.setPointSize(max(7, small.pointSize() - 2))
        painter.setFont(small)
        fm = painter.fontMetrics()
        pad = 6
        bw = fm.horizontalAdvance(source) + pad * 2
        bh = self.BADGE_H
        bx = option.rect.x() + 8
        by = option.rect.y() + 8

        painter.setBrush(QColor(bg_hex))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(bx, by, bw, bh, 3, 3)
        painter.setPen(QColor(fg_hex))
        painter.drawText(bx, by, bw, bh, Qt.AlignCenter, source)

        # Date text — below badge
        date_str = index.data(Qt.DisplayRole) or ''
        painter.setPen(QColor('#111827'))
        painter.setFont(option.font)
        date_y = by + bh + 2
        painter.drawText(
            option.rect.x() + 8, date_y,
            option.rect.width() - 16,
            option.rect.bottom() - date_y - 4,
            Qt.AlignTop | Qt.AlignLeft, date_str)

        painter.restore()


# ── Thumbnail delegate ────────────────────────────────────────────────────────
class ThumbnailDelegate(BaseDelegate):

    open_file_requested = Signal(str)  # filepath

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = False

    def set_expanded(self, expanded: bool):
        self._expanded = expanded

    def editorEvent(self, event, model, option, index):
        if (self._expanded and
                event.type() == QEvent.MouseButtonRelease):
            f: MediaFile = index.data(MediaFileRole)
            if f and f.filepath:
                self.open_file_requested.emit(f.filepath)
                return True
        return False

    def paint(self, painter: QPainter, option, index):
        f: MediaFile = index.data(MediaFileRole)

        painter.save()
        self._draw_bg(painter, option, f)

        if f and f.thumbnail:
            px = f.thumbnail.scaled(
                THUMB_W, THUMB_H,
                Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = option.rect.x() + (option.rect.width()  - px.width())  // 2
            y = option.rect.y() + (option.rect.height() - px.height()) // 2
            painter.drawPixmap(x, y, px)

            # Duration badge for videos
            if f.is_video and f.duration_seconds is not None:
                dur = self._fmt(f.duration_seconds)
                small = painter.font()
                small.setPointSize(max(7, small.pointSize() - 2))
                painter.setFont(small)
                fm = painter.fontMetrics()
                bw = fm.horizontalAdvance(dur) + 6
                bh = fm.height() + 2
                bx = x + px.width()  - bw - 2
                by = y + px.height() - bh - 2
                painter.setBrush(QColor(0, 0, 0, 180))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(bx, by, bw, bh, 2, 2)
                painter.setPen(QColor('#FFFFFF'))
                painter.drawText(bx, by, bw, bh, Qt.AlignCenter, dur)
        else:
            # Placeholder
            pr = option.rect.adjusted(6, 4, -6, -4)
            painter.setBrush(QColor('#F3F4F6'))
            painter.setPen(QColor('#D1D5DB'))
            painter.drawRoundedRect(pr, 4, 4)
            if f and f.is_video:
                f2 = painter.font()
                f2.setPointSize(16)
                painter.setFont(f2)
                painter.setPen(QColor('#9CA3AF'))
                painter.drawText(pr, Qt.AlignCenter, '▶')

        painter.restore()

    def _fmt(self, secs: int) -> str:
        m, s = divmod(secs, 60)
        return f'{m}:{s:02d}'

    def sizeHint(self, option, index):
        return QSize(THUMB_W + 16, ROW_H)


# ── Move delegate ─────────────────────────────────────────────────────────────
class MoveDelegate(BaseDelegate):

    BTN_H = 20
    BTN_W = 24

    row_move_requested = Signal(int, int)  # row, direction

    def __init__(self, parent=None):
        super().__init__(parent)
        self._multi_select = False

    def set_multi_select(self, multi: bool):
        self._multi_select = multi

    def paint(self, painter: QPainter, option, index):
        f: MediaFile = index.data(MediaFileRole)
        painter.save()
        self._draw_bg(painter, option, f)

        if self._multi_select:
            painter.restore()
            return

        cx = option.rect.center().x()
        cy = option.rect.center().y()

        for label, dy in [('▲', -14), ('▼', 12)]:
            bx = cx - self.BTN_W // 2
            by = cy + dy - self.BTN_H // 2
            painter.setBrush(QColor('#F3F4F6'))
            painter.setPen(QColor('#D1D5DB'))
            painter.drawRoundedRect(bx, by, self.BTN_W, self.BTN_H, 3, 3)
            small = painter.font()
            small.setPointSize(max(7, small.pointSize() - 2))
            painter.setFont(small)
            painter.setPen(QColor('#6B7280'))
            painter.drawText(bx, by, self.BTN_W, self.BTN_H,
                             Qt.AlignCenter, label)

        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease:
            cy = option.rect.center().y()
            y  = event.position().y()

            up_top = cy - 14 - self.BTN_H // 2
            up_bot = cy - 14 + self.BTN_H // 2
            dn_top = cy + 12 - self.BTN_H // 2
            dn_bot = cy + 12 + self.BTN_H // 2

            row = index.row()
            if up_top <= y <= up_bot:
                self.row_move_requested.emit(row, -1)
                return True
            elif dn_top <= y <= dn_bot:
                self.row_move_requested.emit(row, 1)
                return True
        return False


# ── Row background delegates (checkbox / order / filename columns) ────────────
# These columns have no other custom rendering, but need the same phase-aware
# background as the other columns so the full row is highlighted consistently.

class _CheckDelegate(BaseDelegate):

    def paint(self, painter: QPainter, option, index):
        f: MediaFile = index.data(MediaFileRole)
        painter.save()
        self._draw_bg(painter, option, f)
        checked = (index.data(Qt.CheckStateRole) == Qt.Checked)
        size = 14
        r = option.rect
        cb = QRect(r.x() + (r.width()  - size) // 2,
                   r.y() + (r.height() - size) // 2,
                   size, size)
        opt = QStyleOptionButton()
        opt.rect  = cb
        opt.state = (QStyle.State_Enabled |
                     (QStyle.State_On if checked else QStyle.State_Off))
        QApplication.style().drawPrimitive(QStyle.PE_IndicatorCheckBox, opt, painter)
        painter.restore()


class _RowTextDelegate(BaseDelegate):

    def paint(self, painter: QPainter, option, index):
        f: MediaFile = index.data(MediaFileRole)
        painter.save()
        self._draw_bg(painter, option, f)
        text = index.data(Qt.DisplayRole) or ''
        painter.setPen(QColor('#111827'))
        painter.drawText(option.rect.adjusted(8, 0, -8, 0),
                         Qt.AlignVCenter | Qt.AlignLeft, text)
        painter.restore()


# ── Loading overlay ───────────────────────────────────────────────────────────
class LoadingOverlay(QWidget):
    """
    Circular progress ring overlay. Grey track, blue arc fills clockwise as
    files load. Percentage shown in centre. A short rotating arc signals
    activity before the first progress tick arrives.
    """

    _R = 44.0   # ring radius
    _W = 7      # stroke width

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setVisible(False)
        self._progress = 0.0   # 0.0–1.0
        self._angle    = 0     # rotating stub arc for 0 % state
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._progress = 0.0
        self._angle    = 0
        self._resize_to_parent()
        self.setVisible(True)
        self.raise_()
        self._timer.start(25)

    def stop(self):
        self._timer.stop()
        self.setVisible(False)

    def set_progress(self, done: int, total: int):
        self._progress = done / total if total > 0 else 0.0
        self.update()

    def _tick(self):
        self._angle = (self._angle + 4) % 360
        self.update()

    def _resize_to_parent(self):
        if self.parent():
            self.setGeometry(self.parent().rect())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(Qt.NoBrush)

        painter.fillRect(self.rect(), QColor(255, 255, 255, 210))

        cx = self.width()  / 2
        cy = self.height() / 2
        R, W = self._R, self._W
        ring = QRectF(cx - R, cy - R, R * 2, R * 2)

        # Grey background track
        pen = QPen(QColor('#E5E7EB'), W, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(ring, 0, 360 * 16)

        pct = self._progress
        if pct > 0:
            # Blue progress arc, clockwise from 12 o'clock
            pen = QPen(QColor('#2563EB'), W, Qt.SolidLine, Qt.RoundCap)
            painter.setPen(pen)
            painter.drawArc(ring, 90 * 16, -int(pct * 360 * 16))
        else:
            # Short rotating stub to show activity before first tick
            pen = QPen(QColor('#93C5FD'), W, Qt.SolidLine, Qt.RoundCap)
            painter.setPen(pen)
            painter.drawArc(ring, (90 - self._angle) * 16, -60 * 16)

        # Percentage in centre
        pct_text = f'{int(pct * 100)}%'
        f1 = painter.font()
        f1.setPointSize(17)
        f1.setBold(True)
        painter.setFont(f1)
        painter.setPen(QColor('#1E3A8A'))
        fm = painter.fontMetrics()
        painter.drawText(
            int(cx - fm.horizontalAdvance(pct_text) / 2),
            int(cy + fm.ascent() / 2 - fm.descent() / 2),
            pct_text)

        # Label below ring
        f2 = painter.font()
        f2.setBold(False)
        f2.setPointSize(10)
        painter.setFont(f2)
        painter.setPen(QColor('#6B7280'))
        label = 'Reading metadata…'
        fm2 = painter.fontMetrics()
        painter.drawText(
            int(cx - fm2.horizontalAdvance(label) / 2),
            int(cy + R + 22),
            label)

        painter.end()


# ── Table view with row-border selection ─────────────────────────────────────
class MediaTableView(QTableView):
    """QTableView that draws a 1px blue border around each selected row instead
    of flooding the row with a highlight fill."""

    def paintEvent(self, event):
        super().paintEvent(event)
        sel = self.selectionModel()
        if not sel:
            return
        rows = {idx.row() for idx in sel.selectedIndexes()}
        if not rows:
            return
        model = self.model()
        if not model:
            return
        last_col = model.columnCount() - 1
        painter = QPainter(self.viewport())
        pen = QPen(QColor('#2563EB'), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        for row in sorted(rows):
            left  = self.visualRect(model.index(row, 0))
            right = self.visualRect(model.index(row, last_col))
            row_rect = left.united(right).adjusted(0, 0, -1, -1)
            painter.drawRect(row_rect)
        painter.end()


# ── Main window ───────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Media Reel')
        self.setMinimumSize(1100, 600)
        self.resize(1280, 800)
        self._model        = MediaTableModel()
        self._is_phase1    = False
        self._expanded     = False
        self._repeat_timer = QTimer(self)
        self._repeat_timer.timeout.connect(self._on_repeat_tick)
        self._repeat_dir   = 0
        self._repeat_count = 0
        self._setup_ui()
        self._overlay = LoadingOverlay(self.centralWidget())
        self._connect_signals()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Toolbar ──────────────────────────────────────────────────────────
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setStyleSheet('''
            QToolBar {
                border-bottom: 1px solid #E5E7EB;
                padding: 6px 10px; spacing: 6px;
                background: #F9FAFB;
            }
            QToolBar::separator {
                width: 1px; background: #E5E7EB; margin: 2px 4px;
            }
        ''')
        self.addToolBar(toolbar)

        self._btn_open = QPushButton('📁  Open folder')
        self._btn_open.setStyleSheet(self._btn_style())
        toolbar.addWidget(self._btn_open)
        toolbar.addSeparator()

        self._btn_expand = QPushButton('⊞  Expand')
        self._btn_expand.setStyleSheet(self._btn_style())
        toolbar.addWidget(self._btn_expand)
        toolbar.addSeparator()

        self._btn_down = QPushButton('▼  Move down')
        self._btn_down.setStyleSheet(self._btn_style())
        self._btn_down.setEnabled(False)
        toolbar.addWidget(self._btn_down)

        self._btn_up = QPushButton('▲  Move up')
        self._btn_up.setStyleSheet(self._btn_style())
        self._btn_up.setEnabled(False)
        toolbar.addWidget(self._btn_up)

        toolbar.addSeparator()

        self._btn_attention = QPushButton('⚠  0 files need attention')
        self._btn_attention.setStyleSheet(self._btn_style_warn())
        self._act_attention = toolbar.addWidget(self._btn_attention)
        self._act_attention.setVisible(False)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        self._lbl_toolbar_rename = QLabel('')
        self._lbl_toolbar_rename.setStyleSheet(
            'color: #6B7280; font-size: 12px; padding: 0 8px;')
        toolbar.addWidget(self._lbl_toolbar_rename)

        toolbar.addSeparator()

        self._btn_apply = QPushButton('✓  Apply rename')
        self._btn_apply.setStyleSheet(self._btn_style_primary())
        self._btn_apply.setEnabled(False)
        toolbar.addWidget(self._btn_apply)

        # ── Progress bar ──────────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setFixedHeight(3)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet('''
            QProgressBar { border: none; background: #E5E7EB; }
            QProgressBar::chunk { background: #2563EB; }
        ''')
        layout.addWidget(self._progress)

        # ── Table ─────────────────────────────────────────────────────────────
        self._table = MediaTableView()
        self._table.setModel(self._model)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setStyleSheet('''
            QTableView {
                border: none; outline: none;
                background: #FFFFFF;
                alternate-background-color: #FAFAFA;
            }
            QTableView::item {
                border-bottom: 1px solid #F3F4F6;
                color: #111827;
            }
            QHeaderView::section {
                background: #F9FAFB;
                border: none;
                border-bottom: 2px solid #E5E7EB;
                border-right: 1px solid #F3F4F6;
                padding: 6px 8px;
                font-weight: 600;
                font-size: 11px;
                color: #6B7280;
            }
        ''')

        # Left-align all column headers
        self._table.horizontalHeader().setDefaultAlignment(
            Qt.AlignLeft | Qt.AlignVCenter)

        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(COL_CHECK,    QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_ORDER,    QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_FILENAME, QHeaderView.Interactive)
        hh.setSectionResizeMode(COL_DATE,     QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_PREVIEW,  QHeaderView.Stretch)
        hh.setSectionResizeMode(COL_THUMB,    QHeaderView.Fixed)
        hh.setSectionResizeMode(COL_MOVE,     QHeaderView.Fixed)

        self._table.setColumnWidth(COL_CHECK,    32)
        self._table.setColumnWidth(COL_ORDER,    44)
        self._table.setColumnWidth(COL_FILENAME, 240)
        self._table.setColumnWidth(COL_DATE,     170)
        self._table.setColumnWidth(COL_THUMB,    THUMB_W + 16)
        self._table.setColumnWidth(COL_MOVE,     48)

        self._table.verticalHeader().setDefaultSectionSize(ROW_H)

        self._move_delegate     = MoveDelegate(self)
        self._thumb_delegate    = ThumbnailDelegate(self)
        self._date_delegate     = DateDelegate(self)
        self._preview_delegate  = PreviewDelegate(self)
        self._check_delegate    = _CheckDelegate(self)
        self._order_delegate    = _RowTextDelegate(self)
        self._filename_delegate = _RowTextDelegate(self)
        self._table.setItemDelegateForColumn(COL_CHECK,   self._check_delegate)
        self._table.setItemDelegateForColumn(COL_ORDER,   self._order_delegate)
        self._table.setItemDelegateForColumn(COL_FILENAME, self._filename_delegate)
        self._table.setItemDelegateForColumn(COL_DATE,    self._date_delegate)
        self._table.setItemDelegateForColumn(COL_PREVIEW, self._preview_delegate)
        self._table.setItemDelegateForColumn(COL_THUMB,   self._thumb_delegate)
        self._table.setItemDelegateForColumn(COL_MOVE,    self._move_delegate)

        layout.addWidget(self._table)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status = QStatusBar()
        self._status.setStyleSheet('''
            QStatusBar {
                border-top: 1px solid #E5E7EB;
                background: #F9FAFB;
                font-size: 11px;
                color: #6B7280;
            }
            QStatusBar::item { border: none; }
        ''')
        self.setStatusBar(self._status)

        self._lbl_files   = QLabel('No folder loaded')
        self._lbl_flagged = QLabel('')

        self._status.addWidget(self._lbl_files)
        self._status.addWidget(self._lbl_flagged)

    def _connect_signals(self):
        self._btn_open.clicked.connect(self._open_folder)
        self._btn_down.pressed.connect(lambda: self._on_move_pressed(1))
        self._btn_down.released.connect(self._on_move_released)
        self._btn_up.pressed.connect(lambda: self._on_move_pressed(-1))
        self._btn_up.released.connect(self._on_move_released)
        self._btn_expand.clicked.connect(self._toggle_expand)
        self._btn_apply.clicked.connect(self._apply_rename)
        self._btn_attention.clicked.connect(self._jump_to_attention)
        self._move_delegate.row_move_requested.connect(
            lambda row, direction: self._move(direction, row))
        self._thumb_delegate.open_file_requested.connect(self._open_file)

        self._model.folder_load_started.connect(self._on_load_started)
        self._model.folder_load_complete.connect(self._on_load_complete)
        self._model.file_progress.connect(self._overlay.set_progress)
        self._model.attention_required.connect(self._on_attention_required)
        self._model.rename_complete.connect(self._on_rename_complete)
        self._model.phase_changed.connect(self._on_phase_changed)
        self._model.dataChanged.connect(self._refresh_status)
        self._model.layoutChanged.connect(self._refresh_status)

        self._table.selectionModel().selectionChanged.connect(
            self._on_selection_changed)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _open_folder(self):
        pictures = QStandardPaths.writableLocation(
            QStandardPaths.PicturesLocation)
        folder = QFileDialog.getExistingDirectory(
            self, 'Select event folder', pictures,
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks
        )
        if folder:
            self.setWindowTitle(f'Media Reel — {Path(folder).name}')
            self._model.load_folder(folder)

    def _move(self, direction: int, clicked_row: int = -1):
        if clicked_row >= 0:
            # Chevron click — move all selected rows if the clicked row is
            # part of that selection, otherwise just the clicked row.
            selected = self._model.get_selected_indices()
            indices = selected if clicked_row in selected else [clicked_row]
        else:
            # Toolbar click — fall back to current index if nothing selected.
            indices = self._model.get_selected_indices()
            if not indices:
                idx = self._table.currentIndex()
                if idx.isValid():
                    indices = [idx.row()]

        if not indices:
            return

        n = self._model.rowCount()
        if direction == -1 and min(indices) == 0:
            return
        if direction == 1 and max(indices) == n - 1:
            return

        new_indices = [i + direction for i in indices]

        # Block selection signals during the move to prevent reset
        sel_model = self._table.selectionModel()
        sel_model.blockSignals(True)
        self._model.move_rows(indices, direction)

        # Re-apply selection at new positions
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        selection = QItemSelection()
        for row in new_indices:
            tl = self._model.index(row, 0)
            br = self._model.index(row, self._model.columnCount() - 1)
            selection.select(tl, br)

        sel_model.clearSelection()
        sel_model.select(selection, QItemSelectionModel.Select)
        sel_model.blockSignals(False)

        # Sync model selection state
        rows_set = set(new_indices)
        for i, f in enumerate(self._model.files()):
            f.selected = (i in rows_set)

        # Keep the moved row focused. Use QItemSelectionModel.Current so the
        # focus indicator moves without clearing the multi-file selection.
        # (self._table.setCurrentIndex uses ClearAndSelect internally.)
        focus_row = (clicked_row + direction) if clicked_row >= 0 else new_indices[0]
        sel_model.setCurrentIndex(
            self._model.index(focus_row, COL_FILENAME),
            QItemSelectionModel.Current)

        # Scroll to keep moved rows visible
        self._table.scrollTo(
            self._model.index(focus_row, COL_FILENAME),
            QAbstractItemView.EnsureVisible)

        self._refresh_move_buttons()
        self._refresh_status()

    def _apply_rename(self):
        pending = sum(
            1 for f in self._model.files()
            if f.proposed_filename != f.filename
            and not f.proposed_filename.startswith('---')
            and f.proposed_filename != ''
        )
        if self._model.has_pending_strong_renames():
            msg = (f'{pending} file(s) will be renamed with the date '
                   f'and time they were taken.\n\n'
                   f'Make sure you have a backup.\n\nContinue?')
        else:
            msg = (f'{pending} file(s) will be renamed.\n\n'
                   f'Make sure you have a backup.\n\nContinue?')

        reply = QMessageBox.question(
            self, 'Apply rename', msg,
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        dlg = QProgressDialog(self)
        dlg.setWindowTitle('Media Reel')
        dlg.setLabelText(f'Renaming {pending} file{"s" if pending != 1 else ""}…')
        dlg.setRange(0, pending)
        dlg.setCancelButton(None)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)

        self._model.rename_progress.connect(lambda done, _: dlg.setValue(done))
        self._model.apply_rename()
        self._model.rename_progress.disconnect()
        dlg.close()

    def _jump_to_attention(self):
        files = self._model.files()
        if self._is_phase1:
            target = next(
                (i for i, f in enumerate(files)
                 if not f.is_already_formatted
                 and f.date_source in ('metadata', 'filename')
                 and not f.user_moved),
                None)
        else:
            target = next(
                (i for i, f in enumerate(files) if f.needs_attention),
                None)
        if target is not None:
            idx = self._model.index(target, COL_FILENAME)
            self._table.scrollTo(idx, QAbstractItemView.PositionAtCenter)
            self._table.setCurrentIndex(idx)

    def _open_file(self, filepath: str):
        try:
            os.startfile(filepath)
        except Exception:
            pass

    def _on_move_pressed(self, direction: int):
        self._repeat_dir   = direction
        self._repeat_count = 0
        self._move(direction)
        self._repeat_timer.setInterval(500)
        self._repeat_timer.start()

    def _on_move_released(self):
        self._repeat_timer.stop()
        self._repeat_count = 0

    def _on_repeat_tick(self):
        self._move(self._repeat_dir)
        interval = max(60, 150 - self._repeat_count * 9)
        self._repeat_timer.setInterval(interval)
        self._repeat_count += 1

    def _toggle_expand(self):
        global THUMB_W, THUMB_H, ROW_H
        self._expanded = not self._expanded
        if self._expanded:
            THUMB_W, THUMB_H, ROW_H = EXPANDED_THUMB_W, EXPANDED_THUMB_H, EXPANDED_ROW_H
            self._btn_expand.setText('⊟  Compact')
        else:
            THUMB_W, THUMB_H, ROW_H = COMPACT_THUMB_W, COMPACT_THUMB_H, COMPACT_ROW_H
            self._btn_expand.setText('⊞  Expand')
        self._thumb_delegate.set_expanded(self._expanded)
        self._table.verticalHeader().setDefaultSectionSize(ROW_H)
        self._table.setColumnWidth(COL_THUMB, THUMB_W + 16)
        self._table.reset()

    # ── Model callbacks ───────────────────────────────────────────────────────

    def _on_load_started(self, count: int):
        self._progress.setMaximum(count)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._lbl_files.setText(f'Loading {count} files…')
        self._btn_apply.setEnabled(False)
        self._btn_up.setEnabled(False)
        self._btn_down.setEnabled(False)
        self._act_attention.setVisible(False)  # clear stale state from previous folder
        self._overlay.start()

    def _on_load_complete(self):
        self._progress.setVisible(False)
        self._overlay.stop()
        self._refresh_status()

    def _on_attention_required(self, count: int):
        self._refresh_attention_button()

    def _refresh_attention_button(self):
        files = self._model.files()
        if self._is_phase1:
            n = sum(1 for f in files
                    if not f.is_already_formatted
                    and f.date_source in ('metadata', 'filename')
                    and not f.user_moved)
            if n > 0:
                self._btn_attention.setText(f'⚠  {n} file(s) need renaming')
                self._act_attention.setVisible(True)
            else:
                self._act_attention.setVisible(False)
        else:
            n = sum(1 for f in files if f.needs_attention)
            if n > 0:
                self._btn_attention.setText(f'⚠  {n} file(s) need ordering')
                self._act_attention.setVisible(True)
            else:
                self._act_attention.setVisible(False)

    def _on_phase_changed(self, is_phase1: bool):
        self._is_phase1 = is_phase1
        for delegate in (self._check_delegate, self._order_delegate,
                         self._filename_delegate, self._date_delegate,
                         self._preview_delegate, self._thumb_delegate,
                         self._move_delegate):
            delegate.set_phase1(is_phase1)
        tooltip = ('Rename files with proposed new filenames first — click Apply rename'
                   if is_phase1 else '')
        self._btn_up.setToolTip(tooltip)
        self._btn_down.setToolTip(tooltip)
        self._refresh_move_buttons()
        self._refresh_status()
        self._table.viewport().update()

    def _on_rename_complete(self, success: int, errors: int):
        if errors == 0:
            QMessageBox.information(
                self, 'Done',
                f'{success} file(s) renamed successfully.')
        else:
            QMessageBox.warning(
                self, 'Rename complete with errors',
                f'{success} file(s) renamed.\n'
                f'{errors} file(s) failed — check they are not open '
                f'in another application.')
        # Re-sort so newly renamed files (YYYYMMDD_HHMMSS prefix) appear in
        # correct chronological order, then recalculate to update all states.
        self._model._sort_by_filename()
        self._model.recalculate_proposed_filenames()
        self._refresh_status()
        self._table.scrollToTop()
        

    def _on_selection_changed(self, selected, deselected):
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        for i, f in enumerate(self._model.files()):
            f.selected = (i in rows)
        self._move_delegate.set_multi_select(len(rows) > 1)
        self._refresh_move_buttons()
        self._refresh_status()

    # ── Status / button refresh ───────────────────────────────────────────────

    def _refresh_status(self):
        files = self._model.files()
        total = len(files)
        self._lbl_files.setText(f'{total} files')
        self._btn_apply.setEnabled(self._model.has_pending_renames())

        if self._is_phase1:
            n = sum(1 for f in files
                    if not f.is_already_formatted
                    and f.date_source in ('metadata', 'filename')
                    and not f.user_moved)
            self._lbl_flagged.setText('')
            self._lbl_toolbar_rename.setText(
                f'Rename the {n} highlighted file(s)')
        else:
            will_rename = sum(
                1 for f in files
                if f.proposed_filename != f.filename
                and not f.proposed_filename.startswith('---')
                and f.proposed_filename != ''
            )
            flagged = sum(1 for f in files if f.needs_attention)
            self._lbl_flagged.setText(f'  ·  {flagged} flagged' if flagged else '')
            self._lbl_toolbar_rename.setText(
                f'{will_rename} file(s) to be renamed.' if will_rename else '')
        self._refresh_attention_button()

    def _refresh_move_buttons(self):
        if self._is_phase1:
            self._btn_up.setEnabled(False)
            self._btn_down.setEnabled(False)
        else:
            has_sel = bool(self._model.get_selected_indices())
            self._btn_up.setEnabled(has_sel)
            self._btn_down.setEnabled(has_sel)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_overlay'):
            self._overlay.setGeometry(self.centralWidget().rect())

    # ── Button styles ─────────────────────────────────────────────────────────

    def _btn_style(self) -> str:
        return '''
            QPushButton {
                border: 1px solid #D1D5DB; border-radius: 5px;
                padding: 5px 14px; background: #FFFFFF;
                font-size: 12px; color: #374151;
            }
            QPushButton:hover { background: #F3F4F6; }
            QPushButton:pressed { background: #E5E7EB; }
            QPushButton:disabled { color: #9CA3AF; background: #F9FAFB;
                                   border-color: #E5E7EB; }
        '''

    def _btn_style_primary(self) -> str:
        return '''
            QPushButton {
                border: 1px solid #1D4ED8; border-radius: 5px;
                padding: 5px 16px; background: #2563EB;
                font-size: 12px; color: white; font-weight: 600;
            }
            QPushButton:hover { background: #1D4ED8; }
            QPushButton:pressed { background: #1E40AF; }
            QPushButton:disabled {
                background: #BFDBFE; border-color: #BFDBFE; color: white;
            }
        '''

    def _btn_style_warn(self) -> str:
        return '''
            QPushButton {
                border: 1px solid #D97706; border-radius: 5px;
                padding: 5px 14px; background: #FEF3C7;
                font-size: 12px; color: #92400E; font-weight: 500;
            }
            QPushButton:hover { background: #FDE68A; }
        '''


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # Force light mode regardless of system theme
    palette = app.palette()
    palette.setColor(QPalette.Window,          QColor('#FFFFFF'))
    palette.setColor(QPalette.WindowText,      QColor('#111827'))
    palette.setColor(QPalette.Base,            QColor('#FFFFFF'))
    palette.setColor(QPalette.AlternateBase,   QColor('#FAFAFA'))
    palette.setColor(QPalette.Text,            QColor('#111827'))
    palette.setColor(QPalette.Button,          QColor('#F9FAFB'))
    palette.setColor(QPalette.ButtonText,      QColor('#374151'))
    palette.setColor(QPalette.Highlight,       QColor('#EFF6FF'))
    palette.setColor(QPalette.HighlightedText, QColor('#1E3A8A'))
    palette.setColor(QPalette.ToolTipBase,     QColor('#FFFFFF'))
    palette.setColor(QPalette.ToolTipText,     QColor('#111827'))
    app.setPalette(palette)

    app.setStyleSheet('''
        QMessageBox {
            background-color: #FFFFFF;
            font-size: 13px;
            color: #111827;
        }
        QMessageBox QLabel {
            color: #111827;
            font-size: 13px;
            padding: 8px;
        }
        QMessageBox QPushButton {
            border: 1px solid #D1D5DB;
            border-radius: 5px;
            padding: 6px 16px;
            background: #FFFFFF;
            font-size: 12px;
            color: #374151;
            min-width: 80px;
        }
        QMessageBox QPushButton:hover {
            background: #F3F4F6;
        }
        QMessageBox QPushButton:pressed {
            background: #E5E7EB;
        }
        QMessageBox QPushButton[text="Yes"],
        QMessageBox QPushButton[text="OK"] {
            background: #2563EB;
            border-color: #1D4ED8;
            color: white;
            font-weight: 600;
        }
        QMessageBox QPushButton[text="Yes"]:hover,
        QMessageBox QPushButton[text="OK"]:hover {
            background: #1D4ED8;
        }
        QMessageBox QPushButton[text="Yes"]:pressed,
        QMessageBox QPushButton[text="OK"]:pressed {
            background: #1E40AF;
        }
    ''')

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()