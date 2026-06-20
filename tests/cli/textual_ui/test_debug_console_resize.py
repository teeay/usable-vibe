"""Test that _LogView.render_line handles width mismatches during resize."""

from __future__ import annotations

from unittest.mock import PropertyMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.geometry import Size
from textual.strip import Strip

from vibe.cli.textual_ui.widgets.debug_console import _LogView


class _LogViewTestApp(App):
    def compose(self) -> ComposeResult:
        self._log_view = _LogView(
            load_page=lambda: None, has_more=lambda: False, id="test-log-view"
        )
        yield self._log_view


@pytest.mark.asyncio
async def test_render_line_no_keyerror_on_width_mismatch():
    """render_line must not raise KeyError when wrap width != cached width."""
    app = _LogViewTestApp()
    async with app.run_test(size=(80, 24)) as pilot:
        log_view = app._log_view

        long_line = "A" * 200
        log_view.write_line(long_line)
        await pilot.pause()

        assert log_view._total_visual == 3
        assert log_view._cached_width == 80

        # At width 120, wrapping produces 2 lines, but _wrap_prefix says 3.
        new_size = Size(120, 24)
        log_view._render_line_cache.clear()
        with patch.object(
            type(log_view), "size", new_callable=PropertyMock, return_value=new_size
        ):
            result = log_view.render_line(2)
            assert isinstance(result, Strip)


@pytest.mark.asyncio
async def test_render_line_under_no_color(monkeypatch: pytest.MonkeyPatch):
    """render_line strips must survive the NO_COLOR Monochrome filter.

    With NO_COLOR set, Textual installs a Monochrome/NoColor filter that reads
    ``segment.style.color``; a strip with a ``style=None`` segment crashes it.
    The env var is set fresh here so the test does not depend on whether an
    earlier App already popped it from the environment.
    """
    monkeypatch.setenv("NO_COLOR", "1")
    app = _LogViewTestApp()
    async with app.run_test(size=(80, 24)) as pilot:
        log_view = app._log_view
        assert app.no_color is True

        log_view.write_line("A" * 200)
        await pilot.pause()

        for y in range(log_view._total_visual):
            strip = log_view.render_line(y)
            assert isinstance(strip, Strip)
            assert all(segment.style is not None for segment in strip)
