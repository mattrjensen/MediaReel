"""
Tests for metadata_panel.py: MetadataPanel's state machine and its guard
against a result arriving for a file that's no longer the one requested.
The real exiftool read is exercised end-to-end via real-folder QTest scripts
(not part of this suite, same as preview.py's video playback) — these drive
_on_loaded/_on_failed directly, the way a real (slow, off-thread) read
would eventually call them, without needing a real file or exiftool.
"""
from metadata_panel import MetadataDialog, MetadataPanel


class TestShowFile:
    def test_shows_loading_state_immediately(self, qapp):
        panel = MetadataPanel()
        panel.show()   # isVisible() only reflects reality once a top-level ancestor is shown
        panel.show_file('/no/such/a.jpg', 'a.jpg')
        assert panel._title.text() == 'a.jpg'
        assert panel._status.isVisible()
        assert panel._status.text() == 'Loading metadata…'
        assert not panel._table.isVisible()

    def test_a_second_call_resets_the_table(self, qapp):
        panel = MetadataPanel()
        panel.show()   # isVisible() only reflects reality once a top-level ancestor is shown
        panel.show_file('/no/such/a.jpg', 'a.jpg')
        panel._on_loaded('/no/such/a.jpg', {'EXIF:Make': 'Canon'})
        assert panel._table.rowCount() == 1
        panel.show_file('/no/such/b.jpg', 'b.jpg')
        assert panel._table.rowCount() == 0
        assert not panel._table.isVisible()
        assert panel._status.text() == 'Loading metadata…'


class TestLoaded:
    def test_populates_tag_value_rows(self, qapp):
        panel = MetadataPanel()
        panel.show()   # isVisible() only reflects reality once a top-level ancestor is shown
        panel.show_file('/no/such/a.jpg', 'a.jpg')
        panel._on_loaded('/no/such/a.jpg', {'EXIF:Make': 'Canon', 'File:FileSize': '2 MB'})
        assert panel._table.isVisible()
        assert not panel._status.isVisible()
        assert panel._table.rowCount() == 2
        assert panel._table.item(0, 0).text() == 'EXIF:Make'
        assert panel._table.item(0, 1).text() == 'Canon'

    def test_empty_tags_shows_a_message_not_an_empty_table(self, qapp):
        panel = MetadataPanel()
        panel.show()   # isVisible() only reflects reality once a top-level ancestor is shown
        panel.show_file('/no/such/a.jpg', 'a.jpg')
        panel._on_loaded('/no/such/a.jpg', {})
        assert not panel._table.isVisible()
        assert panel._status.isVisible()
        assert 'No metadata' in panel._status.text()

    def test_a_result_for_a_file_no_longer_showing_is_dropped(self, qapp):
        # The user stepped to a different file before the first file's
        # (slower) read came back.
        panel = MetadataPanel()
        panel.show()   # isVisible() only reflects reality once a top-level ancestor is shown
        panel.show_file('/no/such/a.jpg', 'a.jpg')
        panel.show_file('/no/such/b.jpg', 'b.jpg')
        panel._on_loaded('/no/such/a.jpg', {'EXIF:Make': 'Canon'})
        assert panel._table.rowCount() == 0
        assert panel._title.text() == 'b.jpg'
        assert panel._status.text() == 'Loading metadata…'


class TestFailed:
    def test_shows_the_error_message(self, qapp):
        panel = MetadataPanel()
        panel.show()   # isVisible() only reflects reality once a top-level ancestor is shown
        panel.show_file('/no/such/a.jpg', 'a.jpg')
        panel._on_failed('/no/such/a.jpg', 'file not found')
        assert not panel._table.isVisible()
        assert panel._status.isVisible()
        assert 'file not found' in panel._status.text()

    def test_a_stale_failure_is_dropped(self, qapp):
        panel = MetadataPanel()
        panel.show()   # isVisible() only reflects reality once a top-level ancestor is shown
        panel.show_file('/no/such/a.jpg', 'a.jpg')
        panel.show_file('/no/such/b.jpg', 'b.jpg')
        panel._on_failed('/no/such/a.jpg', 'file not found')
        assert panel._title.text() == 'b.jpg'
        assert panel._status.text() == 'Loading metadata…'


class TestMetadataDialog:
    def test_sets_title_and_dispatches_a_read(self, qapp):
        dlg = MetadataDialog('/no/such/a.jpg', 'a.jpg')
        assert 'a.jpg' in dlg.windowTitle()
        assert dlg._panel._pending_filepath == '/no/such/a.jpg'
        dlg.close()
