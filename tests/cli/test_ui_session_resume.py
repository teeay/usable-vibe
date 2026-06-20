from __future__ import annotations

from pathlib import Path
import time
from unittest.mock import patch

import pytest

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
)
from tests.update_notifier.adapters.fake_update_cache_repository import (
    FakeUpdateCacheRepository,
)
from tests.update_notifier.adapters.fake_update_gateway import FakeUpdateGateway
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIPlanType, WhoAmIResponse
from vibe.cli.textual_ui.widgets.messages import (
    AssistantMessage,
    UserMessage,
    WhatsNewMessage,
)
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage
from vibe.cli.update_notifier import UpdateCache
from vibe.core.config import VibeConfig
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall


@pytest.mark.asyncio
async def test_ui_commits_messages_to_scrollback_when_resuming_session(
    vibe_config: VibeConfig,
) -> None:
    agent_loop = build_test_agent_loop(config=vibe_config)

    user_msg = LLMMessage(role=Role.user, content="Hello, how are you?")
    assistant_msg = LLMMessage(
        role=Role.assistant,
        content="I'm doing well, thank you!",
        tool_calls=[
            ToolCall(
                id="tool_call_1",
                index=0,
                function=FunctionCall(
                    name="read", arguments='{"file_path": "test.txt"}'
                ),
            )
        ],
    )
    tool_result_msg = LLMMessage(
        role=Role.tool,
        content="File content here",
        name="read",
        tool_call_id="tool_call_1",
    )

    agent_loop.messages.extend([user_msg, assistant_msg, tool_result_msg])

    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause(0.5)

        assert app._committer is not None
        text = "\n".join(app._committer.drain_lines())

        # Resumed history is committed to native scrollback, not mounted into the
        # hidden #messages tree.
        assert "Hello, how are you?" in text
        assert "I'm doing well, thank you!" in text
        assert "read" in text
        assert "File content here" in text

        assert list(app._messages_area.children) == []
        assert len(app.query(UserMessage)) == 0
        assert len(app.query(AssistantMessage)) == 0
        assert len(app.query(ToolCallMessage)) == 0
        assert len(app.query(ToolResultMessage)) == 0


@pytest.mark.asyncio
async def test_ui_commits_nothing_when_only_system_messages_exist(
    vibe_config: VibeConfig,
) -> None:
    agent_loop = build_test_agent_loop(config=vibe_config)

    system_msg = LLMMessage(role=Role.system, content="System prompt")
    agent_loop.messages.append(system_msg)

    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause(0.5)

        assert list(app._messages_area.children) == []
        assert len(app.query(UserMessage)) == 0
        assert len(app.query(AssistantMessage)) == 0


@pytest.mark.asyncio
async def test_ui_commits_multiple_user_assistant_turns(
    vibe_config: VibeConfig,
) -> None:
    agent_loop = build_test_agent_loop(config=vibe_config)

    messages = [
        LLMMessage(role=Role.user, content="First question"),
        LLMMessage(role=Role.assistant, content="First answer"),
        LLMMessage(role=Role.user, content="Second question"),
        LLMMessage(role=Role.assistant, content="Second answer"),
    ]

    agent_loop.messages.extend(messages)

    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause(0.5)

        assert app._committer is not None
        text = "\n".join(app._committer.drain_lines())
        assert "First question" in text
        assert "First answer" in text
        assert "Second question" in text
        assert "Second answer" in text

        assert list(app._messages_area.children) == []


@pytest.mark.asyncio
async def test_ui_commits_messages_when_resuming_in_dangerous_directory(
    monkeypatch: pytest.MonkeyPatch, vibe_config: VibeConfig
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.is_dangerous_directory",
        lambda: (True, "You are in the home directory"),
    )

    agent_loop = build_test_agent_loop(config=vibe_config)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content="Hello from a previous run"),
        LLMMessage(role=Role.assistant, content="Welcome back!"),
    ])

    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause(0.5)

        assert app._committer is not None
        text = "\n".join(app._committer.drain_lines())
        assert "Hello from a previous run" in text
        assert "Welcome back!" in text

        assert list(app._messages_area.children) == []


@pytest.mark.asyncio
async def test_ui_commits_history_with_whats_new_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # we have to define an api key to make sure we display the Plan Offer message
    monkeypatch.setenv("MISTRAL_API_KEY", "api-key")
    config = build_test_vibe_config(enable_update_checks=True)
    agent_loop = build_test_agent_loop(config=config)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content="Hello from the previous session."),
        LLMMessage(role=Role.assistant, content="Welcome back!"),
    ])
    update_cache = UpdateCache(
        latest_version="1.0.0",
        stored_at_timestamp=int(time.time()),
        seen_whats_new_version=None,
    )
    update_cache_repository = FakeUpdateCacheRepository(update_cache=update_cache)
    plan_offer_gateway = FakeWhoAmIGateway(
        WhoAmIResponse(
            plan_type=WhoAmIPlanType.API,
            plan_name="FREE",
            prompt_switching_to_pro_plan=False,
        )
    )
    app = build_test_vibe_app(
        agent_loop=agent_loop,
        update_notifier=FakeUpdateGateway(update=None),
        update_cache_repository=update_cache_repository,
        plan_offer_gateway=plan_offer_gateway,
        current_version="1.0.0",
        config=config,
    )

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        whats_new_file = tmp_path / "whats_new.md"
        whats_new_file.write_text("# What's New\n\n- Feature 1")

        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            whats_new_message = app.query_one(WhatsNewMessage)
            messages_area = app.query_one("#messages")
            children = list(messages_area.children)
            assert app._committer is not None
            text = "\n".join(app._committer.drain_lines())

    # History is committed to scrollback; the what's-new notice is a live surface,
    # never the hidden transcript.
    assert "Hello from the previous session." in text
    assert "Welcome back!" in text
    assert whats_new_message is not None
    assert whats_new_message.parent is not messages_area
    assert children == []
