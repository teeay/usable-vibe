from __future__ import annotations

import pytest
from rich.console import RenderableType
from textual.screen import Screen

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.widgets.banner.petit_chat import PetitChat
from vibe.cli.textual_ui.widgets.loading import paused_timer


@pytest.mark.asyncio
async def test_banner_animation_not_running_in_native_mode() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        # Allow at least one animation tick (0.16s) to elapse.
        await pilot.pause(0.3)
        petit = app.query_one(PetitChat)
        # The banner lives in the hidden #chat and is never shown in native
        # mode; its intro animation timer must not keep running, or it repaints
        # the inline live region forever and makes the visible cursor jump.
        assert petit._timer is None


@pytest.mark.asyncio
async def test_idle_session_produces_no_inline_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app()
    frames = {"n": 0}
    original_display = app._display

    def counting_display(screen: Screen, renderable: RenderableType | None) -> None:
        frames["n"] += 1
        original_display(screen, renderable)

    monkeypatch.setattr(app, "_display", counting_display)

    async with app.run_test() as pilot:
        await pilot.pause(0.5)  # let startup settle
        frames["n"] = 0
        await pilot.pause(0.6)  # idle window spanning several 0.16s ticks
        assert frames["n"] == 0


@pytest.mark.asyncio
async def test_dialog_wait_produces_no_inline_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app()
    frames = {"n": 0}
    original_display = app._display

    def counting_display(screen: Screen, renderable: RenderableType | None) -> None:
        frames["n"] += 1
        original_display(screen, renderable)

    monkeypatch.setattr(app, "_display", counting_display)

    async with app.run_test() as pilot:
        await pilot.pause(0.5)  # let startup settle

        # An active turn keeps a LoadingWidget up; its 0.1s spinner animation
        # repaints the live region (this is the allowed "agent working" redraw).
        await app._ensure_loading_widget("Generating")
        await pilot.pause(0.2)
        frames["n"] = 0
        await pilot.pause(0.4)
        assert frames["n"] > 0

        # A wait-for-input dialog (approval / question) wraps the wait in
        # paused_timer. The live region (and the active form) must not redraw
        # while the user decides.
        with paused_timer(app._loading_widget):
            await pilot.pause(0.1)  # let any in-flight frame settle
            frames["n"] = 0
            await pilot.pause(0.6)
            assert frames["n"] == 0
