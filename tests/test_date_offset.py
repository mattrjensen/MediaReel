"""
Tests for MediaTableModel.apply_date_offset() — shifting a whole selection
of files by the same delta, for a camera whose clock was wrong. Uses
synthetic MediaFile lists, no real files or exiftool calls.
"""
from datetime import datetime, timedelta

from media_model import MediaFile
from metadata_reader import DATE_SOURCE_FILENAME, DATE_SOURCE_METADATA, DATE_SOURCE_NONE


def strong(filename, date, *, source=DATE_SOURCE_METADATA):
    return MediaFile(
        filepath=filename, filename=filename, ext='.jpg', is_video=False,
        is_already_formatted=False, date=date, date_source=source,
        stripped_filename=filename)


def hard(filename, date):
    return MediaFile(
        filepath=filename, filename=filename, ext='.jpg', is_video=False,
        is_already_formatted=True, date=date, date_source=DATE_SOURCE_FILENAME,
        stripped_filename=filename)


def weak(filename):
    return MediaFile(
        filepath=filename, filename=filename, ext='.jpg', is_video=False,
        is_already_formatted=False, date=None, date_source=DATE_SOURCE_NONE,
        stripped_filename=filename)


def load(model, files):
    model._files = files
    model.recalculate_proposed_filenames()


class TestBasicShift:
    def test_shifts_every_affected_files_date(self, model):
        a = strong('a.jpg', datetime(2024, 1, 1, 10, 0, 0))
        b = strong('b.jpg', datetime(2024, 1, 1, 11, 0, 0))
        load(model, [a, b])
        affected, skipped = model.apply_date_offset([a, b], timedelta(hours=2, minutes=15))
        assert affected == 2 and skipped == 0
        assert a.date == datetime(2024, 1, 1, 12, 15, 0)
        assert b.date == datetime(2024, 1, 1, 13, 15, 0)

    def test_a_negative_offset_subtracts(self, model):
        a = strong('a.jpg', datetime(2024, 1, 1, 10, 0, 0))
        load(model, [a])
        model.apply_date_offset([a], timedelta(hours=-3))
        assert a.date == datetime(2024, 1, 1, 7, 0, 0)

    def test_only_the_selected_files_are_touched(self, model):
        a = strong('a.jpg', datetime(2024, 1, 1, 10, 0, 0))
        b = strong('b.jpg', datetime(2024, 1, 1, 11, 0, 0))
        load(model, [a, b])
        model.apply_date_offset([a], timedelta(hours=1))
        assert a.date == datetime(2024, 1, 1, 11, 0, 0)
        assert b.date == datetime(2024, 1, 1, 11, 0, 0)   # untouched

    def test_treated_as_manual_same_as_the_calendar_picker(self, model):
        a = strong('a.jpg', datetime(2024, 1, 1, 10, 0, 0), source=DATE_SOURCE_METADATA)
        load(model, [a])
        model.apply_date_offset([a], timedelta(hours=1))
        assert a.date_source == 'manual'
        assert a.user_moved is False
        assert a.manual_filename is None


class TestSkippedFiles:
    def test_files_with_no_date_are_skipped_not_errors(self, model):
        a = strong('a.jpg', datetime(2024, 1, 1, 10, 0, 0))
        w = weak('w.jpg')
        load(model, [a, w])
        affected, skipped = model.apply_date_offset([a, w], timedelta(hours=1))
        assert affected == 1 and skipped == 1
        assert w.date is None   # untouched, still no date

    def test_all_selected_files_dateless_is_a_no_op(self, model):
        w1, w2 = weak('w1.jpg'), weak('w2.jpg')
        load(model, [w1, w2])
        affected, skipped = model.apply_date_offset([w1, w2], timedelta(hours=1))
        assert (affected, skipped) == (0, 2)


