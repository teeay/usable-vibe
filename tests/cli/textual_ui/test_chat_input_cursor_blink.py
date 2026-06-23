from __future__ import annotations

import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.widgets.chat_input import ChatTextArea


@pytest.mark.asyncio
async def test_focused_chat_input_does_not_blink() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        # A focused input must not blink: the blink timer is what produced idle
        # InlineUpdate frames that made the visible cursor flicker.
        assert text_area.cursor_blink is False
        # The drawn caret stays steadily visible rather than toggling.
        assert text_area._cursor_visible is True


@pytest.mark.asyncio
async def test_cursor_blink_cannot_be_re_enabled() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        text_area = app.query_one(ChatTextArea)

        # set_app_focus assigns cursor_blink on every app focus change; recording
        # start/stop assigns it directly. validate_cursor_blink coerces them all.
        text_area.set_app_focus(True)
        assert text_area.cursor_blink is False

        text_area.cursor_blink = True
        assert text_area.cursor_blink is False


@pytest.mark.asyncio
async def test_default_caret_is_a_visible_block() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        # Default shape is a full block, applied via config wiring at mount.
        assert text_area.caret_shape == "block"
        assert not text_area.has_class("-caret-underscore")
        # The caret is drawn every frame (focused, blink disabled).
        assert text_area._draw_cursor is True
        # It must be a solid block (a filled background, no reverse) — not the
        # base reverse style, which under the ansi themes resolved to the default
        # background on a blank caret cell, i.e. an invisible caret.
        text_area._theme.apply_css(text_area)
        cursor_style = text_area._theme.cursor_style
        assert cursor_style is not None
        assert not cursor_style.reverse
        assert not cursor_style.underline
        assert cursor_style.bgcolor is not None


@pytest.mark.asyncio
async def test_underscore_shape_renders_an_underline() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        text_area.caret_shape = "underscore"
        await pilot.pause(0.1)

        assert text_area.has_class("-caret-underscore")
        text_area._theme.apply_css(text_area)
        cursor_style = text_area._theme.cursor_style
        assert cursor_style is not None
        assert cursor_style.underline is True
        assert not cursor_style.reverse
