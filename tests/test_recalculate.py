"""
Tests for MediaTableModel.recalculate_proposed_filenames() and related predicates.
Uses synthetic MediaFile lists — no real files or exiftool calls.
Requires a QApplication (provided by the qapp fixture in conftest.py).
"""
from datetime import datetime, timedelta
import pytest

from media_model import (
    MediaFile,
    MediaTableModel,
    _PLACEHOLDER_PHASE1,
    _PLACEHOLDER_PHASE2,
)
from metadata_reader import (
    DATE_SOURCE_METADATA,
    DATE_SOURCE_FILENAME,
    DATE_SOURCE_MODIFIED,
    DATE_SOURCE_NONE,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def strong(filename, date, *, user_moved=False):
    """Unmoved or moved strong-source file (metadata date)."""
    return MediaFile(
        filepath=filename, filename=filename,
        ext='.jpg', is_video=False,
        is_already_formatted=False,
        date=date, date_source=DATE_SOURCE_METADATA,
        stripped_filename=filename,
        user_moved=user_moved,
    )


def hard(filename, date):
    """Already-formatted file — the final source-of-truth state."""
    return MediaFile(
        filepath=filename, filename=filename,
        ext='.jpg', is_video=False,
        is_already_formatted=True,
        date=date, date_source=DATE_SOURCE_FILENAME,
        stripped_filename=filename,
    )


def weak(filename, *, user_moved=False):
    """No-date file, optionally already moved by the user."""
    return MediaFile(
        filepath=filename, filename=filename,
        ext='.jpg', is_video=False,
        is_already_formatted=False,
        date=None, date_source=DATE_SOURCE_NONE,
        stripped_filename=filename,
        user_moved=user_moved,
    )


def load(model: MediaTableModel, files: list):
    """Inject a synthetic file list and recalculate."""
    model._files = files
    model.recalculate_proposed_filenames()


# ── Hard anchors ──────────────────────────────────────────────────────────────

class TestHardAnchor:
    def test_already_formatted_unchanged(self, model):
        f = hard('20241215_183042_IMG_001.jpg', datetime(2024, 12, 15, 18, 30, 42))
        load(model, [f])
        assert f.proposed_filename == '20241215_183042_IMG_001.jpg'
        assert not f.needs_attention
        assert not f.is_interpolated


# ── Strong anchors ────────────────────────────────────────────────────────────

class TestStrongAnchor:
    def test_gets_proposed_name_from_own_date(self, model):
        f = strong('IMG_001.jpg', datetime(2024, 12, 15, 18, 30, 42))
        load(model, [f])
        assert f.proposed_filename == '20241215_183042_IMG_001.jpg'
        assert not f.needs_attention
        assert not f.is_interpolated

    def test_multiple_strong_files_each_use_own_date(self, model):
        a = strong('IMG_001.jpg', datetime(2024, 12, 15, 10, 0, 0))
        b = strong('IMG_002.jpg', datetime(2024, 12, 15, 11, 0, 0))
        load(model, [a, b])
        assert a.proposed_filename == '20241215_100000_IMG_001.jpg'
        assert b.proposed_filename == '20241215_110000_IMG_002.jpg'

    def test_moved_in_order_keeps_own_date(self, model):
        a = hard('20241215_090000_a.jpg', datetime(2024, 12, 15, 9, 0, 0))
        b = strong('IMG_002.jpg', datetime(2024, 12, 15, 10, 0, 0), user_moved=True)
        c = hard('20241215_110000_c.jpg', datetime(2024, 12, 15, 11, 0, 0))
        load(model, [a, b, c])
        # b's own date (10:00) is between a (09:00) and c (11:00) — still in order
        assert b.proposed_filename == '20241215_100000_IMG_002.jpg'
        assert not b.is_re_anchored

    def test_moved_out_of_order_is_reanchored(self, model):
        # b (10:00) has been dragged before a (09:00) — out of order
        b = strong('IMG_b.jpg', datetime(2024, 12, 15, 10, 0, 0), user_moved=True)
        a = hard('20241215_090000_a.jpg', datetime(2024, 12, 15, 9, 0, 0))
        load(model, [b, a])
        assert b.is_re_anchored
        # Should get a time before a's 09:00
        assert '090000' not in b.proposed_filename or b.proposed_filename < a.proposed_filename


# ── Weak anchors — Phase 1 (strong renames pending) ──────────────────────────

class TestWeakAnchorPhase1:
    def test_unmoved_gets_phase1_placeholder(self, model):
        s = strong('IMG_001.jpg', datetime(2024, 12, 15, 10, 0, 0))
        w = weak('received_001.jpg')
        load(model, [s, w])
        assert w.needs_attention
        assert w.proposed_filename == _PLACEHOLDER_PHASE1

    def test_moved_gets_interpolated_even_in_phase1(self, model):
        a = hard('20241215_100000_a.jpg', datetime(2024, 12, 15, 10, 0, 0))
        s = strong('IMG_002.jpg', datetime(2024, 12, 15, 11, 0, 0))
        w = weak('received_001.jpg', user_moved=True)
        c = hard('20241215_120000_c.jpg', datetime(2024, 12, 15, 12, 0, 0))
        load(model, [a, w, s, c])
        assert w.is_interpolated
        assert '---' not in w.proposed_filename


# ── Weak anchors — Phase 2 (no strong renames pending) ───────────────────────

class TestWeakAnchorPhase2:
    def test_unmoved_gets_phase2_placeholder(self, model):
        a = hard('20241215_100000_a.jpg', datetime(2024, 12, 15, 10, 0, 0))
        w = weak('received_001.jpg')
        load(model, [a, w])
        assert w.needs_attention
        assert w.proposed_filename == _PLACEHOLDER_PHASE2

    def test_moved_between_anchors_gets_midpoint(self, model):
        a = hard('20241215_100000_a.jpg', datetime(2024, 12, 15, 10, 0, 0))
        w = weak('received_001.jpg', user_moved=True)
        b = hard('20241215_100400_b.jpg', datetime(2024, 12, 15, 10, 4, 0))
        load(model, [a, w, b])
        assert w.is_interpolated
        assert '100200' in w.proposed_filename  # halfway = 10:02:00

    def test_two_weak_files_evenly_spaced(self, model):
        a = hard('20241215_100000_a.jpg', datetime(2024, 12, 15, 10, 0, 0))
        w1 = weak('w1.jpg', user_moved=True)
        w2 = weak('w2.jpg', user_moved=True)
        b = hard('20241215_100300_b.jpg', datetime(2024, 12, 15, 10, 3, 0))
        load(model, [a, w1, w2, b])
        assert '100100' in w1.proposed_filename  # 10:01:00
        assert '100200' in w2.proposed_filename  # 10:02:00

    def test_weak_file_before_all_anchors_extrapolates_backward(self, model):
        w = weak('w.jpg', user_moved=True)
        a = hard('20241215_100000_a.jpg', datetime(2024, 12, 15, 10, 0, 0))
        b = hard('20241215_100200_b.jpg', datetime(2024, 12, 15, 10, 2, 0))
        load(model, [w, a, b])
        assert w.is_interpolated
        # Should be before 10:00
        assert w.proposed_filename < '20241215_100000'

    def test_weak_file_after_all_anchors_extrapolates_forward(self, model):
        a = hard('20241215_100000_a.jpg', datetime(2024, 12, 15, 10, 0, 0))
        b = hard('20241215_100200_b.jpg', datetime(2024, 12, 15, 10, 2, 0))
        w = weak('w.jpg', user_moved=True)
        load(model, [a, b, w])
        assert w.is_interpolated
        # Should be after 10:02
        assert w.proposed_filename > '20241215_100200'

    def test_no_anchors_leaves_weak_file_as_attention(self, model):
        w = weak('w.jpg', user_moved=True)
        load(model, [w])
        assert w.needs_attention


# ── Collision resolution ──────────────────────────────────────────────────────

class TestCollisionResolution:
    def test_two_files_same_proposed_name_get_suffixed(self, model):
        # Both strip to 'IMG.jpg' and share the same date
        dt = datetime(2024, 12, 15, 10, 0, 0)
        # 'IMG_20241215_100000.jpg' strips date → 'IMG.jpg' → proposed '20241215_100000_IMG.jpg'
        # 'IMG.jpg' → stripped stays 'IMG.jpg'  → proposed '20241215_100000_IMG.jpg'
        a = strong('IMG_20241215_100000.jpg', dt)
        b = strong('IMG.jpg', dt)
        load(model, [a, b])
        names = {a.proposed_filename, b.proposed_filename}
        assert len(names) == 2
        assert all('20241215_100000_IMG' in n for n in names)
        assert any('_01' in n for n in names)
        assert any('_02' in n for n in names)


# ── Predicates ────────────────────────────────────────────────────────────────

class TestPredicates:
    def test_has_pending_renames_true(self, model):
        f = strong('IMG_001.jpg', datetime(2024, 12, 15, 10, 0, 0))
        load(model, [f])
        assert model.has_pending_renames()

    def test_has_pending_renames_false_all_hard(self, model):
        f = hard('20241215_100000_a.jpg', datetime(2024, 12, 15, 10, 0, 0))
        load(model, [f])
        assert not model.has_pending_renames()

    def test_has_pending_renames_ignores_placeholder(self, model):
        a = hard('20241215_100000_a.jpg', datetime(2024, 12, 15, 10, 0, 0))
        w = weak('w.jpg')
        load(model, [a, w])
        # Only the weak file's placeholder — no actual rename pending
        assert not model.has_pending_renames()

    def test_has_pending_strong_renames_true(self, model):
        f = strong('IMG_001.jpg', datetime(2024, 12, 15, 10, 0, 0))
        load(model, [f])
        assert model.has_pending_strong_renames()

    def test_has_pending_strong_renames_false_all_hard(self, model):
        f = hard('20241215_100000_a.jpg', datetime(2024, 12, 15, 10, 0, 0))
        load(model, [f])
        assert not model.has_pending_strong_renames()

    def test_has_pending_strong_renames_false_after_user_moved(self, model):
        # user_moved=True excludes from "pending strong renames"
        f = strong('IMG_001.jpg', datetime(2024, 12, 15, 10, 0, 0), user_moved=True)
        load(model, [f])
        assert not model.has_pending_strong_renames()

    def test_attention_count(self, model):
        a = hard('20241215_100000_a.jpg', datetime(2024, 12, 15, 10, 0, 0))
        w1 = weak('w1.jpg')
        w2 = weak('w2.jpg')
        load(model, [a, w1, w2])
        assert model.attention_count() == 2
