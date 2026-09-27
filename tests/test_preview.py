"""
Tests for the full-screen preview (preview.py): the photo decoder, time
formatting, and PreviewWindow's navigation / delete-landing logic.

Navigation and delete run against synthetic MediaFile lists whose paths don't
exist — the window's *decisions* (which file is next, where to land after a
delete, when to close) don't depend on the pixels, and a missing file just
makes the background loader return None. Playback itself is covered by
tests/diagnostic_video_playback.py, which needs real video files.
"""
from PIL import Image

import pytest

from media_model import MediaFile
from metadata_reader import DATE_SOURCE_FILENAME
import preview
from preview import PreviewWindow, format_time, load_preview_image


def _file(name):
    return MediaFile(
        filepath=f'/no/such/dir/{name}', filename=name, ext='.jpg',
        is_video=False, is_already_formatted=True, date=None,
        date_source=DATE_SOURCE_FILENAME, stripped_filename=name)


class TestFormatTime:
    def test_minutes_and_seconds(self):
        assert format_time(0) == '0:00'
        assert format_time(5_000) == '0:05'
        assert format_time(65_000) == '1:05'

    def test_truncates_not_rounds(self):
        assert format_time(1_999) == '0:01'

    def test_hours(self):
        assert format_time(3_725_000) == '1:02:05'

    def test_negative_is_zero(self):
        assert format_time(-5) == '0:00'


class TestLoadPreviewImage:
    def test_shrinks_to_fit_keeping_aspect(self, tmp_path):
        p = tmp_path / 'big.jpg'
        Image.new('RGB', (4000, 3000), 'red').save(p)
        img = load_preview_image(str(p), 1920, 1080)
        assert (img.width(), img.height()) == (1440, 1080)

    def test_never_enlarges_a_small_file(self, tmp_path):
        p = tmp_path / 'small.png'
        Image.new('RGB', (300, 200), 'blue').save(p)
        img = load_preview_image(str(p), 1920, 1080)
        assert (img.width(), img.height()) == (300, 200)

    def test_applies_exif_orientation(self, tmp_path):
        # Stored landscape (400x200) with orientation 6 = "rotate 90° to view".
        p = tmp_path / 'rotated.jpg'
        exif = Image.Exif()
        exif[0x0112] = 6
        Image.new('RGB', (400, 200), 'green').save(p, exif=exif)
        img = load_preview_image(str(p), 1920, 1080)
        assert (img.width(), img.height()) == (200, 400)

    def test_image_with_alpha_is_flattened(self, tmp_path):
        p = tmp_path / 'alpha.png'
        Image.new('RGBA', (50, 50), (255, 0, 0, 128)).save(p)
        assert load_preview_image(str(p), 100, 100) is not None

    def test_unreadable_file_is_none_not_an_exception(self, tmp_path):
        p = tmp_path / 'not_an_image.jpg'
        p.write_bytes(b'this is not a jpeg')
        assert load_preview_image(str(p), 100, 100) is None
        assert load_preview_image(str(tmp_path / 'missing.jpg'), 100, 100) is None

    def test_result_owns_its_pixels(self, tmp_path):
        # Returned from a worker thread, so it must not depend on a buffer
        # that goes away when the function returns.
        p = tmp_path / 'a.png'
        Image.new('RGB', (20, 20), (10, 20, 30)).save(p)
        img = load_preview_image(str(p), 100, 100)
        assert img.pixelColor(5, 5).getRgb()[:3] == (10, 20, 30)


class _Harness:
    """A model with a fake delete, and a PreviewWindow on it."""

    def __init__(self, model, names, start, delete_ok=True):
        self.model = model
        model._files = [_file(n) for n in names]
        self.by_name = {f.filename: f for f in model._files}
        self.delete_ok = delete_ok
        self.delete_calls = []
        self.win = PreviewWindow(model, self.by_name[start], self._delete)

    def _delete(self, f, parent, before_delete=None):
        self.delete_calls.append(f.filename)
        if not self.delete_ok:
            return False          # the user said No / the trash failed
        if before_delete:
            before_delete()
        row = next(i for i, mf in enumerate(self.model._files) if mf is f)
        self.model.delete_file(row)
        return True

    @property
    def shown(self):
        f = self.win.current_file
        return f.filename if f else None


@pytest.fixture
def harness(model):
    made = []

    def make(names, start, **kw):
        h = _Harness(model, names, start, **kw)
        made.append(h)
        return h
    yield make
    for h in made:
        h.win.done(0)
        h.win.deleteLater()


NAMES = ['a.jpg', 'b.jpg', 'c.jpg', 'd.jpg']


