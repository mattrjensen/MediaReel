"""
Tests for the Size column's formatting and MediaTableModel.delete_file().

Uses synthetic MediaFile lists. delete_file() sends real files to the Recycle
Bin, so nothing here ever lets it reach one: files that are missing from disk
just lose their row, and the "file exists" cases patch QFile.moveToTrash.
"""
from datetime import datetime

import pytest
from PySide6.QtCore import Qt

import media_model
from media_model import (
    MediaFile, COL_SIZE, format_file_size,
)
from metadata_reader import DATE_SOURCE_FILENAME, DATE_SOURCE_NONE

MB = 1024 * 1024


def _file(filename, *, hard=False, date=None, moved=False, size=None, path=None):
    return MediaFile(
        filepath=path or f'/no/such/dir/{filename}', filename=filename,
        ext='.jpg', is_video=False, is_already_formatted=hard,
        date=date,
        date_source=DATE_SOURCE_FILENAME if date else DATE_SOURCE_NONE,
        stripped_filename=filename, user_moved=moved, size_bytes=size)


def _load(model, files):
    model._files = files
    model.recalculate_proposed_filenames()


class TestFormatFileSize:
    def test_none_is_blank(self):
        assert format_file_size(None) == ''

    def test_one_decimal_place_in_mb(self):
        assert format_file_size(int(1.5 * MB)) == '1.5 MB'
        assert format_file_size(MB) == '1.0 MB'
        assert format_file_size(int(245.66 * MB)) == '245.7 MB'

    def test_uses_binary_megabytes_like_explorer(self):
        assert format_file_size(1_000_000) == '1.0 MB'   # 0.95 MiB rounds up
        assert format_file_size(10 * MB) == '10.0 MB'

    def test_tiny_file_is_not_shown_as_zero(self):
        assert format_file_size(1) == '<0.1 MB'
        assert format_file_size(12_000) == '<0.1 MB'

    def test_smallest_size_that_rounds_to_a_tenth(self):
        assert format_file_size(int(0.05 * MB) + 1) == '0.1 MB'

    def test_genuinely_empty_file_is_zero(self):
        assert format_file_size(0) == '0.0 MB'


class TestSizeColumn:
    def test_display_role_uses_the_formatter(self, model):
        _load(model, [_file('a.jpg', size=3 * MB)])
        assert model.data(model.index(0, COL_SIZE), Qt.DisplayRole) == '3.0 MB'

    def test_blank_until_the_size_has_been_read(self, model):
        _load(model, [_file('a.jpg')])
        assert model.data(model.index(0, COL_SIZE), Qt.DisplayRole) == ''

    def test_header_is_right_aligned_with_its_numbers(self, model):
        align = model.headerData(COL_SIZE, Qt.Horizontal, Qt.TextAlignmentRole)
        assert align & int(Qt.AlignRight)


class TestDeleteFile:
    def test_missing_file_just_loses_its_row(self, model):
        _load(model, [_file('a.jpg'), _file('b.jpg'), _file('c.jpg')])
        assert model.delete_file(1) is True
        assert [f.filename for f in model.files()] == ['a.jpg', 'c.jpg']

    def test_rows_removed_is_emitted_once(self, model):
        _load(model, [_file('a.jpg'), _file('b.jpg')])
        seen = []
        model.rowsRemoved.connect(lambda parent, first, last: seen.append((first, last)))
        model.delete_file(0)
        assert seen == [(0, 0)]

    def test_bad_row_is_refused(self, model):
        _load(model, [_file('a.jpg')])
        assert model.delete_file(5) is False
        assert model.delete_file(-1) is False
        assert len(model.files()) == 1

    def test_refused_while_metadata_is_still_loading(self, model):
        _load(model, [_file('a.jpg'), _file('b.jpg')])
        model._pending_metadata_shards = 2
        assert model.delete_file(0) is False
        assert len(model.files()) == 2

    def test_existing_file_is_trashed_then_its_row_removed(self, model, tmp_path, monkeypatch):
        real = tmp_path / 'a.jpg'
        real.write_bytes(b'x')
        trashed = []
        monkeypatch.setattr(media_model.QFile, 'moveToTrash',
                            staticmethod(lambda p: trashed.append(p) or True))
        _load(model, [_file('a.jpg', path=str(real)), _file('b.jpg')])
        assert model.delete_file(0) is True
        assert trashed == [str(real)]
        assert [f.filename for f in model.files()] == ['b.jpg']

    def test_a_file_that_cant_be_trashed_keeps_its_row(self, model, tmp_path, monkeypatch):
        real = tmp_path / 'a.jpg'
        real.write_bytes(b'x')
        monkeypatch.setattr(media_model.QFile, 'moveToTrash',
                            staticmethod(lambda p: False))
        _load(model, [_file('a.jpg', path=str(real)), _file('b.jpg')])
        assert model.delete_file(0) is False
        assert [f.filename for f in model.files()] == ['a.jpg', 'b.jpg']

    def test_deleting_an_anchor_re_evaluates_its_neighbours(self, model):
        # a 10:00 | w (moved, weak) | b 10:04 | c 10:10 -> w sits halfway
        # between a and b, at 10:02. Delete b and w's next anchor is c, so
        # it moves to halfway between 10:00 and 10:10.
        a = _file('20241215_100000_a.jpg', hard=True, date=datetime(2024, 12, 15, 10, 0, 0))
        w = _file('w.jpg', moved=True)
        b = _file('20241215_100400_b.jpg', hard=True, date=datetime(2024, 12, 15, 10, 4, 0))
        c = _file('20241215_101000_c.jpg', hard=True, date=datetime(2024, 12, 15, 10, 10, 0))
        _load(model, [a, w, b, c])
        assert '100200' in w.proposed_filename
        model.delete_file(2)
        assert '100500' in w.proposed_filename
