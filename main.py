from __future__ import annotations

import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    Qt, QSize, QRect, QEvent, QTimer, QRectF, QStandardPaths, Signal,
    QDate, QPersistentModelIndex
)
from PySide6.QtGui import (
    QColor, QPainter, QPen, QPixmap, QImage, QPalette, QFontMetrics
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QToolBar, QTableView, QAbstractItemView,
    QHeaderView, QLabel, QStatusBar, QPushButton,
    QFileDialog, QMessageBox, QProgressDialog, QStyledItemDelegate,
    QStyleOptionViewItem, QStyleOptionButton, QProgressBar, QStyle,
    QSizePolicy, QLineEdit, QCalendarWidget, QDialog, QHBoxLayout,
    QListWidget, QListWidgetItem, QToolTip
)

from media_model import (
    MediaTableModel, MediaFile, HEIF_AVAILABLE,
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
    'manual':        ('#EDE9FE', '#5B21B6'),
}


# ── Base delegate ─────────────────────────────────────────────────────────────
class BaseDelegate(QStyledItemDelegate):

    def _draw_bg(self, painter: QPainter, option, f: MediaFile = None):
        # Amber marks "needs a decision" (weak, unmoved, no value) — every
        # other row, hard anchor or not, gets the plain alternating
        # background; the New filename box's own colour/contents already
        # say whether it'll be renamed.
        alt = bool(option.features & QStyleOptionViewItem.Alternate)
        if f is not None and f.needs_attention:
            painter.fillRect(option.rect, QColor('#FFFBEB' if alt else '#FEF3C7'))
        else:
            painter.fillRect(option.rect, QColor('#FAFAFA' if alt else '#FFFFFF'))

    def sizeHint(self, option, index):
        return QSize(super().sizeHint(option, index).width(), ROW_H)


# ── Preview delegate ──────────────────────────────────────────────────────────
# Cells are custom-painted to look like a text input (bordered box, fixed
# ~40px height regardless of row height) rather than given a real,
# always-present QLineEdit per row — with up to 2000 rows, that many live
# widgets would be a real performance cost. A real QLineEdit is only
# created on demand for the one cell being edited. Unlike a normal Qt
# delegate, that editor opens on a plain single click anywhere in the box
# (MediaTableView.mousePressEvent calls edit() directly; see the class
# comment there for why this isn't done via editTriggers) — the point is
# for it to behave like a native input field, not a table cell that
# happens to support editing.
class PreviewDelegate(BaseDelegate):

    _ICON_SIZE = 28
    _BOX_H     = 40

    def _box_rect(self, option) -> QRect:
        h = self._BOX_H
        y = option.rect.y() + max(0, (option.rect.height() - h) // 2)
        return QRect(option.rect.x() + 6, y, option.rect.width() - 12, h)

    def _reset_icon_rect(self, option) -> QRect:
        box = self._box_rect(option)
        s = self._ICON_SIZE
        return QRect(box.right() - s - 6, box.center().y() - s // 2, s, s)

    def paint(self, painter: QPainter, option, index):
        f: MediaFile = index.data(MediaFileRole)
        if f is None:
            super().paint(painter, option, index)
            return

        painter.save()
        self._draw_bg(painter, option, f)

        if f.is_already_formatted:
            # Hard anchor — not editable, filename is already the source of
            # truth, so nothing will change. Left empty rather than
            # repeating the Filename column's value: this column means
            # "what will this become," and repeating the current name under
            # that header reads as a rename that isn't actually happening.
            painter.restore()
            return

        name         = f.display_filename
        has_override = f.manual_filename is not None

        # Input-box chrome. Blue border signals "you edited this"; grey is
        # the default, still-editable-but-untouched look.
        box = self._box_rect(option)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor('#FFFFFF'))
        painter.setPen(QPen(QColor('#93C5FD' if has_override else '#E5E7EB'), 1))
        painter.drawRoundedRect(box, 4, 4)

        if name == f.filename or name.startswith('---'):
            colour = CLR_NO_CHANGE
        else:
            colour = CLR_DERIVED

        # Clear icon — shown only when the box holds a real name that can
        # be cleared (MediaFile.can_clear_filename, the same test the click
        # handler in MediaTableView.mousePressEvent uses, so an unpainted
        # icon can't be clicked). Clicking sends '' to the model, which
        # blanks the field (skip this file on Apply) — except on a strong
        # anchor still waiting to be renamed, which must always have a
        # name, so there it puts the metadata-derived one back.
        show_reset = f.can_clear_filename

        text_rect = box.adjusted(10, 0, -((self._ICON_SIZE + 14) if show_reset else 10), 0)
        painter.setPen(colour)
        fm = painter.fontMetrics()
        elided = fm.elidedText(name, Qt.ElideMiddle, max(0, text_rect.width()))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided)

        if show_reset:
            # No background — just the glyph, sized to fill the (large, easy
            # to hit) icon rect rather than sitting small inside it.
            icon = self._reset_icon_rect(option)
            glyph_font = painter.font()
            glyph_font.setPixelSize(round(self._ICON_SIZE * 0.75))
            painter.setFont(glyph_font)
            painter.setPen(QColor('#6B7280'))
            painter.drawText(icon, Qt.AlignCenter, '×')

        painter.restore()

    def createEditor(self, parent, option, index):
        f: MediaFile = index.data(MediaFileRole)
        if f is None or f.is_already_formatted:
            return None
        editor = QLineEdit(parent)
        # Match the painted box's look so opening the editor doesn't cause
        # a visible jump — see updateEditorGeometry for the matching size.
        editor.setStyleSheet('''
            QLineEdit {
                border: 1px solid #93C5FD;
                border-radius: 4px;
                padding: 0 10px;
                background: #FFFFFF;
            }
        ''')
        return editor

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(self._box_rect(option))

    def setEditorData(self, editor, index):
        f: MediaFile = index.data(MediaFileRole)
        text = f.display_filename if f else ''
        editor.setText(text)
        # Pre-select just the stem, Explorer-rename style, so a normal
        # type-to-replace doesn't clobber the extension by accident.
        editor.setSelection(0, len(Path(text).stem))

    def setModelData(self, editor, model, index):
        # Always the literal typed text, including '' — an explicitly
        # emptied field means "skip this file on Apply" (see
        # MediaTableModel.set_manual_filename), the same as clicking the
        # reset icon on a non-empty field.
        model.setData(index, editor.text().strip(), Qt.EditRole)


