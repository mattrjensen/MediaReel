"""
Full-screen preview: a bigger look at one file than the table's Expanded View
gives, for the times you need to actually review it. Opened by double-clicking
a thumbnail.

Left / Right step to the previous / next file in the *table's* current order
(up / down the list), so what you see is the staged sequence rather than
whatever order the folder happens to be in. Delete removes the file (through
the same confirm-and-Recycle-Bin path as the row's trash button). Esc closes;
MainWindow then selects and scrolls to whichever file was showing.

Photos are decoded off the main thread at screen size (never full resolution —
this is a fit-to-screen viewer, not a zoomer) and the neighbours are
prefetched so stepping through is quick. Videos play through QMediaPlayer.
"""
from __future__ import annotations

import os
from collections import OrderedDict
from typing import Callable, Optional

from PySide6.QtCore import (
    QObject, QRect, QRunnable, QThreadPool, QUrl, Qt, Signal,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider,
    QStackedWidget, QStyle, QVBoxLayout, QWidget,
)

from media_model import MediaFile, format_file_size

# How many decoded photos to keep: the one showing plus its neighbours, and a
# little slack so a quick back-and-forth doesn't re-decode.
CACHE_SIZE = 5

# Small images are enlarged to fill the screen, but not without limit — a
# thumbnail-sized file blown up 20x is just a blur.
MAX_UPSCALE = 3.0

# @NAME@ is swapped in with str.replace: str.format would choke on the CSS braces.
_BAR_STYLE = """
QWidget#@NAME@ { background: #111827; }
QWidget#@NAME@ QLabel { color: #F3F4F6; }
QWidget#@NAME@ QPushButton {
    background: #1F2937; color: #F9FAFB; border: 1px solid #374151;
    border-radius: 6px; padding: 6px 14px; font-size: 13px;
}
QWidget#@NAME@ QPushButton:hover { background: #374151; }
QWidget#@NAME@ QPushButton:disabled { color: #6B7280; border-color: #1F2937; }
QWidget#@NAME@ QFrame#previewSep { background: #4B5563; border: none; }
QWidget#@NAME@ QPushButton#previewDelete { color: #FCA5A5; border-color: #7F1D1D; }
QWidget#@NAME@ QPushButton#previewDelete:hover { background: #7F1D1D; }
QWidget#@NAME@ QSlider::groove:horizontal {
    height: 4px; background: #374151; border-radius: 2px;
}
QWidget#@NAME@ QSlider::sub-page:horizontal {
    background: #2563EB; border-radius: 2px;
}
QWidget#@NAME@ QSlider::handle:horizontal {
    background: #F9FAFB; width: 14px; margin: -5px 0; border-radius: 7px;
}
"""


def format_time(ms: int) -> str:
    """m:ss for a position in milliseconds (h:mm:ss from an hour up)."""
    seconds = max(int(ms), 0) // 1000
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def load_preview_image(filepath: str, max_w: int, max_h: int) -> Optional[QImage]:
    """
    Decode a photo to fit within max_w x max_h (never larger than the file
    itself), with EXIF orientation applied. None if it can't be read.

    Runs on a worker thread, so it returns a QImage (safe off the GUI thread)
    that owns its pixels — never a QPixmap.
    """
    try:
        from PIL import Image, ImageOps
        img = Image.open(filepath)
        if img.format == 'JPEG':
            # Let libjpeg decode at a reduced scale — several times faster on
            # a 24MP photo, and we're about to shrink it anyway. The box is
            # square so an EXIF rotation can't leave it short in one axis.
            side = max(max_w, max_h)
            img.draft('RGB', (side, side))
        ImageOps.exif_transpose(img, in_place=True)
        img.thumbnail((max_w, max_h), Image.LANCZOS)
        img = img.convert('RGB')
        data = img.tobytes('raw', 'RGB')
        # copy(): detach from `data` so the QImage owns its pixels.
        return QImage(data, img.width, img.height, img.width * 3,
                      QImage.Format_RGB888).copy()
    except Exception:
        return None


class _LoadSignals(QObject):
    loaded = Signal(str, object)   # filepath, QImage or None if unreadable


