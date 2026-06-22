"""Native-scroll coverage for resumed transcript sources.

Resumed session history routes through the same ``ScrollbackCommitter`` as local
turns: the recent history tail commits to native scrollback with a marker for
earlier messages and never mounts durable content into the hidden ``#messages``
tree. The interactive load-more affordance is not used in native mode.
"""

from __future__ import annotations

import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.native_scroll.committer import ScrollbackCommitter
from vibe.cli.textual_ui.native_scroll.history_render import render_history_blocks
from vibe.cli.textual_ui.widgets.load_more import HistoryLoadMoreRequested
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall


def _committer() -> ScrollbackCommitter:
    return ScrollbackCommitter(width_getter=lambda: 80, color_system=None)


def _lines(committer: ScrollbackCommitter) -> str:
    return "\n".join(committer.drain_lines())


# -- pure history renderer -------------------------------------------------


def test_render_history_blocks_covers_each_role() -> None:
    messages = [
        LLMMessage(role=Role.user, content="ping"),
        LLMMessage(
            role=Role.assistant,
            content="pong",
            tool_calls=[
                ToolCall(
                    id="c1", index=0, function=FunctionCall(name="read", arguments="{}")
                )
            ],
        ),
        LLMMessage(role=Role.tool, content="file body", name="read", tool_call_id="c1"),
    ]
    committer = _committer()
    for block in render_history_blocks(messages, {}, omitted_count=0):
        committer._enqueue(block)
    text = _lines(committer)
    assert "ping" in text
    assert "pong" in text
    assert "read" in text
    assert "file body" in text


def test_render_history_blocks_omitted_marker() -> None:
    committer = _committer()
    for block in render_history_blocks(
        [LLMMessage(role=Role.user, content="tail")], {}, omitted_count=7
    ):
        committer._enqueue(block)
    text = _lines(committer)
    assert "7 earlier messages omitted" in text
    assert "tail" in text


def test_render_history_blocks_skips_injected() -> None:
    messages = [
        LLMMessage(role=Role.user, content="visible"),
        LLMMessage(role=Role.user, content="injected one", injected=True),
    ]
    committer = _committer()
    for block in render_history_blocks(messages, {}, omitted_count=0):
        committer._enqueue(block)
    text = _lines(committer)
    assert "visible" in text
    assert "injected one" not in text


# -- committer commit methods ----------------------------------------------


def test_commit_history_enqueues_tail_and_marker() -> None:
    committer = _committer()
    committer.commit_history(
        [LLMMessage(role=Role.assistant, content="answer")], {}, omitted_count=3
    )
    text = _lines(committer)
    assert "3 earlier messages omitted" in text
    assert "answer" in text


# -- resume integration ----------------------------------------------------


@pytest.mark.asyncio
async def test_resume_commits_tail_not_hidden_messages() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()  # drop the startup-header baseline

        app.agent_loop.messages.extend([
            LLMMessage(role=Role.user, content="resumed prompt"),
            LLMMessage(role=Role.assistant, content="resumed answer"),
        ])
        await app._resume_history_from_messages()
        await pilot.pause()

        text = "\n".join(app._committer.drain_lines())
        assert "resumed prompt" in text
        assert "resumed answer" in text
        assert list(app._messages_area.children) == []


@pytest.mark.asyncio
async def test_resume_commits_omitted_marker_beyond_tail() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()

        # More than HISTORY_RESUME_TAIL_MESSAGES so a backfill remains.
        for i in range(25):
            app.agent_loop.messages.append(
                LLMMessage(role=Role.user, content=f"msg {i}")
            )
        await app._resume_history_from_messages()
        await pilot.pause()

        text = "\n".join(app._committer.drain_lines())
        assert "earlier messages omitted" in text
        assert "msg 24" in text  # tail is committed
        assert list(app._messages_area.children) == []


@pytest.mark.asyncio
async def test_load_more_is_noop_in_native_mode() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()

        await app.on_history_load_more_requested(HistoryLoadMoreRequested())
        await pilot.pause()

        assert app._committer.has_pending is False
        assert app._load_more.widget is None
