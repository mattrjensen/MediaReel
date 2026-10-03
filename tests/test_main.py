"""
Tests for pure helper functions in main.py. MainWindow/the table view
itself has no pytest coverage (see CLAUDE.md's Testing section) — real
clicks against a shown window, verified with real scratch files, is how
that gets exercised instead.
"""
from main import _format_coordinates


class TestFormatCoordinates:
    def test_southern_and_eastern_hemisphere(self):
        assert _format_coordinates(-38.3425767, 144.3071788) == '38.342577° S, 144.307179° E'

    def test_northern_and_western_hemisphere(self):
        assert _format_coordinates(51.5, -0.1278) == '51.500000° N, 0.127800° W'

    def test_on_the_equator_or_prime_meridian_reads_as_north_and_east(self):
        assert _format_coordinates(0.0, 0.0) == '0.000000° N, 0.000000° E'