class _ImageLoader(QRunnable):
    def __init__(self, filepath: str, max_w: int, max_h: int, signals: _LoadSignals):
        super().__init__()
        self._filepath = filepath
        self._size = (max_w, max_h)
        # Held here so the QObject can't be collected while a queued emit is
        # still in flight (see MediaTableModel._active_workers for the gotcha).
        self._signals = signals
        self.setAutoDelete(True)

    def run(self):
        img = load_preview_image(self._filepath, *self._size)
        try:
            self._signals.loaded.emit(self._filepath, img)
        except RuntimeError:
            pass   # the window closed while this was decoding


class _StageView(QWidget):
    """Paints one photo scaled to fit, or a centred message ("Loading…",
    "Can't display this file")."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image: Optional[QImage] = None
        self._message = ''
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def show_image(self, image: QImage):
        self._image, self._message = image, ''
        self.update()

    def show_message(self, text: str):
        self._image, self._message = None, text
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#000000'))
        if self._image is not None and not self._image.isNull():
            img = self._image
            scale = min(self.width() / img.width(), self.height() / img.height())
            scale = min(scale, MAX_UPSCALE)
            w, h = int(img.width() * scale), int(img.height() * scale)
            target = QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(target, img)
        elif self._message:
            p.setPen(QColor('#9CA3AF'))
            p.drawText(self.rect(), Qt.AlignCenter, self._message)
        p.end()


class _SeekSlider(QSlider):
    """A slider where a click jumps straight to that point, instead of
    paging towards it a step at a time."""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.maximum() > 0:
            value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), int(event.position().x()), self.width())
            self.setValue(value)
            self.sliderMoved.emit(value)
        super().mousePressEvent(event)


def _separator() -> QFrame:
    """A thin vertical rule between groups of buttons in the top bar."""
    line = QFrame()
    line.setObjectName('previewSep')
    line.setFixedSize(1, 24)
    return line


def _button(text: str, on_click: Callable, name: str = '') -> QPushButton:
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    # No focus, no default: the arrow keys and Space belong to the window
    # (step / play), not to whichever button was clicked last.
    b.setFocusPolicy(Qt.NoFocus)
    b.setAutoDefault(False)
    b.clicked.connect(on_click)
    return b


class PreviewWindow(QDialog):
    """
    delete_file(f, parent, before_delete) -> bool is MainWindow's delete: it
    confirms, calls before_delete() just before touching the file, and returns
    whether the file is gone. before_delete is how a playing video lets go of
    its file first — Windows won't move an open file to the Recycle Bin.
    """

    def __init__(self, model, start_file: MediaFile, delete_file: Callable,
                 parent=None):
        super().__init__(parent)
        self._model = model
        self._delete_file = delete_file
        self._file: Optional[MediaFile] = start_file
        self._cache: OrderedDict[str, Optional[QImage]] = OrderedDict()
        self._pending: set[str] = set()
        self._signals = _LoadSignals(self)
        self._signals.loaded.connect(self._on_image_loaded)

        self.setWindowTitle('Preview')
        self.setModal(True)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor('#000000'))
        self.setPalette(pal)

        self._build_ui()
        self._build_player()
        self._show_file(start_file)

    # ── construction ─────────────────────────────────────────────────────────

    def _build_ui(self):
        # Top bar, left to right: what and where this file is (far left), then
        # Open in default app | Prev / Next | Delete, Close.
        self._top = QWidget()
        self._top.setObjectName('previewTop')
        self._top.setStyleSheet(_BAR_STYLE.replace('@NAME@', 'previewTop'))
        top = QHBoxLayout(self._top)
        top.setContentsMargins(12, 8, 12, 8)
        top.setSpacing(8)
        self._btn_prev = _button('‹  Prev', lambda: self._step(-1))
        self._btn_next = _button('Next  ›', lambda: self._step(1))
        self._lbl_name = QLabel()
        self._lbl_name.setStyleSheet('font-size: 15px; font-weight: 600;')
        self._lbl_meta = QLabel()
        self._lbl_meta.setStyleSheet('font-size: 12px; color: #9CA3AF;')
        for lbl in (self._lbl_name, self._lbl_meta):
            # Ignored: a long name mustn't force the window's minimum width.
            lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(self._lbl_name)
        text.addWidget(self._lbl_meta)
        top.addLayout(text, 1)
        top.addWidget(_button('Open in default app', self._open_externally))
        top.addWidget(_separator())
        top.addWidget(self._btn_prev)
        top.addWidget(self._btn_next)
        top.addWidget(_separator())
        top.addWidget(_button('Delete', self._delete_current, 'previewDelete'))
        top.addWidget(_button('Close', self.reject))

        # Centre: the photo/message view or the video, one at a time.
        self._stage = _StageView()
        self._video = QVideoWidget()
        self._video.setStyleSheet('background: #000000;')
        self._pages = QStackedWidget()
        self._pages.addWidget(self._stage)
        self._pages.addWidget(self._video)

        # Bottom bar: video transport. Hidden for photos.
        self._controls = QWidget()
        self._controls.setObjectName('previewControls')
        self._controls.setStyleSheet(_BAR_STYLE.replace('@NAME@', 'previewControls'))
        ctl = QHBoxLayout(self._controls)
        ctl.setContentsMargins(12, 8, 12, 8)
        ctl.setSpacing(10)
        self._btn_play = _button('Play', self._toggle_play)
        self._btn_play.setMinimumWidth(80)
        self._slider = _SeekSlider(Qt.Horizontal)
        self._slider.setFocusPolicy(Qt.NoFocus)
        self._lbl_time = QLabel('0:00 / 0:00')
        self._btn_mute = _button('Mute', self._toggle_mute)
        self._btn_mute.setMinimumWidth(80)
        ctl.addWidget(self._btn_play)
        ctl.addWidget(self._slider, 1)
        ctl.addWidget(self._lbl_time)
        ctl.addWidget(self._btn_mute)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._top)
        root.addWidget(self._pages, 1)
        root.addWidget(self._controls)

    def _build_player(self):
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._player.errorOccurred.connect(self._on_player_error)
        self._slider.sliderMoved.connect(self._player.setPosition)

    # ── public ───────────────────────────────────────────────────────────────

    @property
    def current_file(self) -> Optional[MediaFile]:
        """The file showing (or last showing) — what MainWindow selects on
        close. None only if the last file was deleted."""
        return self._file

    # ── navigation ───────────────────────────────────────────────────────────

    def _index(self) -> int:
        """Row of the current file in the table's current order, by identity
        (rows move and disappear, so a stored row number would go stale)."""
        for i, f in enumerate(self._model.files()):
            if f is self._file:
                return i
        return -1

    def _step(self, delta: int):
        files = self._model.files()
        i = self._index()
        target = i + delta
        if i < 0 or not 0 <= target < len(files):
            return   # dead end, like the toolbar's Prev/Next — no wrapping
        self._show_file(files[target])

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Left:
            self._step(-1)
        elif key == Qt.Key_Right:
            self._step(1)
        elif key == Qt.Key_Delete:
            self._delete_current()
        elif key == Qt.Key_Space:
            if self._file is not None and self._file.is_video:
                self._toggle_play()
        else:
            super().keyPressEvent(event)   # Esc closes

    # ── showing a file ───────────────────────────────────────────────────────

    def _show_file(self, f: MediaFile):
        self._release_media()
        self._file = f
        self._refresh_info()
        if f.is_video:
            self._pages.setCurrentWidget(self._video)
            self._controls.setVisible(True)
            self._slider.setValue(0)
            self._player.setSource(QUrl.fromLocalFile(f.filepath))
            self._player.play()
        else:
            self._pages.setCurrentWidget(self._stage)
            self._controls.setVisible(False)
            self._show_photo(f.filepath)
        self._prefetch_neighbours()

    def _refresh_info(self):
        f = self._file
        files = self._model.files()
        i = self._index()
        self._lbl_name.setText(f.filename)

        parts = [f'{i + 1} / {len(files)}']
        date = f.effective_date or f.date
        if date:
            parts.append(f'{date:%Y-%m-%d %H:%M:%S} ({f.date_source})')
        size = format_file_size(f.size_bytes)
        if size:
            parts.append(size)
        new = f.display_filename
        if new and new != f.filename and not new.startswith('---'):
            parts.append(f'will be renamed to {new}')
        self._lbl_meta.setText('   ·   '.join(parts))

        self._btn_prev.setEnabled(i > 0)
        self._btn_next.setEnabled(0 <= i < len(files) - 1)

    # ── photos ───────────────────────────────────────────────────────────────

    def _target_size(self) -> tuple[int, int]:
        screen = self.screen()
        size, dpr = screen.size(), screen.devicePixelRatio()
        return int(size.width() * dpr), int(size.height() * dpr)

    def _show_photo(self, path: str):
        if path in self._cache:
            self._cache.move_to_end(path)
            self._display(self._cache[path])
            return
        self._stage.show_message('Loading…')
        self._request(path)

    def _display(self, image: Optional[QImage]):
        if image is None:
            self._stage.show_message("Can't display this file")
        else:
            self._stage.show_image(image)

    def _request(self, path: str):
        if path in self._cache or path in self._pending:
            return
        self._pending.add(path)
        loader = _ImageLoader(path, *self._target_size(), self._signals)
        QThreadPool.globalInstance().start(loader)

    def _on_image_loaded(self, path: str, image):
        self._pending.discard(path)
        self._cache[path] = image
        self._cache.move_to_end(path)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        if self._file is not None and path == self._file.filepath:
            self._display(image)

    def _prefetch_neighbours(self):
        files = self._model.files()
        i = self._index()
        for j in (i + 1, i - 1):
            if 0 <= j < len(files) and not files[j].is_video:
                self._request(files[j].filepath)

    # ── video ────────────────────────────────────────────────────────────────

    def _release_media(self):
        """Stop and let go of the current video file. Deleting needs this
        (an open file can't go to the Recycle Bin), and it stops one clip's
        sound carrying on under the next file."""
        self._player.stop()
        self._player.setSource(QUrl())

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
            return
        if self._player.mediaStatus() == QMediaPlayer.EndOfMedia:
            self._player.setPosition(0)   # replay from the start
        self._player.play()

    def _toggle_mute(self):
        muted = not self._audio.isMuted()
        self._audio.setMuted(muted)
        self._btn_mute.setText('Unmute' if muted else 'Mute')

    def _on_position(self, ms: int):
        if not self._slider.isSliderDown():
            self._slider.setValue(ms)
        self._lbl_time.setText(
            f'{format_time(ms)} / {format_time(self._player.duration())}')

    def _on_duration(self, ms: int):
        self._slider.setRange(0, max(ms, 0))
        self._lbl_time.setText(
            f'{format_time(self._player.position())} / {format_time(ms)}')

    def _on_playback_state(self, state):
        self._btn_play.setText(
            'Pause' if state == QMediaPlayer.PlayingState else 'Play')

    def _on_player_error(self, error, message: str):
        if self._file is None or not self._file.is_video:
            return
        self._pages.setCurrentWidget(self._stage)
        self._stage.show_message(
            f"Can't play this video.\n\n{message}\n\n"
            f'"Open in default app" may be able to.')

    # ── actions ──────────────────────────────────────────────────────────────

    def _open_externally(self):
        if self._file is None:
            return
        # Let go first: some players won't open a file another process has.
        self._release_media()
        try:
            os.startfile(self._file.filepath)
        except Exception:
            pass

    def _delete_current(self):
        f = self._file
        files = self._model.files()
        i = self._index()
        if f is None or i < 0:
            return
        # Where to land afterwards: the next file down the list, or the one
        # above if this was the last.
        if i + 1 < len(files):
            landing = files[i + 1]
        elif i > 0:
            landing = files[i - 1]
        else:
            landing = None

        released = []
        deleted = self._delete_file(
            f, self, before_delete=lambda: (self._release_media(), released.append(True)))
        if not deleted:
            if released:
                self._show_file(f)   # it stayed; put the video back
            return

        self._cache.pop(f.filepath, None)
        if landing is None:
            self._file = None
            self.reject()
        else:
            self._show_file(landing)

    def done(self, result):
        self._release_media()
        super().done(result)
