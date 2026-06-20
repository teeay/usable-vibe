"""Transcript-rendering coverage for native scroll mode.

Replaces the deleted full-screen *transcript* snapshot tests: in the patched
(native) UI the conversation is rendered by the ScrollbackCommitter into the
terminal scrollback rather than into on-screen widgets, so these assert on the
committed scrollback text instead of an SVG of the screen.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from tests.conftest import build_test_vibe_app, committed_scrollback
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.scrollback_committer import ScrollbackCommitter
from vibe.cli.textual_ui.widgets.messages import UserMessage, WhatsNewMessage
from vibe.core.types import (
    AssistantEvent,
    ImageAttachment,
    ReasoningEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    WaitingForInputEvent,
)


def _committer(app: VibeApp) -> ScrollbackCommitter:
    committer = app._committer
    assert committer is not None
    return committer


async def _commit_events(app, *events) -> str:
    committer = _committer(app)
    for event in events:
        committer.handle_event(event)
    committer.flush()
    return committed_scrollback(app)


@pytest.mark.asyncio
async def test_basic_conversation_assistant_markdown_reaches_scrollback() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        text = await _commit_events(
            app,
            AssistantEvent(content="I'm the Vibe agent and I'm **ready** to help."),
            WaitingForInputEvent(task_id="t"),
        )
        assert "I'm the Vibe agent and I'm ready to help." in text
        assert "**" not in text  # rendered markdown, not raw


@pytest.mark.asyncio
async def test_reasoning_content_reaches_scrollback() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        text = await _commit_events(
            app,
            ReasoningEvent(content="Let me think about the parser design."),
            WaitingForInputEvent(task_id="t"),
        )
        assert "Thinking" in text
        assert "think about the parser design" in text


@pytest.mark.asyncio
async def test_streaming_code_fence_is_preserved() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        text = await _commit_events(
            app,
            AssistantEvent(
                content="Here is code:\n\n```python\nprint('hi there')\n```"
            ),
            WaitingForInputEvent(task_id="t"),
        )
        assert "print('hi there')" in text


@pytest.mark.asyncio
async def test_empty_assistant_before_reasoning_is_not_committed() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        # An empty assistant chunk arrives, then reasoning starts.
        text = await _commit_events(
            app,
            AssistantEvent(content=""),
            ReasoningEvent(content="actual reasoning"),
            WaitingForInputEvent(task_id="t"),
        )
        assert "actual reasoning" in text
        # No stray empty assistant block / no error fallback.
        assert "Error" not in text


@pytest.mark.asyncio
async def test_whats_new_is_excluded_from_scrollback() -> None:
    # WhatsNewMessage is a startup notice, not conversation transcript: it must
    # never be committed to scrollback (ui-map.md, status excluded).
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        _committer(app).drain_lines()  # drop the startup-header baseline
        consumed = _committer(app).render_widget(WhatsNewMessage("## What's New"))
        assert consumed is False
        assert committed_scrollback(app) == ""


@pytest.mark.asyncio
async def test_user_message_image_attachments_reach_scrollback(tmp_path: Path) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        message = UserMessage(
            "look at this",
            images=[
                ImageAttachment(path=image, alias="shot.png", mime_type="image/png")
            ],
        )
        assert _committer(app).render_widget(message) is True
        text = committed_scrollback(app)
        assert "look at this" in text
        assert "shot.png" in text
        assert "attached image" in text


@pytest.mark.asyncio
async def test_ask_user_question_answers_reach_scrollback() -> None:
    from vibe.core.tools.builtins.ask_user_question import (
        Answer,
        AskUserQuestion,
        AskUserQuestionResult,
    )

    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        result = AskUserQuestionResult(
            answers=[Answer(question="Which DB?", answer="SQLite", is_other=False)],
            cancelled=False,
        )
        _committer(app).handle_event(
            ToolResultEvent(
                tool_name="ask_user_question",
                tool_class=AskUserQuestion,
                result=result,
                tool_call_id="q1",
            )
        )
        text = committed_scrollback(app)
        # The submitted answer is durable transcript, committed exactly once, and
        # is not mounted into the hidden #messages tree.
        assert text.count("SQLite") == 1
        assert len(app.query_one("#messages").children) == 0


@pytest.mark.asyncio
async def test_question_app_form_lifecycle_commits_answer_once() -> None:
    # Exercises the real bottom-app transition end to end: the live QuestionApp
    # form, its submit bridge through on_question_app_answered, and the resulting
    # durable commit -- not just the body renderer.
    from vibe.cli.textual_ui.widgets.question_app import QuestionApp
    from vibe.core.tools.builtins.ask_user_question import (
        AskUserQuestion,
        AskUserQuestionArgs,
        AskUserQuestionResult,
        Choice,
        Question,
    )

    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        _committer(app).drain_lines()  # drop the startup-header baseline
        args = AskUserQuestionArgs(
            questions=[
                Question(
                    question="Which DB?",
                    header="DB",
                    options=[Choice(label="Postgres"), Choice(label="SQLite")],
                )
            ]
        )
        callback = asyncio.ensure_future(app._user_input_callback(args))

        question_app: QuestionApp | None = None
        for _ in range(50):
            found = app.query(QuestionApp)
            if found:
                question_app = found.first()
                break
            await pilot.pause()
        assert question_app is not None

        # While the form is live it is not durable transcript.
        assert committed_scrollback(app) == ""
        assert len(app.query_one("#messages").children) == 0

        # Submit through the widget's real submission path; the posted Answered
        # message is bridged to the pending-question future by the app.
        question_app.selected_option = 0
        question_app._save_current_answer()
        question_app._submit()
        result = cast(
            AskUserQuestionResult, await asyncio.wait_for(callback, timeout=2)
        )

        assert result.cancelled is False
        assert result.answers[0].answer == "Postgres"
        # The form is torn down and never became transcript.
        assert not app.query(QuestionApp)
        assert len(app.query_one("#messages").children) == 0

        # The agent loop emits the tool result for the answered question; it
        # commits exactly once as durable transcript.
        _committer(app).handle_event(
            ToolResultEvent(
                tool_name="ask_user_question",
                tool_class=AskUserQuestion,
                result=result,
                tool_call_id="q1",
            )
        )
        text = committed_scrollback(app)
        assert text.count("Postgres") == 1
        assert len(app.query_one("#messages").children) == 0


@pytest.mark.asyncio
async def test_question_app_cancel_commits_cancellation() -> None:
    from vibe.cli.textual_ui.widgets.question_app import QuestionApp
    from vibe.core.tools.builtins.ask_user_question import (
        AskUserQuestion,
        AskUserQuestionArgs,
        AskUserQuestionResult,
        Choice,
        Question,
    )

    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        _committer(app).drain_lines()  # drop the startup-header baseline
        args = AskUserQuestionArgs(
            questions=[
                Question(
                    question="Which DB?",
                    header="DB",
                    options=[Choice(label="Postgres"), Choice(label="SQLite")],
                )
            ]
        )
        callback = asyncio.ensure_future(app._user_input_callback(args))

        question_app: QuestionApp | None = None
        for _ in range(50):
            found = app.query(QuestionApp)
            if found:
                question_app = found.first()
                break
            await pilot.pause()
        assert question_app is not None

        # Cancel through the widget's real action (bypassing only the grace timer).
        question_app._mount_time = 0.0
        question_app.action_cancel()
        result = cast(
            AskUserQuestionResult, await asyncio.wait_for(callback, timeout=2)
        )

        assert result.cancelled is True
        assert not app.query(QuestionApp)

        _committer(app).handle_event(
            ToolResultEvent(
                tool_name="ask_user_question",
                tool_class=AskUserQuestion,
                result=result,
                tool_call_id="q1",
            )
        )
        text = committed_scrollback(app)
        assert text.count("User cancelled") == 1
        assert len(app.query_one("#messages").children) == 0


@pytest.mark.asyncio
async def test_bash_result_body_reaches_scrollback() -> None:
    from vibe.core.tools.builtins.bash import Bash, BashResult

    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        _committer(app).handle_event(
            ToolResultEvent(
                tool_name="bash",
                tool_class=Bash,
                result=BashResult(
                    command="ls", stdout="alpha.txt\n", stderr="", returncode=0
                ),
                tool_call_id="b1",
            )
        )
        text = committed_scrollback(app)
        assert text.count("alpha.txt") == 1
        assert len(app.query_one("#messages").children) == 0


@pytest.mark.asyncio
async def test_edit_result_diff_reaches_scrollback() -> None:
    from vibe.core.tools.builtins.edit import Edit, EditResult

    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        _committer(app).handle_event(
            ToolResultEvent(
                tool_name="edit",
                tool_class=Edit,
                result=EditResult(
                    file="a.py",
                    message="Edited",
                    old_string="a\nb\nc",
                    new_string="a\nB\nc",
                ),
                tool_call_id="e1",
            )
        )
        text = committed_scrollback(app)
        assert "- b" in text
        assert "+ B" in text
        assert len(app.query_one("#messages").children) == 0


@pytest.mark.asyncio
async def test_active_tool_call_and_stream_stay_live() -> None:
    from vibe.core.tools.builtins.bash import Bash

    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        _committer(app).drain_lines()  # drop the startup-header baseline
        # The active call and its progress are live-only: nothing is committed to
        # scrollback until the result arrives.
        _committer(app).handle_event(
            ToolCallEvent(tool_name="bash", tool_class=Bash, tool_call_id="b1")
        )
        _committer(app).handle_event(
            ToolStreamEvent(tool_name="bash", message="running…", tool_call_id="b1")
        )
        assert _committer(app).has_pending is False
        assert committed_scrollback(app) == ""