class TestNavigation:
    def test_opens_on_the_given_file(self, harness):
        assert harness(NAMES, 'c.jpg').shown == 'c.jpg'

    def test_next_and_prev_follow_the_list_order(self, harness):
        h = harness(NAMES, 'b.jpg')
        h.win._step(1)
        assert h.shown == 'c.jpg'
        h.win._step(-1)
        h.win._step(-1)
        assert h.shown == 'a.jpg'

    def test_follows_the_tables_current_order_not_the_folders(self, harness):
        # The user has moved d to the front: "next" after it must be a.
        h = harness(['d.jpg', 'a.jpg', 'b.jpg', 'c.jpg'], 'd.jpg')
        h.win._step(1)
        assert h.shown == 'a.jpg'

    def test_dead_ends_at_both_ends(self, harness):
        h = harness(NAMES, 'a.jpg')
        h.win._step(-1)
        assert h.shown == 'a.jpg'
        h.win._step(1); h.win._step(1); h.win._step(1)
        assert h.shown == 'd.jpg'
        h.win._step(1)
        assert h.shown == 'd.jpg'

    def test_prev_next_buttons_disable_at_the_ends(self, harness):
        h = harness(NAMES, 'a.jpg')
        assert not h.win._btn_prev.isEnabled() and h.win._btn_next.isEnabled()
        h.win._step(1)
        assert h.win._btn_prev.isEnabled() and h.win._btn_next.isEnabled()
        h.win._step(1); h.win._step(1)
        assert h.win._btn_prev.isEnabled() and not h.win._btn_next.isEnabled()

    def test_position_counter_in_the_info_line(self, harness):
        h = harness(NAMES, 'c.jpg')
        assert h.win._lbl_meta.text().startswith('3 / 4')

    def test_pending_rename_is_shown(self, harness):
        h = harness(NAMES, 'a.jpg')
        f = h.by_name['a.jpg']
        f.proposed_filename = '20240101_090000_a.jpg'
        h.win._refresh_info()
        assert 'will be renamed to 20240101_090000_a.jpg' in h.win._lbl_meta.text()

    def test_placeholder_is_not_shown_as_a_rename(self, harness):
        h = harness(NAMES, 'a.jpg')
        h.by_name['a.jpg'].proposed_filename = '--- put me somewhere ---'
        h.win._refresh_info()
        assert 'will be renamed' not in h.win._lbl_meta.text()


class TestDelete:
    def test_lands_on_the_next_file(self, harness):
        h = harness(NAMES, 'b.jpg')
        h.win._delete_current()
        assert h.delete_calls == ['b.jpg']
        assert h.shown == 'c.jpg'
        assert [f.filename for f in h.model.files()] == ['a.jpg', 'c.jpg', 'd.jpg']

    def test_deleting_the_last_file_lands_on_the_one_above(self, harness):
        h = harness(NAMES, 'd.jpg')
        h.win._delete_current()
        assert h.shown == 'c.jpg'

    def test_position_counter_updates_after_a_delete(self, harness):
        h = harness(NAMES, 'b.jpg')
        h.win._delete_current()
        assert h.win._lbl_meta.text().startswith('2 / 3')

    def test_deleting_the_only_file_closes_the_preview(self, harness):
        h = harness(['only.jpg'], 'only.jpg')
        finished = []
        h.win.finished.connect(finished.append)
        h.win._delete_current()
        assert h.shown is None
        assert finished == [0]

    def test_declined_delete_changes_nothing(self, harness):
        h = harness(NAMES, 'b.jpg', delete_ok=False)
        h.win._delete_current()
        assert h.shown == 'b.jpg'
        assert len(h.model.files()) == 4

    def test_media_is_released_only_after_the_user_confirms(self, harness):
        # The callback stands in for MainWindow._delete_file, which calls
        # before_delete only once the user has said Yes. A No must not have
        # stopped a playing video.
        h = harness(NAMES, 'b.jpg', delete_ok=False)
        released = []
        h.win._release_media = lambda: released.append(True)
        h.win._delete_current()
        assert released == []

    def test_media_is_released_before_a_confirmed_delete(self, harness):
        h = harness(NAMES, 'b.jpg')
        order = []
        h.win._release_media = lambda: order.append('released')
        real_delete = h.model.delete_file
        h.model.delete_file = lambda row: (order.append('deleted'), real_delete(row))[1]
        h.win._delete_current()
        # (_show_file also releases when moving on to the next file, so look
        # at what came first.)
        assert order[:2] == ['released', 'deleted']

    def test_a_delete_that_fails_after_release_puts_the_file_back_up(self, harness):
        h = harness(NAMES, 'b.jpg')
        shown = []
        real_show = h.win._show_file
        h.win._show_file = lambda f: (shown.append(f.filename), real_show(f))[1]

        def failing_delete(f, parent, before_delete=None):
            before_delete()       # confirmed, media let go...
            return False          # ...but the trash refused
        h.win._delete_file = failing_delete
        h.win._delete_current()
        assert shown == ['b.jpg']
        assert h.shown == 'b.jpg'
        assert len(h.model.files()) == 4

    def test_deleted_files_photo_is_dropped_from_the_cache(self, harness):
        h = harness(NAMES, 'b.jpg')
        h.win._cache['/no/such/dir/b.jpg'] = None
        h.win._delete_current()
        assert '/no/such/dir/b.jpg' not in h.win._cache


class TestPhotoCache:
    def test_cache_is_bounded(self, harness):
        h = harness(NAMES, 'a.jpg')
        h.win._cache.clear()
        for i in range(preview.CACHE_SIZE + 3):
            h.win._on_image_loaded(f'/no/such/dir/{i}.jpg', None)
        assert len(h.win._cache) == preview.CACHE_SIZE

    def test_oldest_is_evicted_first(self, harness):
        h = harness(NAMES, 'a.jpg')
        h.win._cache.clear()
        for i in range(preview.CACHE_SIZE + 1):
            h.win._on_image_loaded(f'/no/such/dir/{i}.jpg', None)
        assert '/no/such/dir/0.jpg' not in h.win._cache

    def test_a_result_for_another_file_does_not_replace_the_current_one(self, harness):
        h = harness(NAMES, 'a.jpg')
        h.win._stage.show_message('Loading…')
        h.win._on_image_loaded('/no/such/dir/zzz.jpg', preview.QImage(10, 10, preview.QImage.Format_RGB888))
        assert h.win._stage._image is None
