"""Native-scroll coverage for the local input / command lifecycles (Phase 3).

These tests assert the shipped native path: queued prompts/bash and the queue
header are live in ``#live-queue`` (never the hidden ``#messages`` tree), queued
prompts commit exactly once when activated, manual/queued bash commits one
durable block on finish, and ``/clear`` / ``/compact`` route their durable
outcome through the committer while their live widgets never become hidden
transcript.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import build_test_vibe_app
from vibe.app_server.models import (
    PublicEntryGenerationStatus,
    PublicMessageEntry,
    TextContentBlock,
)
from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.cli.textual_ui.widgets.messages import (
    BashOutputMessage,
    QueueHeaderMessage,
    UserMessage,
)


def _entry(
    app: object, entry_id: str, role: str, text: str, index: int
) -> PublicMessageEntry:
    return PublicMessageEntry(
        id=entry_id,
        session_id=app.app_server.session_id,  # type: ignore[attr-defined]
        turn_id=f"turn-{index}",
        role=role,  # type: ignore[arg-type]
        content=[TextContentBlock(text=text)],
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        created_at=index,
        updated_at=index,
    )


def _set_history(app: object, entries: list[PublicMessageEntry]) -> None:
    app.app_server.state.history = entries  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_queued_prompt_is_live_not_hidden() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()  # drop the startup-header baseline
        await app._queue.enqueue_prompt("queued one")
        await pilot.pause()

        live = list(app._live_queue.children)
        assert any(isinstance(c, QueueHeaderMessage) for c in live)
        assert any(isinstance(c, UserMessage) and c.pending for c in live)
        # Not in the hidden transcript, and not committed while pending.
        assert list(app._messages_area.children) == []
        assert app._committer is not None
        assert app._committer.has_pending is False


@pytest.mark.asyncio
async def test_queue_header_tracks_pause_resume() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._queue.enqueue_prompt("one")
        await pilot.pause()

        header = app._queue.header
        assert header is not None
        assert header in app._live_queue.children

        app._queue.set_paused(True)
        assert app._queue.queue.paused is True
        assert header._paused is True

        app._queue.set_paused(False)
        assert app._queue.queue.paused is False
        assert header._paused is False


@pytest.mark.asyncio
async def test_queue_pop_removes_live_widget_without_committing() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()  # drop the startup-header baseline
        await app._queue.enqueue_prompt("first")
        await app._queue.enqueue_prompt("second")
        await pilot.pause()

        pending = [c for c in app._live_queue.children if isinstance(c, UserMessage)]
        assert len(pending) == 2

        assert await app._queue.pop_last() is True
        await pilot.pause()

        pending = [c for c in app._live_queue.children if isinstance(c, UserMessage)]
        assert len(pending) == 1
        assert app._committer is not None
        assert app._committer.has_pending is False
        assert list(app._messages_area.children) == []


@pytest.mark.asyncio
async def test_queued_bash_widget_is_live_in_live_queue() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()  # drop the startup-header baseline
        await app._queue.enqueue_bash("echo hi", app.app_server.cwd)
        await pilot.pause()

        assert any(isinstance(c, BashOutputMessage) for c in app._live_queue.children)
        assert list(app._messages_area.children) == []
        assert app._committer is not None
        assert app._committer.has_pending is False


@pytest.mark.asyncio
async def test_activated_queue_prompt_commits_once_to_scrollback() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        # The activation path used by the drain engine when a queued prompt
        # becomes active: commits via the same native prompt path, exactly once.
        await app._commit_queue_prompt("activated prompt", None)
        await pilot.pause()

        assert list(app._messages_area.children) == []
        assert app._committer is not None
        text = "\n".join(app._committer.drain_lines())
        assert text.count("activated prompt") == 1


@pytest.mark.asyncio
async def test_manual_bash_commits_durable_block_and_drops_live_widget() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._handle_bash_command("echo hello-bash")
        await pilot.pause()

        # The live widget is gone from both the live region and hidden tree.
        assert not any(
            isinstance(c, BashOutputMessage) for c in app._live_queue.children
        )
        assert not any(
            isinstance(c, BashOutputMessage) for c in app._messages_area.children
        )
        assert app._committer is not None
        text = "\n".join(app._committer.drain_lines())
        assert "echo hello-bash" in text
        assert "hello-bash" in text


@pytest.mark.asyncio
async def test_clear_echo_commits_to_scrollback() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._clear_history()
        await pilot.pause()

        assert list(app._messages_area.children) == []
        assert app._committer is not None
        text = "\n".join(app._committer.drain_lines())
        assert "clear" in text
        assert "New conversation started" in text


@pytest.mark.asyncio
async def test_compact_status_is_live_and_outcome_is_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()  # drop the startup-header baseline
        release = asyncio.Event()

        async def fake_compact(extra_instructions: str = "") -> str:
            await release.wait()
            return ""

        monkeypatch.setattr(app.app_server, "compact", fake_compact)
        _set_history(
            app,
            [
                _entry(app, "user-1", "user", "a", 1),
                _entry(app, "assistant-1", "assistant", "b", 2),
            ],
        )

        await app._compact_history()
        await pilot.pause()

        # Live status while compacting: in #live-queue, never the hidden tree,
        # nothing durable committed yet.
        live = [c for c in app._live_queue.children if isinstance(c, CompactMessage)]
        assert len(live) == 1
        assert not any(
            isinstance(c, CompactMessage) for c in app._messages_area.children
        )
        assert app._committer is not None
        assert app._committer.has_pending is False

        release.set()
        if app._agent_task is not None:
            await app._agent_task
        await pilot.pause()

        # Live status removed; durable outcome committed to scrollback.
        assert not any(isinstance(c, CompactMessage) for c in app._live_queue.children)
        text = "\n".join(app._committer.drain_lines())
        assert "Conversation compacted" in text
        assert list(app._messages_area.children) == []
