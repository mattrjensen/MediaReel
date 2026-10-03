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


class TestExtensions:
    def test_m4v_is_supported_and_treated_as_video(self):
        from metadata_reader import SUPPORTED_EXTENSIONS, VIDEO_EXTENSIONS, _new_result
        assert '.m4v' in SUPPORTED_EXTENSIONS
        assert '.m4v' in VIDEO_EXTENSIONS
        assert _new_result('/no/such/clip.M4V')['is_video'] is True

    def test_photos_are_not_videos(self):
        from metadata_reader import SUPPORTED_EXTENSIONS, VIDEO_EXTENSIONS, PHOTO_EXTENSIONS
        assert not PHOTO_EXTENSIONS & VIDEO_EXTENSIONS
        assert SUPPORTED_EXTENSIONS == PHOTO_EXTENSIONS | VIDEO_EXTENSIONS


class TestDateTagPriority:
    """_date_from_tags walks DATE_TAG_CANDIDATES in order."""

    def test_content_create_date_beats_export_create_date(self):
        # An exported .m4v: CreateDate is the export date, ContentCreateDate
        # the original capture (values from a real file).
        from metadata_reader import _date_from_tags
        tags = {
            'QuickTime:CreateDate': '2018:06:08 12:57:46',
            'QuickTime:MediaCreateDate': '2018:06:08 12:57:46',
            'QuickTime:ContentCreateDate': '2017:06:24 09:55:46+10:00',
        }
        assert _date_from_tags(tags) == datetime(2017, 6, 24, 9, 55, 46)

    def test_ios_local_time_tags_still_win(self):
        from metadata_reader import _date_from_tags
        tags = {
            'QuickTime:CreationDate': '2026:07:04 14:48:46+12:00',
            'QuickTime:ContentCreateDate': '2026:07:04 01:00:00+00:00',
            'QuickTime:CreateDate': '2026:07:04 02:48:46',
        }
        assert _date_from_tags(tags) == datetime(2026, 7, 4, 14, 48, 46)

    def test_falls_back_to_create_date_without_content_create_date(self):
        from metadata_reader import _date_from_tags
        assert _date_from_tags({'QuickTime:CreateDate': '2024:03:05 14:30:00'}) \
            == datetime(2024, 3, 5, 14, 30, 0)


class TestIsUuidFilename:
    def test_matches_a_bare_uuid(self):
        from metadata_reader import is_uuid_filename
        assert is_uuid_filename('5c4ec94a-0ccb-465f-bb89-99dde3e458a7')

    def test_case_insensitive(self):
        from metadata_reader import is_uuid_filename
        assert is_uuid_filename('5C4EC94A-0CCB-465F-BB89-99DDE3E458A7')

    def test_an_ordinary_filename_does_not_match(self):
        from metadata_reader import is_uuid_filename
        assert not is_uuid_filename('IMG_0001')
        assert not is_uuid_filename('received_img_882746')

    def test_wrong_segment_lengths_do_not_match(self):
        from metadata_reader import is_uuid_filename
        assert not is_uuid_filename('5c4ec94a-0ccb-465f-bb89-99dde3e458a')   # one short
        assert not is_uuid_filename('5c4ec94a0ccb465fbb8999dde3e458a7')      # no hyphens

    def test_new_result_sets_looks_like_uuid_from_the_stripped_stem(self):
        from metadata_reader import _new_result
        r = _new_result('/no/such/dir/5c4ec94a-0ccb-465f-bb89-99dde3e458a7.mp4')
        assert r['looks_like_uuid'] is True
        r2 = _new_result('/no/such/dir/IMG_0001.jpg')
        assert r2['looks_like_uuid'] is False

    def test_an_already_renamed_hard_anchor_with_a_uuid_stem_still_flags(self):
        # The point: the warning survives a rename within the session,
        # checked against the stripped stem (prefix removed), not the raw
        # on-disk name.
        from metadata_reader import _new_result
        r = _new_result('/no/such/dir/20241215_184705_5c4ec94a-0ccb-465f-bb89-99dde3e458a7.mp4')
        assert r['looks_like_uuid'] is True


class TestLocationFromTags:
    def test_uses_the_composite_tag_already_signed(self):
        from metadata_reader import _location_from_tags
        tags = {'Composite:GPSLatitude': -38.3425767, 'Composite:GPSLongitude': 144.3071788}
        assert _location_from_tags(tags) == (-38.3425767, 144.3071788)

    def test_no_gps_tags_is_none_none(self):
        from metadata_reader import _location_from_tags
        assert _location_from_tags({'EXIF:Make': 'Canon'}) == (None, None)

    def test_falls_back_to_unsigned_exif_tags_with_ref_applied(self):
        from metadata_reader import _location_from_tags
        tags = {
            'GPS:GPSLatitude': 38.3425767, 'GPS:GPSLatitudeRef': 'S',
            'GPS:GPSLongitude': 144.3071788, 'GPS:GPSLongitudeRef': 'E',
        }
        assert _location_from_tags(tags) == (-38.3425767, 144.3071788)

    def test_north_and_east_stay_positive(self):
        from metadata_reader import _location_from_tags
        tags = {
            'GPS:GPSLatitude': 51.5, 'GPS:GPSLatitudeRef': 'N',
            'GPS:GPSLongitude': 0.1, 'GPS:GPSLongitudeRef': 'E',
        }
        assert _location_from_tags(tags) == (51.5, 0.1)

    def test_garbage_values_do_not_raise(self):
        from metadata_reader import _location_from_tags
        assert _location_from_tags({'Composite:GPSLatitude': 'n/a',
                                     'Composite:GPSLongitude': 144.0}) == (None, None)


class TestReadAllMetadataIncludesLocation:
    def test_new_result_defaults_to_none(self):
        from metadata_reader import _new_result
        r = _new_result('/no/such/dir/a.jpg')
        assert r['latitude'] is None and r['longitude'] is None
