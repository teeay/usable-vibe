from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from vibe.cli.textual_ui.widgets.loading import LoadingWidget, paused_timer


class _LoadingHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield LoadingWidget(status="Generating")


@pytest.mark.asyncio
async def test_paused_timer_freezes_spinner_animation() -> None:
    app = _LoadingHarness()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        loading = app.query_one(LoadingWidget)

        # The spinner animation runs while the agent works.
        assert loading._spinner_timer is not None
        assert loading._spinner_timer._active.is_set() is True

        # The approval/question wait wraps itself in paused_timer; the spinner
        # timer must stop emitting frames so the live region (and the active
        # form) does not repaint at idle.
        with paused_timer(loading):
            assert loading._spinner_timer._active.is_set() is False

        # Returning to the agent turn resumes the animation.
        assert loading._spinner_timer._active.is_set() is True


@pytest.mark.asyncio
async def test_paused_timer_still_freezes_elapsed_counter() -> None:
    app = _LoadingHarness()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        loading = app.query_one(LoadingWidget)

        with paused_timer(loading):
            assert loading._pause_start is not None
        assert loading._pause_start is None
