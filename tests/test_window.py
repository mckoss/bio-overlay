"""Tests for the window's self-contained status page."""

from bio_overlay import __version__
from bio_overlay.window import _status_page


def test_loading_page_has_spinner_and_version():
    page = _status_page("Starting bio-overlay…", loading=True)
    assert 'class="spinner"' in page
    assert f"v{__version__}" in page


def test_error_page_escapes_message_and_keeps_line_breaks():
    page = _status_page("bio-overlay <2.0> is already running.\nQuit it first.")
    assert 'class="spinner"' not in page
    assert "bio-overlay &lt;2.0&gt; is already running.<br>Quit it first." in page
