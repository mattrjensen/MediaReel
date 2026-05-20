"""
Pure-logic tests for metadata_reader.py.
No files, no exiftool, no Qt required.
"""
from datetime import datetime
import pytest

from metadata_reader import (
    is_already_formatted,
    parse_date_from_filename,
    strip_date_from_filename,
    build_new_filename,
)


# ── is_already_formatted ──────────────────────────────────────────────────────

class TestIsAlreadyFormatted:
    def test_formatted_with_suffix(self):
        assert is_already_formatted('20241215_183042_IMG_4821.heic')

    def test_formatted_bare(self):
        assert is_already_formatted('20241215_183042.jpg')

    def test_not_formatted_plain(self):
        assert not is_already_formatted('IMG_4821.heic')

    def test_not_formatted_date_in_middle(self):
        assert not is_already_formatted('IMG_20241215_183042.jpg')

    def test_not_formatted_signal(self):
        assert not is_already_formatted('signal-2024-12-15-190122.mov')

    def test_partial_prefix_eight_digits_only(self):
        assert not is_already_formatted('20241215_IMG.jpg')


# ── parse_date_from_filename ──────────────────────────────────────────────────

class TestParseDateFromFilename:
    def test_pattern_yyyymmdd_underscore_hhmmss(self):
        dt = parse_date_from_filename('IMG_20241215_183042.jpg')
        assert dt == datetime(2024, 12, 15, 18, 30, 42)

    def test_pattern_yyyymmdd_dash_hhmmss(self):
        dt = parse_date_from_filename('photo_20241215-183042.jpg')
        assert dt == datetime(2024, 12, 15, 18, 30, 42)

    def test_pattern_yyyy_dash_mm_dash_dd_dash_hhmmss(self):
        dt = parse_date_from_filename('signal-2024-12-15-190122.mov')
        assert dt == datetime(2024, 12, 15, 19, 1, 22)

    def test_pattern_yyyy_dash_mm_dash_dd_underscore_hh_dash_mm_dash_ss(self):
        dt = parse_date_from_filename('photo-2024-12-15_18-30-42.jpg')
        assert dt == datetime(2024, 12, 15, 18, 30, 42)

    def test_no_date_returns_none(self):
        assert parse_date_from_filename('IMG_4821.heic') is None

    def test_invalid_month_returns_none(self):
        assert parse_date_from_filename('IMG_20241332_183042.jpg') is None

    def test_invalid_hour_returns_none(self):
        assert parse_date_from_filename('IMG_20241215_253042.jpg') is None

    def test_already_formatted_prefix_is_parsed(self):
        dt = parse_date_from_filename('20241215_183042_IMG_4821.heic')
        assert dt == datetime(2024, 12, 15, 18, 30, 42)


# ── strip_date_from_filename ──────────────────────────────────────────────────

class TestStripDateFromFilename:
    def test_strips_yyyymmdd_hhmmss(self):
        assert strip_date_from_filename('IMG_20241215_183042.jpg') == 'IMG.jpg'

    def test_strips_signal_pattern(self):
        assert strip_date_from_filename('signal-2024-12-15-190122.mov') == 'signal.mov'

    def test_no_date_unchanged(self):
        assert strip_date_from_filename('IMG_4821.heic') == 'IMG_4821.heic'

    def test_date_only_stem_falls_back_to_original(self):
        # Stem is just the date — stripping leaves nothing, so original is kept
        result = strip_date_from_filename('20241215_183042.jpg')
        assert result == '20241215_183042.jpg'

    def test_strips_leading_underscore_after_removal(self):
        # IMG_ after stripping the date → IMG (leading underscore trimmed)
        result = strip_date_from_filename('IMG_20241215_183042.heic')
        assert result == 'IMG.heic'


# ── build_new_filename ────────────────────────────────────────────────────────

class TestBuildNewFilename:
    def test_basic_prepend(self):
        dt = datetime(2024, 12, 15, 18, 30, 42)
        assert build_new_filename('IMG_4821.heic', dt) == '20241215_183042_IMG_4821.heic'

    def test_strips_embedded_date(self):
        dt = datetime(2024, 12, 15, 19, 1, 22)
        assert build_new_filename('signal-2024-12-15-190122.mov', dt) == '20241215_190122_signal.mov'

    def test_already_formatted_not_changed(self):
        dt = datetime(2024, 12, 15, 20, 0, 0)
        original = '20241215_183042_IMG_4821.heic'
        assert build_new_filename(original, dt) == original

    def test_already_formatted_with_force_overrides(self):
        dt = datetime(2024, 12, 15, 20, 0, 0)
        result = build_new_filename('20241215_183042_IMG_4821.heic', dt, force=True)
        assert result == '20241215_200000_IMG_4821.heic'

    def test_is_interpolated_flag_does_not_affect_filename(self):
        dt = datetime(2024, 12, 15, 18, 30, 42)
        plain  = build_new_filename('IMG_4821.heic', dt, is_interpolated=False)
        interp = build_new_filename('IMG_4821.heic', dt, is_interpolated=True)
        assert plain == interp

    def test_no_double_date_prefix(self):
        dt = datetime(2024, 12, 15, 18, 30, 42)
        result = build_new_filename('IMG_20241215_183042.jpg', dt)
        assert result.count('20241215') == 1
