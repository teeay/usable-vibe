from __future__ import annotations

import asyncio
import time
from unittest.mock import Mock

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


async def _wait_until(pause, predicate, timeout: float = 2.0) -> None:
    start = time.monotonic()
    while (time.monotonic() - start) < timeout:
        if predicate():
            return
        await pause(0.02)
    raise AssertionError("Condition was not met within the timeout")


@pytest.mark.asyncio
async def test_ui_mount_defers_history_resume(
    vibe_config: VibeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    app = VibeApp(agent_loop=agent_loop, plan_offer_gateway=_pro_plan_gateway())
    history_started = asyncio.Event()
    history_release = asyncio.Event()
    restore_from_session = Mock()
    loop_start = Mock()
    initialize_experiments = Mock()

    async def resume_history() -> None:
        history_started.set()
        await history_release.wait()

    monkeypatch.setattr(app, "_resume_history_from_messages", resume_history)
    monkeypatch.setattr(app._loop_runner, "restore_from_session", restore_from_session)
    monkeypatch.setattr(app._loop_runner, "start", loop_start)
    monkeypatch.setattr(
        agent_loop, "start_initialize_experiments", initialize_experiments
    )

    async with asyncio.timeout(5):
        async with app.run_test() as pilot:
            await _wait_until(pilot.pause, history_started.is_set, timeout=2.0)

            app.query_one(ChatScroll)
            restore_from_session.assert_not_called()
            loop_start.assert_not_called()
            initialize_experiments.assert_not_called()

            history_release.set()
            await _wait_until(
                pilot.pause,
                lambda: (
                    restore_from_session.call_count == 1
                    and loop_start.call_count == 1
                    and initialize_experiments.call_count == 1
                ),
                timeout=2.0,
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

    assert f"{omitted} earlier messages omitted" in text
    assert "msg-65" in text
    assert "msg-46" in text
    assert "msg-45" not in text

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
        assert chat.display is False
        assert list(app._messages_area.children) == []
