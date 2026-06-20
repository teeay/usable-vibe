"""Phase 4 native-scroll routing: app-generated status / control surfaces.

Covers the compact startup header, the fatal-init exit hint, teleport
status/result finalization, the native plan-review live owner + Ctrl+G, and the
unsupported-durable-widget guard. Integration tests assert committed terminal
scrollback and the live region, and that durable content does not land in the
hidden ``#messages`` tree.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from textual.widgets import Static

from tests.conftest import build_test_vibe_app
import vibe.cli.textual_ui.app as app_module
from vibe.cli.textual_ui.native_scroll.app_surfaces import (
    render_plan_notice,
    render_startup_header,
    render_teleport_outcome,
)
from vibe.cli.textual_ui.scrollback_committer import ScrollbackCommitter
from vibe.cli.textual_ui.widgets.messages import PlanFileMessage, WarningMessage
from vibe.cli.textual_ui.widgets.teleport_message import TeleportMessage
from vibe.core.types import PlanReviewEndedEvent, PlanReviewRequestedEvent


def _committer() -> ScrollbackCommitter:
    return ScrollbackCommitter(width_getter=lambda: 80, color_system=None)


def _lines(committer: ScrollbackCommitter) -> str:
    return "\n".join(committer.drain_lines())


# -- pure renderers --------------------------------------------------------


def test_render_startup_header_has_version_model_cwd() -> None:
    block = render_startup_header(version="1.2.3", model="big[high]", cwd="/work/proj")
    committer = _committer()
    committer._enqueue(block)
    text = _lines(committer)
    assert "Usable Vibe" in text
    assert "v1.2.3" in text
    assert "big[high]" in text
    assert "/work/proj" in text
    assert "/help" in text


def test_render_teleport_outcome_success_and_error() -> None:
    committer = _committer()
    committer._enqueue(render_teleport_outcome(url="https://x.dev/s", error=None))
    success = _lines(committer)
    assert "Teleported to Vibe Code Web: https://x.dev/s" in success

    committer._enqueue(render_teleport_outcome(url=None, error="no remote"))
    failure = _lines(committer)
    assert "Teleport failed: no remote" in failure


def test_render_plan_notice_has_path() -> None:
    committer = _committer()
    committer._enqueue(render_plan_notice(Path("/tmp/plan.md")))
    text = _lines(committer)
    assert "Plan ready for review" in text
    assert "/tmp/plan.md" in text


# -- committer commit methods ----------------------------------------------


def test_commit_startup_header_enqueues_once() -> None:
    committer = _committer()
    committer.commit_startup_header(version="9.9.9", model="m[low]", cwd="/c")
    assert committer.has_pending is True
    text = _lines(committer)
    assert text.count("Usable Vibe") == 1
    assert "9.9.9" in text


def test_commit_teleport_success_and_error() -> None:
    committer = _committer()
    committer.commit_teleport(url="https://t", error=None)
    assert "Teleported to Vibe Code Web: https://t" in _lines(committer)
    committer.commit_teleport(url=None, error="boom")
    assert "Teleport failed: boom" in _lines(committer)


def test_plan_requested_event_commits_notice_ended_is_silent() -> None:
    committer = _committer()
    committer.handle_event(PlanReviewRequestedEvent(file_path=Path("/tmp/p.md")))
    assert "Plan ready for review" in _lines(committer)

    committer.handle_event(PlanReviewEndedEvent())
    assert committer.has_pending is False


# -- integration: real VibeApp ---------------------------------------------


@pytest.mark.asyncio
async def test_startup_header_committed_once() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        # The header is the durable session context committed at startup.
        assert app._committer.has_pending is True
        text = "\n".join(app._committer.drain_lines())
        assert text.count("Usable Vibe") == 1
        assert "/help" in text
        # Not mounted into the hidden transcript.
        assert len(list(app._messages_area.children)) == 0


@pytest.mark.asyncio
async def test_fatal_init_hint_commits_durably() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()  # clear startup header baseline

        before = list(app._messages_area.children)
        await app._mount_and_scroll(
            WarningMessage("Press any key to exit...", show_border=False)
        )
        await pilot.pause()

        assert list(app._messages_area.children) == before
        assert "Press any key to exit..." in "\n".join(app._committer.drain_lines())


@pytest.mark.asyncio
async def test_teleport_outcome_commits_and_removes_live_surface() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()

        message = TeleportMessage()
        await app._live_surface.mount(message)
        await pilot.pause()
        assert message in app._live_surface.children

        await app._finalize_teleport(message, url="https://web/s", error=None)
        await pilot.pause()

        # Live spinner gone; durable outcome committed, not in hidden transcript.
        assert message not in app._live_surface.children
        assert len(list(app._messages_area.children)) == 0
        assert "Teleported to Vibe Code Web: https://web/s" in "\n".join(
            app._committer.drain_lines()
        )


@pytest.mark.asyncio
async def test_teleport_error_commits_failure_line() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()

        message = TeleportMessage()
        await app._live_surface.mount(message)
        await app._finalize_teleport(message, url=None, error="not available")
        await pilot.pause()

        assert message not in app._live_surface.children
        assert "Teleport failed: not available" in "\n".join(
            app._committer.drain_lines()
        )


@pytest.mark.asyncio
async def test_plan_review_native_owner_and_ctrl_g(tmp_path: Path) -> None:
    app = build_test_vibe_app()
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("# Plan\n")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()

        await app._apply_native_plan_effects(
            PlanReviewRequestedEvent(file_path=plan_path)
        )
        await pilot.pause()

        # Live owner mounted in #live-surface, tracked for Ctrl+G, not hidden.
        message = app._native_plan_message
        assert isinstance(message, PlanFileMessage)
        assert message in app._live_surface.children
        assert message not in app._messages_area.children

        # Ctrl+G opens the natively-tracked plan, not EventHandler state.
        message.open_in_editor = MagicMock()  # type: ignore[method-assign]
        app.action_open_plan_in_editor()
        message.open_in_editor.assert_called_once()

        # Review ends: live owner torn down.
        await app._apply_native_plan_effects(PlanReviewEndedEvent())
        await pilot.pause()
        assert app._native_plan_message is None
        assert message not in app._live_surface.children


@pytest.mark.asyncio
async def test_unsupported_durable_widget_warns_and_falls_through() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()

        warn = MagicMock()
        original = app_module.logger.warning
        app_module.logger.warning = warn  # type: ignore[method-assign]
        try:
            orphan = Static("unhandled durable surface")
            await app._mount_and_scroll(orphan)
            await pilot.pause()
        finally:
            app_module.logger.warning = original  # type: ignore[method-assign]

        # Fall-through is visible: warned, nothing durable committed, widget hidden.
        warn.assert_called_once()
        assert app._committer.has_pending is False
        assert orphan in app._messages_area.children
