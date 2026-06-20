"""Integration tests for native-scroll routing: durable widgets and agent events
are routed to the ScrollbackCommitter rather than mounted into the internal
#messages tree.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.widgets.messages import (
    HookRunContainer,
    UserMessage,
    WhatsNewMessage,
)
from vibe.core.hooks.models import (
    HookEndEvent,
    HookMessageSeverity,
    HookRunEndEvent,
    HookRunStartEvent,
    HookType,
)
from vibe.core.types import AssistantEvent, BaseEvent, WaitingForInputEvent


async def _events(*events: BaseEvent) -> AsyncGenerator[BaseEvent]:
    for event in events:
        yield event


@pytest.mark.asyncio
async def test_mount_and_scroll_routes_durable_widget_to_committer() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None

        messages_before = list(app._messages_area.children)
        await app._mount_and_scroll(UserMessage("hello native"))
        await pilot.pause()

        # Consumed by the committer: not mounted into the internal transcript.
        assert list(app._messages_area.children) == messages_before
        assert app._committer.has_pending is True
        text = "\n".join(app._committer.drain_lines())
        assert "hello native" in text


@pytest.mark.asyncio
async def test_agent_events_route_to_committer() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None

        await app._handle_agent_loop_events(
            _events(
                AssistantEvent(content="streamed "),
                AssistantEvent(content="answer"),
                WaitingForInputEvent(task_id="t"),
            )
        )
        await pilot.pause()

        assert app._committer.has_pending is True
        text = "\n".join(app._committer.drain_lines())
        assert text.count("streamed answer") == 1


@pytest.mark.asyncio
async def test_whats_new_shows_live_and_never_becomes_transcript() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()  # drop the startup-header baseline

        message = WhatsNewMessage("**What's new**")
        await app._live_surface.mount(message)
        app._whats_new_message = message
        await pilot.pause()

        # Shown live in the inline region, not mounted into the hidden transcript.
        assert message in app._live_surface.children
        assert message not in app._messages_area.children
        # Never durable: nothing was committed to scrollback.
        assert app._committer.has_pending is False

        # Disappears cleanly on dismissal, still without becoming transcript.
        await message.remove()
        app._whats_new_message = None
        await pilot.pause()
        assert message not in app._live_surface.children
        assert app._committer.has_pending is False


@pytest.mark.asyncio
async def test_hook_run_routes_grouped_block_without_hidden_container() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()  # drop the startup-header baseline

        await app._handle_agent_loop_events(
            _events(
                HookRunStartEvent(scope=HookType.POST_AGENT_TURN),
                HookEndEvent(
                    hook_name="format",
                    status=HookMessageSeverity.OK,
                    content="formatted 2 files",
                ),
                HookRunEndEvent(scope=HookType.POST_AGENT_TURN),
                WaitingForInputEvent(task_id="t"),
            )
        )
        await pilot.pause()

        # Grouped hook output reaches native scrollback.
        text = "\n".join(app._committer.drain_lines())
        assert "post-agent-turn" in text
        assert "[format] formatted 2 files" in text
        # No HookRunContainer is mounted into the hidden transcript.
        assert not any(
            isinstance(child, HookRunContainer) for child in app._messages_area.children
        )


@pytest.mark.asyncio
async def test_assistant_markdown_renders_with_formatting() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        await app._handle_agent_loop_events(
            _events(
                AssistantEvent(content="# Heading\n\nsome **bold** words"),
                WaitingForInputEvent(task_id="t"),
            )
        )
        await pilot.pause()
        text = "\n".join(app._committer.drain_lines())
        # Markdown rendered (heading text + body), with ANSI styling, not raw md.
        assert "Heading" in text
        assert "bold" in text
        assert "\x1b[" in text
        assert "**" not in text  # not raw markdown