# ── Date delegate ─────────────────────────────────────────────────────────────
class DateDelegate(BaseDelegate):

    BADGE_H    = 15
    _ICON_SIZE = 14
    _expanded: bool = False  # see ThumbnailDelegate — same class-attribute-default pattern

    def set_expanded(self, expanded: bool):
        self._expanded = expanded

    def _calendar_icon_rect(self, option) -> QRect:
        s = self._ICON_SIZE
        return QRect(option.rect.right() - s - 8, option.rect.center().y() - s // 2, s, s)

    def _draw_calendar_icon(self, painter: QPainter, rect: QRect, color: QColor):
        """Drawn with primitives, not a text/emoji glyph, so it renders in a
        single flat colour consistent with the rest of the app's icons
        (e.g. the play triangle) instead of risking a forced-colour emoji
        presentation for a Unicode calendar character."""
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(color, 1.3)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        body = rect.adjusted(0, 2, 0, 0)
        painter.drawRoundedRect(body, 2, 2)
        painter.drawLine(body.left(), body.top() + 3, body.right(), body.top() + 3)
        painter.drawLine(body.left() + 3, rect.top(), body.left() + 3, body.top() + 4)
        painter.drawLine(body.right() - 3, rect.top(), body.right() - 3, body.top() + 4)
        painter.restore()

    def paint(self, painter: QPainter, option, index):
        f: MediaFile = index.data(MediaFileRole)
        if f is None:
            super().paint(painter, option, index)
            return

        painter.save()
        self._draw_bg(painter, option, f)

        # Source badge — drawn first
        source = ('interpolated'
                  if (f.is_interpolated or f.is_re_anchored)
                  else f.date_source)
        bg_hex, fg_hex = SOURCE_BADGE.get(source, SOURCE_BADGE['none'])

        small = painter.font()
        small.setPointSize(max(7, small.pointSize() - 2))
        badge_fm = QFontMetrics(small)
        pad = 6
        bw = badge_fm.horizontalAdvance(source) + pad * 2
        bh = self.BADGE_H

        # Badge + gap + one line of date text, as a block. Top-aligned in
        # compact mode (68px rows leave little room to spare anyway);
        # vertically centred in expanded mode (140px rows), where top
        # alignment left a lot of dead space below the date.
        date_gap  = 2
        content_h = bh + date_gap + QFontMetrics(option.font).height()
        if self._expanded:
            by = option.rect.y() + max(4, (option.rect.height() - content_h) // 2)
        else:
            by = option.rect.y() + 8
        bx = option.rect.x() + 8

        painter.setFont(small)
        painter.setBrush(QColor(bg_hex))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(bx, by, bw, bh, 3, 3)
        painter.setPen(QColor(fg_hex))
        painter.drawText(bx, by, bw, bh, Qt.AlignCenter, source)

        # Date text — below badge, leaving room for the calendar icon. Shown
        # on every row, including hard anchors: the date it was renamed
        # with can itself be wrong (WhatsApp/iOS share-date metadata), and
        # this is the only way back — see MediaTableModel.set_manual_date.
        date_str = index.data(Qt.DisplayRole) or ''
        icon_allowance = self._ICON_SIZE + 14
        painter.setPen(QColor('#111827'))
        painter.setFont(option.font)
        date_y = by + bh + date_gap
        painter.drawText(
            option.rect.x() + 8, date_y,
            option.rect.width() - 16 - icon_allowance,
            option.rect.bottom() - date_y - 4,
            Qt.AlignTop | Qt.AlignLeft, date_str)

        self._draw_calendar_icon(painter, self._calendar_icon_rect(option), QColor('#6B7280'))

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
        if event.type() == QEvent.MouseButtonDblClick:
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
            # Placeholder. While the thumbnail is still loading it pulses
            # between two greys (phase from the wall clock, so every loading
            # cell pulses in step; MainWindow repaints this column on a
            # timer while any are pending). Once loading has finished
            # without an image it stays a static, non-pulsing grey.
            pr = option.rect.adjusted(6, 4, -6, -4)
            if f and not f.thumbnail_loaded:
                k = 0.5 - 0.5 * math.cos(2 * math.pi * (time.monotonic() % 1.2) / 1.2)
                painter.setBrush(QColor(
                    round(243 + (229 - 243) * k),
                    round(244 + (231 - 244) * k),
                    round(246 + (235 - 246) * k)))
            else:
                painter.setBrush(QColor('#F3F4F6'))
            painter.setPen(QColor('#D1D5DB'))
            painter.drawRoundedRect(pr, 4, 4)
            if f and f.is_video:
                f2 = painter.font()
                f2.setPointSize(16)
                painter.setFont(f2)
                painter.setPen(QColor('#9CA3AF'))
                painter.drawText(pr, Qt.AlignCenter, '▶')

            if f and not f.thumbnail_loaded:
                # Videos keep their ▶ glyph in the centre, so the text sits
                # at the bottom of the box for them; photos get it centred.
                f3 = painter.font()
                f3.setPointSize(7)
                painter.setFont(f3)
                painter.setPen(QColor('#9CA3AF'))
                if f.is_video:
                    painter.drawText(pr.adjusted(0, 0, 0, -3),
                                     Qt.AlignHCenter | Qt.AlignBottom, 'Loading…')
                else:
                    painter.drawText(pr, Qt.AlignCenter, 'Loading…')

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
# ── Date/time picker popup ────────────────────────────────────────────────────
# Opened straight from the calendar icon in the Date taken cell — the cell
# itself never turns into an input. A calendar for the date, and Hour / Min /
# Sec columns you click to pick the time (a typed time field would be no
# better than typing the filename, which is what this is meant to spare
# you). Qt.Popup makes it close on any click outside it (and on Escape).
class DateTimePickerPopup(QDialog):

    committed     = Signal(object)   # a python datetime, from "Set date and time"
    draft_changed = Signal(object)   # a python datetime, when closed any other way

    def __init__(self, initial: datetime, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._committed = False
        self._initial   = initial
        # The app palette's Highlight is a very pale blue (fine for table
        # rows, too faint to show what's picked), so selections get the
        # app's solid blue.
        self.setStyleSheet(
            'QDialog { background: #FFFFFF; border: 1px solid #D1D5DB; } '
            'QCalendarWidget QAbstractItemView:enabled { '
            'selection-background-color: #2563EB; selection-color: #FFFFFF; } '
            'QListWidget { border: 1px solid #E5E7EB; border-radius: 4px; outline: none; } '
            'QListWidget::item { height: 22px; padding: 0px; } '
            'QListWidget::item:hover { background: #EFF6FF; } '
            'QListWidget::item:selected { background: #2563EB; color: #FFFFFF; }')

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        body = QHBoxLayout()
        body.setSpacing(10)

        self._calendar = QCalendarWidget()
        self._calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self._calendar.setSelectedDate(QDate(initial.year, initial.month, initial.day))
        # QCalendarWidget's internal grid paints the selected day's text
        # from the widget's own palette (QPalette.HighlightedText), not
        # from the "selection-color" stylesheet property below — that
        # property has no effect on it, which is why the selected day was
        # still showing dark text on the blue background despite it being
        # set. Setting the palette directly is what actually reaches it.
        cal_palette = self._calendar.palette()
        cal_palette.setColor(QPalette.Highlight, QColor('#2563EB'))
        cal_palette.setColor(QPalette.HighlightedText, QColor('#FFFFFF'))
        self._calendar.setPalette(cal_palette)
        body.addWidget(self._calendar)

        self._hours   = self._time_column(24, initial.hour)
        self._minutes = self._time_column(60, initial.minute)
        self._seconds = self._time_column(60, initial.second)
        for title, column in (('Hour', self._hours), ('Min', self._minutes),
                              ('Sec', self._seconds)):
            box = QVBoxLayout()
            box.setSpacing(4)
            label = QLabel(title)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet('color: #6B7280; font-size: 11px;')
            box.addWidget(label)
            box.addWidget(column)
            body.addLayout(box)
        outer.addLayout(body)

        # What "Set date and time" will apply — no guessing from highlights.
        self._summary = QLabel()
        self._summary.setStyleSheet('color: #111827; font-weight: 600;')
        outer.addWidget(self._summary)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton('Cancel')
        cancel.setStyleSheet(
            'QPushButton { background: #FFFFFF; border: 1px solid #D1D5DB; '
            'border-radius: 6px; padding: 6px 12px; } '
            'QPushButton:hover { background: #F3F4F6; }')
        cancel.clicked.connect(self.close)
        ok = QPushButton('Set date and time')
        ok.setStyleSheet(
            'QPushButton { background: #2563EB; color: #FFFFFF; border: none; '
            'border-radius: 6px; padding: 6px 12px; font-weight: 600; } '
            'QPushButton:hover { background: #1D4ED8; }')
        ok.setDefault(True)
        ok.clicked.connect(self._commit)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        outer.addLayout(buttons)

        self._ok_button = ok
        self._calendar.selectionChanged.connect(self._update_summary)
        self._update_summary()

    def _time_column(self, count: int, selected: int) -> QListWidget:
        """A scrollable column of 00..count-1 to click. The row index IS the
        value, so currentRow() reads it back directly."""
        column = QListWidget()
        column.setFixedWidth(52)
        # Gap between rows, not a taller item box — the row height only needs
        # to fit the text; the gap is what keeps a selected row's background
        # from crowding the values above/below it.
        column.setSpacing(4)
        column.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # wheel still scrolls
        column.setSelectionMode(QAbstractItemView.SingleSelection)
        for i in range(count):
            item = QListWidgetItem(f'{i:02d}')
            item.setTextAlignment(Qt.AlignCenter)
            column.addItem(item)
        column.setCurrentRow(selected)
        column.currentRowChanged.connect(self._update_summary)
        return column

    def value(self) -> datetime:
        d = self._calendar.selectedDate()
        return datetime(d.year(), d.month(), d.day(),
                        self._hours.currentRow(), self._minutes.currentRow(),
                        self._seconds.currentRow())

    def _update_summary(self, *_):
        self._summary.setText(self.value().strftime('%Y-%m-%d   %H:%M:%S'))

    def _center_selected(self):
        for column in (self._hours, self._minutes, self._seconds):
            column.scrollToItem(column.currentItem(), QAbstractItemView.PositionAtCenter)

    def _commit(self):
        self._committed = True
        self.committed.emit(self.value())
        self.close()

    def closeEvent(self, event):
        # Closed any way other than "Set date and time" — Cancel, Escape,
        # a click outside (Qt.Popup's own auto-dismiss), or the whole app
        # losing focus to something else (a notification, say), which is
        # what this exists for: that shouldn't discard whatever was being
        # picked. draft_changed carries the in-progress value out so the
        # next popup opened for this same file can pick up where this one
        # left off — see MediaTableView._open_date_picker.
        #
        # Only if it actually changed from what this popup opened with:
        # MediaTableView keeps just one draft slot (deliberately — see
        # there), so a no-op open-then-close (just glancing at a file's
        # date) would otherwise silently evict a real draft left behind by
        # a different file.
        if not self._committed and self.value() != self._initial:
            self.draft_changed.emit(self.value())
        super().closeEvent(event)

    def show_near(self, anchor: QRect):
        """Show below the anchor rect (global coordinates), or above it if
        there isn't room, and never off the screen edges."""
        self.adjustSize()
        screen = QApplication.screenAt(anchor.center()) or QApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = min(anchor.left(), avail.right() - self.width())
        y = anchor.bottom() + 4
        if y + self.height() > avail.bottom():
            y = anchor.top() - self.height() - 4
        self.move(max(avail.left(), x), max(avail.top(), y))
        self.show()
        self._center_selected()   # needs the final size, so after show()


class MediaTableView(QTableView):
    """QTableView that draws a 1px blue border around each selected row
    instead of flooding the row with a highlight fill, and routes clicks
    on the New filename / Date taken columns explicitly (see
    mousePressEvent) rather than through Qt's editTriggers — each needs
    different, specific behaviour (single click anywhere for the filename
    field; only the calendar icon, never any other click, for the date,
    and that opens a popup picker, not an in-cell editor) that one global
    trigger setting can't express. editTriggers is set to NoEditTriggers in
    MainWindow for exactly this reason."""

    # (filepath, datetime) of the last picker's uncommitted selection, or
    # None. Deliberately just one slot, not a dict of every file ever
    # opened — a draft only matters while you're still mid-edit on that one
    # file; opening the picker for a different file has nothing to do with
    # it and should start fresh, not carry around bookkeeping for files
    # nobody's mid-edit on any more. See _open_date_picker.
    _date_draft = None

    def _open_date_picker(self, index, icon_rect: QRect):
        f: MediaFile = index.data(MediaFileRole)
        initial = (f.effective_date or f.date) if f else None
        # Resume an interrupted edit on this same file (popup closed by
        # something other than "Set date and time" — Escape, a stray
        # click, the app losing focus to a notification) — but only for
        # this file; a draft from a different file is stale and ignored.
        if f is not None and self._date_draft is not None and self._date_draft[0] == f.filepath:
            initial = self._date_draft[1]
        popup = DateTimePickerPopup(initial or datetime.now(), self)
        pidx = QPersistentModelIndex(index)

        def commit(dt):
            model = self.model()
            if model is not None and pidx.isValid() and f is not None:
                model.setData(model.index(pidx.row(), pidx.column()), dt, Qt.EditRole)
                self._date_draft = None   # applied for real — no draft left to remember
                # set_manual_date() repositions the file to its new
                # chronological spot (see MediaTableModel._reposition_by_date)
                # — same as a manual drag, so follow it the same way.
                new_row = next(
                    (i for i, mf in enumerate(model.files()) if mf is f),
                    None)
                if new_row is not None:
                    new_idx = model.index(new_row, COL_FILENAME)
                    self.scrollTo(new_idx, QAbstractItemView.PositionAtCenter)
                    self.setCurrentIndex(new_idx)

        def draft(dt):
            if f is not None:
                self._date_draft = (f.filepath, dt)

        popup.committed.connect(commit)
        popup.draft_changed.connect(draft)
        anchor = QRect(self.viewport().mapToGlobal(icon_rect.topLeft()), icon_rect.size())
        popup.show_near(anchor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # .position() (QPointF) is the non-deprecated replacement for
            # the old .pos() (QPoint) — .toPoint() truncates back to ints,
            # which is fine here since it only ever feeds pixel-rect hit
            # tests (indexAt, QRect.contains), never sub-pixel math.
            pos = event.position().toPoint()
            index = self.indexAt(pos)
            model = self.model()
            if index.isValid() and model is not None:
                f: MediaFile = index.data(MediaFileRole)
                col = index.column()

                if col == COL_PREVIEW and f is not None and not f.is_already_formatted:
                    delegate = self.itemDelegateForColumn(COL_PREVIEW)
                    # Just enough of a QStyleOptionViewItem for the
                    # delegate's rect math, which only ever looks at .rect
                    # — QTableView has no viewOptions() in this Qt6/PySide6
                    # version (removed; initViewItemOption(opt) is the
                    # replacement, but would only be needed if the hit-test
                    # helpers used font/palette too).
                    opt = QStyleOptionViewItem()
                    opt.rect = self.visualRect(index)
                    # Same test as PreviewDelegate.paint uses to draw the
                    # icon, so an unpainted icon can't be clicked.
                    if (f.can_clear_filename
                            and delegate._reset_icon_rect(opt).contains(pos)):
                        if f.is_interpolated:
                            # Weak + moved: the name only exists because of
                            # the move, so "clear" means undo the move
                            # (back to wherever it sat since the last Apply
                            # or load), not just blank the box.
                            model.undo_move(index.row())
                        elif f.manual_date_undo is not None:
                            # Its date came from the picker: blanking
                            # wouldn't restore the date/badge/format it had
                            # before, so "clear" means undo the correction
                            # entirely instead.
                            model.undo_manual_date(index.row())
                        else:
                            # Blank always means "skip this file on Apply."
                            model.setData(index, '', Qt.EditRole)
                            return
                        new_row = next(
                            (i for i, mf in enumerate(model.files()) if mf is f),
                            None)
                        if new_row is not None:
                            new_idx = model.index(new_row, COL_FILENAME)
                            self.scrollTo(new_idx, QAbstractItemView.EnsureVisible)
                            self.setCurrentIndex(new_idx)
                        return
                    self.setCurrentIndex(index)
                    self.edit(index)
                    return

                if col == COL_DATE and f is not None:
                    # Allowed on hard anchors too — see
                    # MediaTableModel.set_manual_date: picking a new date
                    # here is the only way back if a file got renamed from
                    # wrong metadata.
                    delegate = self.itemDelegateForColumn(COL_DATE)
                    opt = QStyleOptionViewItem()
                    opt.rect = self.visualRect(index)
                    icon_rect = delegate._calendar_icon_rect(opt)
                    if icon_rect.contains(pos):
                        # Straight to the picker popup — the cell itself
                        # never becomes an input field.
                        self.setCurrentIndex(index)
                        self._open_date_picker(index, icon_rect)
                        return
                    # Any other click in the date cell is just a normal
                    # selection click.

        super().mousePressEvent(event)

    def viewportEvent(self, event):
        # A tooltip just for the reset icon: it's labelled "x", which reads
        # as "clear this text" — true for most files, but for a moved weak
        # file it undoes the move instead (see mousePressEvent). "Reset"
        # covers both without claiming it's always just a text clear.
        if event.type() == QEvent.ToolTip:
            pos = event.pos()
            index = self.indexAt(pos)
            model = self.model()
            if index.isValid() and model is not None and index.column() == COL_PREVIEW:
                f: MediaFile = index.data(MediaFileRole)
                if f is not None and f.can_clear_filename:
                    delegate = self.itemDelegateForColumn(COL_PREVIEW)
                    opt = QStyleOptionViewItem()
                    opt.rect = self.visualRect(index)
                    if delegate._reset_icon_rect(opt).contains(pos):
                        QToolTip.showText(event.globalPos(), 'Reset', self)
                        return True
            QToolTip.hideText()
        return super().viewportEvent(event)

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

    _heif_warned = False  # set once the missing-pillow-heif dialog has been shown

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Media Reel')
        # Wide enough for the whole toolbar: a QToolBar that runs out of room
        # doesn't stop the window shrinking, it pushes its *last* items —
        # Apply rename — into a » overflow menu. The toolbar needs ~1180px
        # with the "need ordering" controls showing and typical counts;
        # 1200 keeps roughly the same slack the old 1100 had before the
        # Move buttons and selection text joined the row.
        self.setMinimumSize(1200, 600)
        self.resize(1280, 800)
        self._model        = MediaTableModel()
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

        self._btn_expand = QPushButton('⊞  Expand View')
        self._btn_expand.setStyleSheet(self._btn_style())
        toolbar.addWidget(self._btn_expand)
        toolbar.addSeparator()

        # Files needing ordering: a status label with Prev / Next to step
        # through them. One container widget, so a single toolbar action
        # shows/hides all three together (see _set_attention_visible).
        attention = QWidget()
        attention_layout = QHBoxLayout(attention)
        attention_layout.setContentsMargins(0, 0, 0, 0)
        attention_layout.setSpacing(6)   # same as the toolbar's own spacing

        self._lbl_attention = QLabel('⚠  0 file(s) need ordering')
        self._lbl_attention.setStyleSheet(self._label_style_warn())
        attention_layout.addWidget(self._lbl_attention)

        self._btn_prev = QPushButton('Prev')
        self._btn_prev.setStyleSheet(self._btn_style())
        attention_layout.addWidget(self._btn_prev)

        self._btn_next = QPushButton('Next')
        self._btn_next.setStyleSheet(self._btn_style())
        attention_layout.addWidget(self._btn_next)

        self._act_attention = toolbar.addWidget(attention)
        # The separator after the group is hidden with it, or the two
        # separators either side of a hidden group would sit back to back.
        self._sep_attention = toolbar.addSeparator()
        self._set_attention_visible(False)

        # Move buttons, led by how many rows they'll act on — they move the
        # whole selection, so the count says what a click is about to do.
        self._lbl_selected_toolbar = QLabel('')
        self._lbl_selected_toolbar.setStyleSheet(
            'color: #6B7280; font-size: 12px; padding: 0 8px;')
        toolbar.addWidget(self._lbl_selected_toolbar)

        self._btn_up = QPushButton('▲  Move up')
        self._btn_up.setStyleSheet(self._btn_style())
        self._btn_up.setEnabled(False)
        toolbar.addWidget(self._btn_up)

        self._btn_down = QPushButton('▼  Move down')
        self._btn_down.setStyleSheet(self._btn_style())
        self._btn_down.setEnabled(False)
        toolbar.addWidget(self._btn_down)

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
        # No automatic edit triggers (double-click, F2, etc.) — the New
        # filename and Date taken columns route every click explicitly
        # through MediaTableView.mousePressEvent instead, since each needs
        # different, specific behaviour (single click anywhere for the
        # filename field; only the calendar icon for the date, never a
        # double-click) that a single global trigger setting can't express.
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
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

        # Each label after the first carries its own leading "  ·  "
        # separator in its text and is empty when there's nothing to say,
        # so nothing dangles when a count is zero.
        self._lbl_files     = QLabel('No folder loaded')
        self._lbl_selected  = QLabel('')
        self._lbl_to_rename = QLabel('')
        self._lbl_flagged   = QLabel('')

        self._status.addWidget(self._lbl_files)
        self._status.addWidget(self._lbl_to_rename)
        self._status.addWidget(self._lbl_flagged)
        self._status.addWidget(self._lbl_selected)

    def _connect_signals(self):
        self._btn_open.clicked.connect(self._open_folder)
        self._btn_down.pressed.connect(lambda: self._on_move_pressed(1))
        self._btn_down.released.connect(self._on_move_released)
        self._btn_up.pressed.connect(lambda: self._on_move_pressed(-1))
        self._btn_up.released.connect(self._on_move_released)
        self._btn_expand.clicked.connect(self._toggle_expand)
        self._btn_apply.clicked.connect(self._apply_rename)
        self._btn_prev.clicked.connect(lambda: self._step_attention(-1))
        self._btn_next.clicked.connect(lambda: self._step_attention(1))
        self._move_delegate.row_move_requested.connect(
            lambda row, direction: self._move(direction, row))
        self._thumb_delegate.open_file_requested.connect(self._open_file)

        self._model.folder_load_started.connect(self._on_load_started)
        self._model.folder_load_complete.connect(self._on_load_complete)
        self._model.thumbnails_complete.connect(self._on_thumbnails_complete)

        # Repaints just the thumbnail column while thumbnails are loading, so
        # their placeholders pulse (see ThumbnailDelegate.paint).
        self._thumb_pulse_timer = QTimer(self)
        self._thumb_pulse_timer.setInterval(80)
        self._thumb_pulse_timer.timeout.connect(self._repaint_thumb_column)
        self._model.file_progress.connect(self._overlay.set_progress)
        self._model.attention_required.connect(self._on_attention_required)
        self._model.metadata_load_error.connect(self._on_metadata_load_error)
        self._model.rename_complete.connect(self._on_rename_complete)
        self._model.dataChanged.connect(self._on_model_data_changed)
        self._model.layoutChanged.connect(self._refresh_status)

        self._table.selectionModel().selectionChanged.connect(
            self._on_selection_changed)
        # The current row can move without the selection changing (Ctrl+arrow
        # keys), and Prev/Next's enabled state depends on it.
        self._table.selectionModel().currentChanged.connect(
            lambda *_: self._refresh_attention_controls())

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
        # Single choke point for the toolbar buttons, the per-row chevrons
        # and hold-to-repeat. Moving a strong anchor before it's renamed is
        # allowed — if that puts it out of chronological order it
        # re-anchors (averages its neighbours) exactly like any other moved
        # strong anchor; the badge flips to "interpolated" so it's visible
        # if that happens. Nothing touches disk until Apply.
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
        # display_filename, not proposed_filename: a manual override or a
        # blanked ("skip") box must count the same way apply_rename() itself
        # decides what to rename (media_model.py's `pending` filter there
        # uses display_filename too) — proposed_filename alone would ignore
        # both.
        pending = sum(
            1 for f in self._model.files()
            if f.display_filename != f.filename
            and not f.display_filename.startswith('---')
            and f.display_filename != ''
        )
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

    def _attention_target(self, direction: int, flagged: list):
        """Row of the next (+1) or previous (-1) file that needs ordering,
        relative to the table's current row, or None if there isn't one —
        it doesn't wrap, so past the last flagged file Next has nowhere to
        go (and likewise Prev before the first).

        Works from the current row rather than a remembered position in the
        list of flagged files: flagged files move and stop being flagged as
        the user works on them, which would leave a stored position pointing
        at the wrong file. With no current row, Next targets the first
        flagged file and Prev the last. `flagged` is the ascending list of
        flagged row numbers."""
        cur = self._table.currentIndex()
        row = cur.row() if cur.isValid() else None
        if direction > 0:
            return next((i for i in flagged if row is None or i > row), None)
        return next((i for i in reversed(flagged) if row is None or i < row), None)

    def _step_attention(self, direction: int):
        flagged = [i for i, f in enumerate(self._model.files()) if f.needs_attention]
        target = self._attention_target(direction, flagged)
        if target is None:
            return
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
            self._btn_expand.setText('⊟  Compact View')
        else:
            THUMB_W, THUMB_H, ROW_H = COMPACT_THUMB_W, COMPACT_THUMB_H, COMPACT_ROW_H
            self._btn_expand.setText('⊞  Expand View')
        self._thumb_delegate.set_expanded(self._expanded)
        self._date_delegate.set_expanded(self._expanded)
        self._table.verticalHeader().setDefaultSectionSize(ROW_H)
        self._table.setColumnWidth(COL_THUMB, THUMB_W + 16)
        self._table.reset()

    # ── Model callbacks ───────────────────────────────────────────────────────

    def _on_load_started(self, count: int):
        self._progress.setMaximum(count)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._lbl_files.setText(f'Loading {count} files…')
        for lbl in (self._lbl_selected, self._lbl_to_rename, self._lbl_flagged,
                    self._lbl_toolbar_rename, self._lbl_selected_toolbar):
            lbl.setText('')   # counts from the previous folder
        self._btn_apply.setEnabled(False)
        self._btn_up.setEnabled(False)
        self._btn_down.setEnabled(False)
        self._set_attention_visible(False)  # clear stale state from previous folder
        self._overlay.start()
        self._thumb_pulse_timer.start()

    def _on_load_complete(self):
        self._progress.setVisible(False)
        self._overlay.stop()
        self._refresh_status()

        # Once per session, and only when it matters: the folder has HEIC
        # files but pillow-heif isn't installed, so they'll have no
        # thumbnails. Deferred so the dialog doesn't run a nested event loop
        # inside the model's signal emission.
        if (not HEIF_AVAILABLE and not self._heif_warned
                and any(f.ext in ('.heic', '.heif') for f in self._model.files())):
            self._heif_warned = True
            QTimer.singleShot(0, self._warn_heif_missing)

    def _warn_heif_missing(self):
        QMessageBox.warning(
            self, 'HEIC thumbnails unavailable',
            'This folder contains HEIC/HEIF files, but the pillow-heif '
            'package is not installed, so they will not have thumbnails.\n\n'
            'Install it with:\n    pip install -r requirements.txt')

    def _on_thumbnails_complete(self):
        self._thumb_pulse_timer.stop()

    def _repaint_thumb_column(self):
        vp = self._table.viewport()
        vp.update(self._table.columnViewportPosition(COL_THUMB), 0,
                  self._table.columnWidth(COL_THUMB), vp.height())

    def _on_metadata_load_error(self, message: str):
        QMessageBox.critical(
            self, 'Could not read file metadata',
            'exiftool failed to start, so dates could not be read for this '
            'folder\'s files.\n\n'
            f'{message}\n\n'
            'Check that vendor/exiftool.exe is present and not blocked.')

    def _on_attention_required(self, count: int):
        self._refresh_attention_controls()

    def _refresh_attention_controls(self):
        flagged = [i for i, f in enumerate(self._model.files()) if f.needs_attention]
        n = len(flagged)
        if n > 0:
            self._lbl_attention.setText(f'⚠  {n} file(s) need ordering')
            # Greyed out where there's nowhere further to go, rather than
            # silently doing nothing when clicked. Depends on the current
            # row as well as the flagged set, so this also runs on
            # currentChanged (see _connect_signals).
            self._btn_prev.setEnabled(self._attention_target(-1, flagged) is not None)
            self._btn_next.setEnabled(self._attention_target(1, flagged) is not None)
            self._set_attention_visible(True)
        else:
            self._set_attention_visible(False)

    def _set_attention_visible(self, visible: bool):
        self._act_attention.setVisible(visible)
        self._sep_attention.setVisible(visible)

    def _on_rename_complete(self, success: int, errors: int):
        # Taken first: the resort below reorders rows, and the view would
        # otherwise be left wherever that lands it.
        scroll_pos = self._table.verticalScrollBar().value()
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
        self._table.verticalScrollBar().setValue(scroll_pos)


    def _on_selection_changed(self, selected, deselected):
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        for i, f in enumerate(self._model.files()):
            f.selected = (i in rows)
        self._move_delegate.set_multi_select(len(rows) > 1)
        self._refresh_move_buttons()
        self._refresh_status()

    # ── Status / button refresh ───────────────────────────────────────────────

    def _on_model_data_changed(self, top_left, bottom_right, roles=None):
        """dataChanged fires once per thumbnail as they load in, as well as
        for date/rename-state changes. _refresh_status() only depends on the
        latter (file counts, rename/flag counts — all date-driven), so a
        thumbnail-only update (Qt.DecorationRole alone) skips it. Without
        this, ~2000 thumbnails arriving in a burst each re-triggered
        _refresh_status()'s O(n) scans over every file, visibly stalling the
        UI during that phase."""
        if roles and set(roles) == {Qt.DecorationRole}:
            return
        self._refresh_status()

    def _refresh_status(self):
        files = self._model.files()
        total = len(files)
        self._lbl_files.setText(f'{total} files')
        self._btn_apply.setEnabled(self._model.has_pending_renames())

        # display_filename, not proposed_filename — see _apply_rename.
        will_rename = sum(
            1 for f in files
            if f.display_filename != f.filename
            and not f.display_filename.startswith('---')
            and f.display_filename != ''
        )
        flagged  = sum(1 for f in files if f.needs_attention)
        selected = sum(1 for f in files if f.selected)
        # The status bar always shows every count, zero or not (unlike the
        # toolbar's copy of "to be renamed" and the attention button, which
        # only appear when there's something to act on). "need ordering"
        # deliberately matches the toolbar button's wording — same count
        # (needs_attention), same phrase.
        self._lbl_selected.setText(f'  ·  {selected} file(s) selected')
        # Shorter than the status bar's "file(s) selected": the toolbar is
        # nearly full (see setMinimumSize in __init__), and next to the Move
        # buttons "N selected" says the same thing.
        self._lbl_selected_toolbar.setText(f'{selected} selected')
        self._lbl_to_rename.setText(f'  ·  {will_rename} file(s) to be renamed')
        self._lbl_flagged.setText(f'  ·  {flagged} file(s) need ordering')
        self._lbl_toolbar_rename.setText(
            f'{will_rename} file(s) to be renamed.' if will_rename else '')
        self._refresh_attention_controls()

    def _refresh_move_buttons(self):
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

    def _label_style_warn(self) -> str:
        # Amber like the row highlight it refers to, but deliberately no
        # border/background — it's a status readout, not something to click,
        # so it shouldn't be dressed like the buttons beside it.
        return 'color: #92400E; font-size: 12px; font-weight: 500; padding: 0 6px;'


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