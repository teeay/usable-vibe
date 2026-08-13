"""Native-scroll coverage for resumed transcript sources.

Resumed session history routes through the same ``ScrollbackCommitter`` as local
turns: the recent history tail commits to native scrollback with a marker for
earlier messages and never mounts durable content into the hidden ``#messages``
tree. The interactive load-more affordance is not used in native mode.
"""

from __future__ import annotations

import pytest

from tests.conftest import build_test_vibe_app
from vibe.app_server.models import (
    PublicEntryGenerationStatus,
    PublicMessageEntry,
    TextContentBlock,
)
from vibe.cli.textual_ui.native_scroll.committer import ScrollbackCommitter
from vibe.cli.textual_ui.native_scroll.history_render import render_history_blocks
from vibe.cli.textual_ui.widgets.load_more import HistoryLoadMoreRequested
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall


def _committer() -> ScrollbackCommitter:
    return ScrollbackCommitter(width_getter=lambda: 80, color_system=None)


def _lines(committer: ScrollbackCommitter) -> str:
    return "\n".join(committer.drain_lines())


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


# -- pure history renderer -------------------------------------------------


def test_render_history_blocks_covers_each_role() -> None:
    messages = [
        LLMMessage(role=Role.user, content="ping"),
        LLMMessage(
            role=Role.assistant,
            content="pong",
            tool_calls=[
                ToolCall(
                    id="c1",
                    index=0,
                    function=FunctionCall(name="read_file", arguments="{}"),
                )
            ],
        ),
        LLMMessage(
            role=Role.tool, content="file body", name="read_file", tool_call_id="c1"
        ),
    ]
    committer = _committer()
    for block, is_full_width in render_history_blocks(messages, {}, omitted_count=0):
        committer._enqueue(block, is_full_width=is_full_width)
    text = _lines(committer)
    assert "ping" in text
    assert "pong" in text
    assert "read_file" in text
    assert "file body" in text


def test_render_history_blocks_omitted_marker() -> None:
    committer = _committer()
    for block, is_full_width in render_history_blocks(
        [LLMMessage(role=Role.user, content="tail")], {}, omitted_count=7
    ):
        committer._enqueue(block, is_full_width=is_full_width)
    text = _lines(committer)
    assert "7 earlier messages omitted" in text
    assert "tail" in text


def test_render_history_blocks_skips_injected() -> None:
    messages = [
        LLMMessage(role=Role.user, content="visible"),
        LLMMessage(role=Role.user, content="injected one", injected=True),
    ]
    committer = _committer()
    for block, is_full_width in render_history_blocks(messages, {}, omitted_count=0):
        committer._enqueue(block, is_full_width=is_full_width)
    text = _lines(committer)
    assert "visible" in text
    assert "injected one" not in text


def test_render_history_blocks_shortens_disposable_tool_output_by_default() -> None:
    messages = [
        LLMMessage(
            role=Role.tool,
            content="\n".join(f"line {i}" for i in range(1, 11)),
            name="bash",
        )
    ]
    committer = _committer()
    for block, is_full_width in render_history_blocks(messages, {}, omitted_count=0):
        committer._enqueue(block, is_full_width=is_full_width)
    text = _lines(committer)
    assert "line 1" in text
    assert "line 3" in text
    assert "line 4" not in text
    assert "line 7" not in text
    assert "line 8" in text
    assert "line 10" in text
    assert "... 4 lines omitted ..." in text


def test_render_history_blocks_can_disable_tool_output_shortening() -> None:
    messages = [
        LLMMessage(
            role=Role.tool,
            content="\n".join(f"line {i}" for i in range(1, 11)),
            name="bash",
        )
    ]
    committer = _committer()
    for block, is_full_width in render_history_blocks(
        messages, {}, omitted_count=0, shorten_tool_output=False
    ):
        committer._enqueue(block, is_full_width=is_full_width)
    text = _lines(committer)
    assert "line 4" in text
    assert "line 7" in text
    assert "omitted" not in text


def test_render_history_blocks_keeps_work_product_tool_output_full() -> None:
    messages = [
        LLMMessage(
            role=Role.tool,
            content="\n".join(f"diff line {i}" for i in range(1, 11)),
            name="edit",
        )
    ]
    committer = _committer()
    for block, is_full_width in render_history_blocks(messages, {}, omitted_count=0):
        committer._enqueue(block, is_full_width=is_full_width)
    text = _lines(committer)
    assert "diff line 4" in text
    assert "diff line 7" in text
    assert "omitted" not in text


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

        _set_history(
            app,
            [
                _entry(app, "user-1", "user", "resumed prompt", 1),
                _entry(app, "assistant-1", "assistant", "resumed answer", 2),
            ],
        )
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
        _set_history(
            app, [_entry(app, f"user-{i}", "user", f"msg {i}", i) for i in range(25)]
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