class TestHardAnchorDemotion:
    def test_a_hard_anchor_in_the_batch_becomes_correctable(self, model):
        # The core use case: files already renamed from the wrong clock.
        h = hard('20240101_100000_a.jpg', datetime(2024, 1, 1, 10, 0, 0))
        load(model, [h])
        model.apply_date_offset([h], timedelta(hours=2))
        assert h.is_already_formatted is False
        assert h.date == datetime(2024, 1, 1, 12, 0, 0)
        # Re-enters Pass 1 as a strong-anchor-not-moved file -> proposes a
        # new name from the corrected date.
        assert '20240101_120000' in h.proposed_filename


class TestReordering:
    def test_shifted_files_reposition_among_untouched_anchors(self, model):
        anchor_early = hard('20240101_060000_x.jpg', datetime(2024, 1, 1, 6, 0, 0))
        # a/b's recorded times (02:00/02:30) sit before anchor_early.
        a = strong('a.jpg', datetime(2024, 1, 1, 2, 0, 0))
        b = strong('b.jpg', datetime(2024, 1, 1, 2, 30, 0))
        anchor_late = hard('20240101_230000_y.jpg', datetime(2024, 1, 1, 23, 0, 0))
        load(model, [a, b, anchor_early, anchor_late])
        # Camera was 8 hours slow -> corrected, a/b land after anchor_early
        # (06:00) instead of before it.
        model.apply_date_offset([a, b], timedelta(hours=8))
        assert [f.filename for f in model.files()] == [
            anchor_early.filename, a.filename, b.filename, anchor_late.filename,
        ]

    def test_relative_order_within_the_batch_is_preserved(self, model):
        a = strong('a.jpg', datetime(2024, 1, 1, 10, 0, 0))
        b = strong('b.jpg', datetime(2024, 1, 1, 9, 0, 0))   # b is earlier than a
        load(model, [b, a])   # list order doesn't match date order
        model.apply_date_offset([a, b], timedelta(hours=5))
        names = [f.filename for f in model.files()]
        assert names.index('b.jpg') < names.index('a.jpg')


class TestUndo:
    def test_each_files_x_undoes_it_individually(self, model):
        a = strong('a.jpg', datetime(2024, 1, 1, 10, 0, 0), source=DATE_SOURCE_METADATA)
        b = strong('b.jpg', datetime(2024, 1, 1, 11, 0, 0), source=DATE_SOURCE_METADATA)
        load(model, [a, b])
        model.apply_date_offset([a, b], timedelta(hours=1))
        row_a = next(i for i, f in enumerate(model.files()) if f is a)
        model.undo_manual_date(row_a)
        assert a.date == datetime(2024, 1, 1, 10, 0, 0)
        assert a.date_source == DATE_SOURCE_METADATA
        # b is untouched by undoing a
        assert b.date == datetime(2024, 1, 1, 12, 0, 0)
        assert b.date_source == 'manual'

    def test_manual_date_undo_snapshots_the_pre_offset_state(self, model):
        a = strong('a.jpg', datetime(2024, 1, 1, 10, 0, 0), source=DATE_SOURCE_METADATA)
        load(model, [a])
        model.apply_date_offset([a], timedelta(hours=1))
        assert a.manual_date_undo == (datetime(2024, 1, 1, 10, 0, 0), DATE_SOURCE_METADATA, False)


class TestRecalculateCalledOnce:
    def test_recalculate_runs_once_for_the_whole_batch_not_once_per_file(self, model):
        a = strong('a.jpg', datetime(2024, 1, 1, 10, 0, 0))
        b = strong('b.jpg', datetime(2024, 1, 1, 11, 0, 0))
        c = strong('c.jpg', datetime(2024, 1, 1, 12, 0, 0))
        load(model, [a, b, c])
        calls = []
        orig = model.recalculate_proposed_filenames
        model.recalculate_proposed_filenames = lambda: (calls.append(1), orig())[1]
        model.apply_date_offset([a, b, c], timedelta(hours=1))
        assert calls == [1]
