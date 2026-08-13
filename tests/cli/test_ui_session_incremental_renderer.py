from __future__ import annotations

import asyncio
from pathlib import Path
import time

import pytest

from tests.conftest import build_test_agent_loop
from tests.stubs.app_server import create_test_app_server_session
from tests.stubs.fake_account_gateway import FakeAccountGateway
from vibe.app_server._account import WhoAmIResult
from vibe.app_server.models import AccountPlanKind
from vibe.cli.textual_ui.app import ChatScroll, VibeApp
from vibe.cli.textual_ui.widgets.load_more import HistoryLoadMoreMessage
from vibe.cli.textual_ui.widgets.messages import UserMessage
from vibe.cli.textual_ui.windowing import HISTORY_RESUME_TAIL_MESSAGES
from vibe.core.config import SessionLoggingConfig, VibeConfigSchema
from vibe.core.types import LLMMessage, Role


@pytest.fixture
def vibe_config(make_config) -> VibeConfigSchema:
    return make_config(
        session_logging=SessionLoggingConfig(enabled=False), enable_update_checks=False
    )


def _pro_account_gateway() -> FakeAccountGateway:
    return FakeAccountGateway(
        result=WhoAmIResult(
            plan_type=AccountPlanKind.CHAT,
            plan_name="INDIVIDUAL",
            prompt_switching_to_pro_plan=False,
        )
    )


def _app(agent_loop) -> VibeApp:
    account_gateway = _pro_account_gateway()
    return VibeApp(
        app_server=lambda: create_test_app_server_session(
            agent_loop, account_gateway=account_gateway
        ),
        history_file=Path(".vibehistory"),
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
    vibe_config: VibeConfigSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    app = _app(agent_loop)
    await app.prepare()
    history_started = asyncio.Event()
    history_release = asyncio.Event()
    event_listener_started = asyncio.Event()
    event_listener_release = asyncio.Event()

    async def resume_history() -> None:
        history_started.set()
        await history_release.wait()

    async def listen_app_server_events() -> None:
        event_listener_started.set()
        await event_listener_release.wait()

    monkeypatch.setattr(app, "_resume_history_from_messages", resume_history)
    monkeypatch.setattr(app, "_listen_app_server_events", listen_app_server_events)
    async with asyncio.timeout(5):
        async with app.run_test() as pilot:
            await _wait_until(pilot.pause, history_started.is_set, timeout=2.0)

            app.query_one(ChatScroll)
            assert not event_listener_started.is_set()

            history_release.set()
            await _wait_until(pilot.pause, event_listener_started.is_set, timeout=2.0)
            event_listener_release.set()


@pytest.mark.asyncio
async def test_resume_commits_bounded_tail_and_omitted_marker(
    vibe_config: VibeConfigSchema,
) -> None:
    total = 66
    omitted = total - HISTORY_RESUME_TAIL_MESSAGES
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content=f"msg-{idx}") for idx in range(total)
    ])

    app = _app(agent_loop)

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
    vibe_config: VibeConfigSchema,
) -> None:
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content=f"msg-{idx}")
        for idx in range(HISTORY_RESUME_TAIL_MESSAGES + 1)
    ])

    app = _app(agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        text = "\n".join(app._committer.drain_lines())

    assert "1 earlier message omitted" in text


@pytest.mark.asyncio
async def test_resume_does_not_populate_hidden_chat(
    vibe_config: VibeConfigSchema,
) -> None:
    agent_loop = build_test_agent_loop(config=vibe_config, enable_streaming=False)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content=f"msg-{idx}") for idx in range(31)
    ])

    app = _app(agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one("#chat", ChatScroll)
        assert chat.display is False
        assert list(app._messages_area.children) == []
