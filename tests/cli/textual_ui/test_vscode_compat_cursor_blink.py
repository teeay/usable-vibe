from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from vibe.cli.textual_ui.widgets.vscode_compat import VscodeCompatInput


class _InputHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield VscodeCompatInput()


@pytest.mark.asyncio
async def test_focused_vscode_input_does_not_blink() -> None:
    app = _InputHarness()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        inp = app.query_one(VscodeCompatInput)
        inp.focus()
        await pilot.pause(0.1)

        # These inputs back the live question "Other" field and proxy setup
        # dialog; a focused blink timer would emit idle InlineUpdate frames that
        # repaint the form while it waits on the user.
        assert inp.cursor_blink is False
        assert inp._cursor_visible is True
        assert inp._blink_timer is None or not inp._blink_timer._active.is_set()


@pytest.mark.asyncio
async def test_vscode_input_cursor_blink_cannot_be_re_enabled() -> None:
    app = _InputHarness()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        inp = app.query_one(VscodeCompatInput)

        inp.focus()
        await pilot.pause(0.1)
        assert inp.cursor_blink is False

        inp.cursor_blink = True
        assert inp.cursor_blink is False
