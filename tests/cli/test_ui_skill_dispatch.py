from __future__ import annotations

from pathlib import Path
import time

import pytest

from tests.conftest import (
    build_test_vibe_app,
    build_test_vibe_config,
    committed_scrollback,
)
from tests.skills.conftest import create_skill
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer

SKILL_BODY = "## Instructions\n\nDo the thing."


@pytest.fixture
def vibe_app_with_skills(tmp_path: Path) -> VibeApp:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    create_skill(skills_dir, "my-skill", body=SKILL_BODY)
    return build_test_vibe_app(config=build_test_vibe_config(skill_paths=[skills_dir]))


async def _wait_for_scrollback_containing(
    vibe_app: VibeApp, pilot, text: str, timeout: float = 1.0
) -> str:
    """The dispatched prompt is committed to native scrollback, not mounted."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        scrollback = committed_scrollback(vibe_app)
        if text in scrollback:
            return scrollback
        await pilot.pause(0.05)
    raise TimeoutError(
        f"Scrollback containing {text!r} did not appear within {timeout}s"
    )


@pytest.mark.asyncio
async def test_skill_without_args_sends_skill_content(
    vibe_app_with_skills: VibeApp,
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/my-skill"))
        await pilot.pause(0.1)

        scrollback = await _wait_for_scrollback_containing(
            vibe_app_with_skills, pilot, "Do the thing."
        )
        assert "Do the thing." in scrollback


@pytest.mark.asyncio
async def test_skill_with_args_prepends_invocation_line(
    vibe_app_with_skills: VibeApp,
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/my-skill foo bar"))
        await pilot.pause(0.1)

        scrollback = await _wait_for_scrollback_containing(
            vibe_app_with_skills, pilot, "Do the thing."
        )
        assert "/my-skill foo bar" in scrollback
        assert "Do the thing." in scrollback


@pytest.mark.asyncio
async def test_unknown_skill_falls_through_to_agent(
    vibe_app_with_skills: VibeApp,
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/nonexistent-skill"))
        await pilot.pause(0.2)

        # Falls through to the agent as a normal prompt; no error is committed.
        assert "Error" not in committed_scrollback(vibe_app_with_skills)


@pytest.mark.asyncio
async def test_bare_slash_falls_through(vibe_app_with_skills: VibeApp) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/"))
        await pilot.pause(0.2)

        assert "Do the thing." not in committed_scrollback(vibe_app_with_skills)


@pytest.mark.asyncio
async def test_skill_without_args_does_not_prepend_invocation_line(
    vibe_app_with_skills: VibeApp,
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/my-skill"))
        await pilot.pause(0.1)

        scrollback = await _wait_for_scrollback_containing(
            vibe_app_with_skills, pilot, "Do the thing."
        )
        assert "/my-skill" not in scrollback


@pytest.mark.asyncio
async def test_popped_queued_skill_does_not_fire_telemetry(
    vibe_app_with_skills: VibeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with vibe_app_with_skills.run_test() as pilot:
        events: list[tuple[str, str]] = []
        monkeypatch.setattr(
            vibe_app_with_skills.agent_loop.telemetry_client,
            "send_slash_command_used",
            lambda name, kind: events.append((name, kind)),
        )

        chat_input = vibe_app_with_skills.query_one(ChatInputContainer)
        vibe_app_with_skills._agent_running = True
        try:
            chat_input.post_message(ChatInputContainer.Submitted("/my-skill"))
            await pilot.pause(0.1)
            assert len(vibe_app_with_skills._input_queue) == 1

            await pilot.press("ctrl+c")
            await pilot.pause(0.1)
            assert len(vibe_app_with_skills._input_queue) == 0
            assert events == []
        finally:
            vibe_app_with_skills._agent_running = False
