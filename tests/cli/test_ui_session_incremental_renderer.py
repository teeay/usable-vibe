from __future__ import annotations

import pytest

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from tests.conftest import build_test_agent_loop
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIPlanType, WhoAmIResponse
from vibe.cli.textual_ui.app import ChatScroll, VibeApp
from vibe.cli.textual_ui.widgets.load_more import HistoryLoadMoreMessage
from vibe.cli.textual_ui.widgets.messages import UserMessage
from vibe.cli.textual_ui.windowing import HISTORY_RESUME_TAIL_MESSAGES
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.types import LLMMessage, Role

# Native mode commits a bounded recent tail of resumed history to host scrollback
# and records the remainder with a durable marker, instead of mounting history
# widgets into the hidden #messages tree with an interactive load-more button.


@pytest.fixture
def vibe_config() -> VibeConfig:
    return VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False), enable_update_checks=False
    )


def _pro_plan_gateway() -> FakeWhoAmIGateway:
    return FakeWhoAmIGateway(
        response=WhoAmIResponse(
            plan_type=WhoAmIPlanType.CHAT,
            plan_name="INDIVIDUAL",
            prompt_switching_to_pro_plan=False,
        )
    )


@pytest.mark.asyncio
async def test_resume_commits_bounded_tail_and_omitted_marker(
    vibe_config: VibeConfig,
) -> None:
    total = 66
    omitted = total - HISTORY_RESUME_TAIL_MESSAGES
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content=f"msg-{idx}") for idx in range(total)
    ])

    app = VibeApp(agent_loop=agent_loop, plan_offer_gateway=_pro_plan_gateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        text = "\n".join(app._committer.drain_lines())

    # Only the recent tail is committed; earlier messages are summarized.
    assert f"{omitted} earlier messages omitted" in text
    assert "msg-65" in text  # newest, in the tail
    assert "msg-46" in text  # oldest message still inside the tail
    assert "msg-45" not in text  # first omitted message is not committed

    # No history widgets and no interactive load-more in native mode.
    assert len(app.query(UserMessage)) == 0
    assert len(app.query(HistoryLoadMoreMessage)) == 0


@pytest.mark.asyncio
async def test_resume_marker_is_singular_for_one_omitted_message(
    vibe_config: VibeConfig,
) -> None:
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content=f"msg-{idx}")
        for idx in range(HISTORY_RESUME_TAIL_MESSAGES + 1)
    ])

    app = VibeApp(agent_loop=agent_loop, plan_offer_gateway=_pro_plan_gateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        text = "\n".join(app._committer.drain_lines())

    assert "1 earlier message omitted" in text


@pytest.mark.asyncio
async def test_resume_does_not_populate_hidden_chat(vibe_config: VibeConfig) -> None:
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content=f"msg-{idx}") for idx in range(31)
    ])

    app = VibeApp(agent_loop=agent_loop, plan_offer_gateway=_pro_plan_gateway())

    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat", ChatScroll)
        # The hidden chat is collapsed and owns no durable transcript.
        assert chat.display is False
        assert list(app._messages_area.children) == []
