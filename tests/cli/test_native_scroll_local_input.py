"""Native-scroll coverage for the local input / command lifecycles (Phase 3).

These tests assert the shipped native path: queued prompts and the queue
header are live in ``#live-queue`` (never the hidden ``#messages`` tree),
queued prompts commit exactly once when promoted, running manual bash shows
only the loading widget live, finished bash commits one durable block, and
``/clear`` / ``/compact`` route their durable outcome through the committer
while their live widgets never become hidden transcript.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_app, wait_until
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server.models import (
    PublicEntryGenerationStatus,
    PublicMessageEntry,
    TextContentBlock,
)
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.cli.textual_ui.widgets.messages import (
    BashOutputMessage,
    QueueHeaderMessage,
    UserMessage,
)


class _BlockingBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__([[mock_llm_chunk(content="done")]] * 4)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
        return await super().complete(**kwargs)


def _blocked_app() -> tuple[VibeApp, _BlockingBackend]:
    backend = _BlockingBackend()
    agent_loop = build_test_agent_loop(backend=backend)
    return build_test_vibe_app(agent_loop=agent_loop), backend


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
    app, backend = _blocked_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        chat_input = app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("block queue"))
        assert await wait_until(pilot, backend.started.is_set)
        await app._queue.enqueue_prompt("queued one")
        assert await wait_until(pilot, lambda: len(app._queue) == 1)
        await pilot.pause()
        app._committer.drain_lines()  # drop the startup header + block prompt

        live = list(app._live_queue.children)
        assert any(isinstance(c, QueueHeaderMessage) for c in live)
        assert any(isinstance(c, UserMessage) and c.pending for c in live)
        # Not in the hidden transcript, and not committed while pending.
        assert list(app._messages_area.children) == []
        assert app._committer is not None
        assert app._committer.has_pending is False
        backend.release.set()


@pytest.mark.asyncio
async def test_queue_header_tracks_pause_resume() -> None:
    app, backend = _blocked_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat_input = app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("block queue"))
        assert await wait_until(pilot, backend.started.is_set)
        await app._queue.enqueue_prompt("one")
        assert await wait_until(pilot, lambda: app._queue.header is not None)

        header = app._queue.header
        assert header is not None
        assert header in app._live_queue.children
        assert header._paused is False

        await pilot.press("escape")
        assert await wait_until(pilot, lambda: app.app_server.turn_queue.paused)
        assert await wait_until(pilot, lambda: header._paused is True)

        backend.release.set()
        await app.app_server.resume_turn_queue()
        assert await wait_until(
            pilot,
            lambda: app._queue.header is None and not app._agent_job_active(),
            timeout=5.0,
        )


@pytest.mark.asyncio
async def test_queue_pop_removes_live_widget_without_committing() -> None:
    app, backend = _blocked_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        chat_input = app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("block queue"))
        assert await wait_until(pilot, backend.started.is_set)
        await app._queue.enqueue_prompt("first")
        await app._queue.enqueue_prompt("second")
        assert await wait_until(pilot, lambda: len(app._queue) == 2)
        await pilot.pause()
        app._committer.drain_lines()  # drop the startup header + block prompt

        pending = [c for c in app._live_queue.children if isinstance(c, UserMessage)]
        assert len(pending) == 2

        assert await app._queue.pop_last() is True
        await pilot.pause()

        pending = [c for c in app._live_queue.children if isinstance(c, UserMessage)]
        assert len(pending) == 1
        assert app._committer is not None
        assert app._committer.has_pending is False
        assert list(app._messages_area.children) == []
        backend.release.set()


@pytest.mark.asyncio
async def test_running_bash_shows_loading_and_commits_on_finish() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()  # drop the startup-header baseline
        task = asyncio.create_task(app._handle_bash_command("sleep 0.5"))
        assert await wait_until(pilot, lambda: app._loading_widget is not None)

        assert list(app._messages_area.children) == []
        assert app._committer is not None
        assert app._committer.has_pending is False

        await asyncio.wait_for(task, timeout=5.0)
        await pilot.pause()

        assert app._loading_widget is None
        assert app._committer is not None
        text = "\n".join(app._committer.drain_lines())
        assert "$ sleep 0.5" in text


@pytest.mark.asyncio
async def test_activated_queue_prompt_commits_once_to_scrollback() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        # The activation path used by the server-queue engine when a queued
        # prompt promotes: commits via the same native prompt path, exactly once.
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
