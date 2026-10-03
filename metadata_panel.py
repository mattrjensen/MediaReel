"""
Full raw-metadata view for one file — every tag exiftool reports, not just
the handful (date, duration, size) the rename pipeline keeps. Two entry
points share this: the table's per-row Info button (MetadataDialog, a modal
wrapper) and the full-screen preview's Metadata button (MetadataPanel
embedded directly as a side panel). Both read on demand, off the GUI thread,
via a small dedicated QThreadPool.

MetadataPanel is the content; MetadataDialog is MetadataPanel plus a title
bar and a Close button, for the table's standalone entry point.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

import metadata_reader

# Dedicated, not QThreadPool.globalInstance(): that pool is handed every file
# in the folder for thumbnail generation as soon as it loads (see CLAUDE.md's
# Threading section, and _PREVIEW_POOL in preview.py for the bug this
# avoided a repeat of) — a metadata read opened mid-load would otherwise
# queue behind however much of that backlog was still outstanding. One
# thread is enough: this is a single on-demand read, never a batch.
_METADATA_POOL = QThreadPool()
_METADATA_POOL.setMaxThreadCount(1)

_PANEL_STYLE_LIGHT = """
QWidget#metadataPanel { background: #F9FAFB; border-right: 1px solid #E5E7EB; }
QWidget#metadataPanel QLabel#metadataTitle {
    font-size: 13px; font-weight: 600; color: #111827; padding: 10px 12px 6px 12px;
}
QWidget#metadataPanel QLabel#metadataStatus { color: #6B7280; padding: 12px; }
QWidget#metadataPanel QTableWidget {
    background: #FFFFFF; border: 1px solid #E5E7EB; gridline-color: #F3F4F6;
    font-size: 12px; color: #111827;
}
QWidget#metadataPanel QTableWidget::item { padding: 3px 6px; }
QWidget#metadataPanel QHeaderView::section {
    background: #F3F4F6; color: #374151; font-size: 11px; font-weight: 600;
    border: none; border-bottom: 1px solid #E5E7EB; padding: 4px 6px;
}
"""

# Used only when embedded in the full-screen preview (dark=True), to match
# its black/navy theme (preview.py's _BAR_STYLE) rather than the table
# dialog's light one. Same palette: #111827 background, #F9FAFB/#9CA3AF
# text, #1F2937 panel chrome, #374151 borders.
_PANEL_STYLE_DARK = """
QWidget#metadataPanel { background: #111827; border-right: 1px solid #374151; }
QWidget#metadataPanel QLabel#metadataTitle {
    font-size: 13px; font-weight: 600; color: #F9FAFB; padding: 10px 12px 6px 12px;
}
QWidget#metadataPanel QLabel#metadataStatus { color: #9CA3AF; padding: 12px; }
QWidget#metadataPanel QTableWidget {
    background: #1F2937; border: 1px solid #374151; gridline-color: #374151;
    font-size: 12px; color: #F3F4F6; alternate-background-color: #243041;
}
QWidget#metadataPanel QTableWidget::item { padding: 3px 6px; }
QWidget#metadataPanel QHeaderView::section {
    background: #1F2937; color: #9CA3AF; font-size: 11px; font-weight: 600;
    border: none; border-bottom: 1px solid #374151; padding: 4px 6px;
}
"""


class _MetadataSignals(QObject):
    loaded = Signal(str, dict)   # filepath, {'Group:Tag': value}
    failed = Signal(str, str)    # filepath, error message


class _MetadataLoader(QRunnable):
    def __init__(self, filepath: str, signals: _MetadataSignals):
        super().__init__()
        self._filepath = filepath
        # Held here, not just captured by the lambda a caller might use, so
        # the QObject can't be collected while a queued emit is still in
        # flight — the same gotcha MediaTableModel._active_workers exists
        # for (see CLAUDE.md's Threading section).
        self._signals = signals
        self.setAutoDelete(True)

    def run(self):
        try:
            tags = metadata_reader.read_all_metadata(self._filepath)
        except Exception as e:
            try:
                self._signals.failed.emit(self._filepath, str(e) or type(e).__name__)
            except RuntimeError:
                pass   # the panel/dialog closed while this was in flight
            return
        try:
            self._signals.loaded.emit(self._filepath, tags)
        except RuntimeError:
            pass


class MetadataPanel(QWidget):
    """The content: a title, and either a status line ("Loading…" / an
    error) or a two-column tag/value table. show_file() can be called again
    for a different file at any time (the preview does this on every
    navigation while the panel is open) — a result for a file that's no
    longer the one requested is dropped, so quickly stepping past several
    files can't land a stale read on the wrong one."""

    def __init__(self, parent=None, dark: bool = False):
        super().__init__(parent)
        self.setObjectName('metadataPanel')
        # dark=True for the preview's embedded side panel, to match its
        # black/navy theme; the table's MetadataDialog uses the default
        # light one, matching the rest of the table/dialogs.
        self.setStyleSheet(_PANEL_STYLE_DARK if dark else _PANEL_STYLE_LIGHT)
        # The status label's colour is set per-state (_set_status below), so
        # it's picked here rather than left to the stylesheet, which a
        # per-widget setStyleSheet call would override anyway.
        self._status_colour = '#9CA3AF' if dark else '#6B7280'
        self._error_colour = '#FCA5A5' if dark else '#B91C1C'
        self._pending_filepath: Optional[str] = None
        self._signals = _MetadataSignals(self)
        self._signals.loaded.connect(self._on_loaded)
        self._signals.failed.connect(self._on_failed)

        self._title = QLabel()
        self._title.setObjectName('metadataTitle')
        self._status = QLabel()
        self._status.setObjectName('metadataStatus')
        self._status.setWordWrap(True)
        # QLabel vertically centres by default — fine for a short line in a
        # small box, but the status page fills the whole body area (so the
        # table and the status always occupy the same space), and centring
        # in that left "Loading…" stranded well below the title instead of
        # reading as its immediate follow-up.
        self._status.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(['Tag', 'Value'])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(True)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)

        # A QStackedWidget, not two widgets toggled with setVisible(): with
        # only the table marked as the layout's stretchy item, hiding it
        # left nothing to absorb the panel's extra height, and the two
        # labels ended up splitting it 50/50 instead — "Loading…" stranded
        # in the middle of the panel rather than sitting under the title.
        # A stack always sizes to whichever page is current, so this can't
        # happen — the same fix preview.py's _pages already uses for
        # stage/video.
        self._body = QStackedWidget()
        self._body.addWidget(self._status)
        self._body.addWidget(self._table)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._title)
        layout.addWidget(self._body, 1)
        self._body.setCurrentWidget(self._status)

    def _set_status(self, text: str, *, error: bool = False):
        self._status.setText(text)
        self._status.setStyleSheet(
            f'color: {self._error_colour if error else self._status_colour};')
        self._body.setCurrentWidget(self._status)

    def show_file(self, filepath: str, filename: str):
        self._title.setText(filename)
        self._pending_filepath = filepath
        self._table.setRowCount(0)
        self._set_status('Loading metadata…')
        _METADATA_POOL.start(_MetadataLoader(filepath, self._signals))

    def _on_loaded(self, filepath: str, tags: dict):
        if filepath != self._pending_filepath:
            return   # superseded by a later show_file() call
        if not tags:
            self._set_status('No metadata found.')
            return
        self._table.setRowCount(len(tags))
        for row, (tag, value) in enumerate(tags.items()):
            tag_item = QTableWidgetItem(tag)
            tag_item.setFlags(tag_item.flags() & ~Qt.ItemIsEditable)
            value_item = QTableWidgetItem(str(value))
            value_item.setFlags(value_item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(row, 0, tag_item)
            self._table.setItem(row, 1, value_item)
        self._table.resizeRowsToContents()
        self._body.setCurrentWidget(self._table)

    def _on_failed(self, filepath: str, message: str):
        if filepath != self._pending_filepath:
            return
        self._set_status(f"Couldn't read metadata.\n\n{message}", error=True)


class MetadataDialog(QDialog):
    """MetadataPanel plus a title bar and Close button — the table's Info
    button opens this standalone, modal (simplest: no risk of several of
    these piling up for different rows at once)."""

    def __init__(self, filepath: str, filename: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'Metadata — {filename}')
        self.resize(560, 640)

        self._panel = MetadataPanel(self)
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        close.setDefault(True)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)
        buttons.setContentsMargins(12, 8, 12, 8)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._panel, 1)
        layout.addLayout(buttons)

        self._panel.show_file(filepath, filename)
