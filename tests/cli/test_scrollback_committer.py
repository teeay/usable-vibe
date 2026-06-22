"""Unit tests for ScrollbackCommitter: event/widget -> block conversion and
streaming buffering, without starting Textual or a terminal.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from pydantic import BaseModel
from textual.widgets import Static

from vibe.cli.textual_ui.scrollback_committer import ScrollbackCommitter
from vibe.cli.textual_ui.widgets.messages import (
    ErrorMessage,
    SlashCommandMessage,
    UserCommandMessage,
    UserMessage,
    WarningMessage,
)
from vibe.core.hooks.models import (
    HookEndEvent,
    HookMessageSeverity,
    HookRunEndEvent,
    HookRunStartEvent,
    HookType,
)
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, InvokeContext
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import (
    AssistantEvent,
    BaseEvent,
    CompactEndEvent,
    FileImageSource,
    ImageAttachment,
    ReasoningEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolStreamEvent,
    UserMessageEvent,
)


class _UnknownEvent(BaseEvent):
    detail: str = "surprise"


class _Args(BaseModel):
    path: str = "x"


class _Result(BaseModel):
    ok: bool = True


class _FakeTool(
    BaseTool[_Args, _Result, BaseToolConfig, BaseToolState], ToolUIData[_Args, _Result]
):
    async def run(
        self, args: _Args, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | _Result, None]:
        yield _Result()

    @classmethod
    def get_status_text(cls) -> str:
        return "Running fake"

    @classmethod
    def format_call_display(cls, args: _Args) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"Reading {args.path}")

    @classmethod
    def format_result_display(cls, result: _Result) -> ToolResultDisplay:
        return ToolResultDisplay(success=True, message="Read 3 lines")


def _committer() -> ScrollbackCommitter:
    # color_system=None renders plain text so assertions are about content.
    return ScrollbackCommitter(width_getter=lambda: 80, color_system=None)


def _drain_text(committer: ScrollbackCommitter) -> str:
    return "\n".join(committer.drain_lines())


def test_drain_empty_is_noop() -> None:
    committer = _committer()
    assert committer.has_pending is False
    assert committer.drain_lines() == []


def test_assistant_streaming_buffers_until_flush() -> None:
    committer = _committer()
    committer.handle_event(AssistantEvent(content="Hello "))
    committer.handle_event(AssistantEvent(content="world"))
    # Buffered, nothing queued yet.
    assert committer.has_pending is False
    committer.flush()
    assert committer.has_pending is True
    text = _drain_text(committer)
    # Single coalesced block; no duplicated chunks.
    assert text.count("Hello world") == 1
    assert committer.has_pending is False


def test_reasoning_then_assistant_preserve_order() -> None:
    committer = _committer()
    committer.handle_event(ReasoningEvent(content="thinking hard"))
    committer.handle_event(AssistantEvent(content="the answer"))
    committer.flush()
    text = _drain_text(committer)
    assert "Thinking" in text
    assert text.index("thinking hard") < text.index("the answer")


def test_tool_call_result_commits_summary_line() -> None:
    committer = _committer()
    committer.handle_event(
        ToolCallEvent(
            tool_name="fake",
            tool_class=_FakeTool,
            args=_Args(path="a.py"),
            tool_call_id="c1",
        )
    )
    # Call alone is live-only: nothing queued.
    assert committer.has_pending is False
    committer.handle_event(
        ToolResultEvent(
            tool_name="fake", tool_class=_FakeTool, result=_Result(), tool_call_id="c1"
        )
    )
    text = _drain_text(committer)
    assert "✓" in text
    assert "Reading a.py" in text
    assert "Read 3 lines" in text


def test_tool_error_result_renders_failure() -> None:
    committer = _committer()
    committer.handle_event(
        ToolResultEvent(
            tool_name="fake", tool_class=_FakeTool, error="boom", tool_call_id="c2"
        )
    )
    text = _drain_text(committer)
    assert "✗" in text
    assert "boom" in text


def test_bash_result_commits_output_body() -> None:
    from vibe.core.tools.builtins.bash import Bash, BashResult

    committer = _committer()
    committer.handle_event(
        ToolResultEvent(
            tool_name="bash",
            tool_class=Bash,
            result=BashResult(
                command="ls", stdout="alpha.txt\nbeta.txt\n", stderr="", returncode=0
            ),
            tool_call_id="b1",
        )
    )
    text = _drain_text(committer)
    assert text.count("alpha.txt") == 1
    assert "beta.txt" in text


def test_edit_result_commits_diff_body() -> None:
    from vibe.core.tools.builtins.edit import Edit, EditResult

    committer = _committer()
    committer.handle_event(
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
    text = _drain_text(committer)
    assert "- b" in text
    assert "+ B" in text


def test_ask_user_question_result_commits_answers_exactly_once() -> None:
    from vibe.core.tools.builtins.ask_user_question import (
        Answer,
        AskUserQuestion,
        AskUserQuestionResult,
    )

    committer = _committer()
    committer.handle_event(
        ToolResultEvent(
            tool_name="ask_user_question",
            tool_class=AskUserQuestion,
            result=AskUserQuestionResult(
                answers=[Answer(question="Which db?", answer="Postgres")],
                cancelled=False,
            ),
            tool_call_id="q1",
        )
    )
    text = _drain_text(committer)
    # The structured answer is committed once; the summary line is not also
    # emitted, so the answer is not duplicated.
    assert text.count("Postgres") == 1


def test_tool_body_drops_generic_summary_line() -> None:
    from vibe.core.tools.builtins.bash import Bash, BashResult

    committer = _committer()
    committer.handle_event(
        ToolResultEvent(
            tool_name="bash",
            tool_class=Bash,
            result=BashResult(
                command="echo hi", stdout="output\n", stderr="", returncode=0
            ),
            tool_call_id="b2",
        )
    )
    text = _drain_text(committer)
    # The body carries the detail; the adapter's summary message ("Ran <cmd>")
    # must not appear as a separate line alongside it.
    assert "output" in text
    assert "Ran echo hi" not in text


def test_write_file_result_commits_content_body() -> None:
    from vibe.core.tools.builtins.write_file import WriteFile, WriteFileResult

    committer = _committer()
    committer.handle_event(
        ToolResultEvent(
            tool_name="write_file",
            tool_class=WriteFile,
            result=WriteFileResult(
                path="hello.py", bytes_written=11, content="print('hi')\n"
            ),
            tool_call_id="w1",
        )
    )
    assert "print('hi')" in _drain_text(committer)


def test_read_result_commits_content_body_without_line_numbers() -> None:
    from vibe.core.tools.builtins.read import Read, ReadResult

    committer = _committer()
    committer.handle_event(
        ToolResultEvent(
            tool_name="read",
            tool_class=Read,
            result=ReadResult(
                file_path="a.py",
                content="   1→import os\n   2→print(os)",
                num_lines=2,
                start_line=1,
            ),
            tool_call_id="r1",
        )
    )
    text = _drain_text(committer)
    assert "import os" in text
    assert "1→" not in text


def test_grep_result_commits_match_body() -> None:
    from vibe.core.tools.builtins.grep import Grep, GrepResult

    committer = _committer()
    committer.handle_event(
        ToolResultEvent(
            tool_name="grep",
            tool_class=Grep,
            result=GrepResult(
                matches="src/a.py:1:hit\nsrc/b.py:5:hit",
                match_count=2,
                was_truncated=False,
            ),
            tool_call_id="g1",
        )
    )
    text = _drain_text(committer)
    assert "src/a.py:1:hit" in text
    assert "src/b.py:5:hit" in text


def test_todo_result_commits_status_grouped_body() -> None:
    from vibe.core.tools.builtins.todo import Todo, TodoItem, TodoResult, TodoStatus

    committer = _committer()
    committer.handle_event(
        ToolResultEvent(
            tool_name="todo",
            tool_class=Todo,
            result=TodoResult(
                message="ok",
                total_count=2,
                todos=[
                    TodoItem(id="1", content="done item", status=TodoStatus.COMPLETED),
                    TodoItem(
                        id="2", content="active item", status=TodoStatus.IN_PROGRESS
                    ),
                ],
            ),
            tool_call_id="td1",
        )
    )
    text = _drain_text(committer)
    # in_progress is grouped before completed.
    assert text.index("active item") < text.index("done item")
    assert "☑ done item" in text


def test_unregistered_builtin_result_is_summary_only() -> None:
    from vibe.core.tools.builtins.websearch import WebSearch, WebSearchResult

    committer = _committer()
    committer.handle_event(
        ToolResultEvent(
            tool_name="websearch",
            tool_class=WebSearch,
            result=WebSearchResult(
                query="capital of france", answer="Paris is the capital.", sources=[]
            ),
            tool_call_id="ws1",
        )
    )
    text = _drain_text(committer)
    # websearch has no dedicated body renderer: the summary line is committed,
    # and the large answer/source body is intentionally not dumped.
    assert "capital of france" in text
    assert "Paris is the capital." not in text


def test_compact_end_commits_marker() -> None:
    committer = _committer()
    committer.handle_event(CompactEndEvent(summary_length=10, tool_call_id="x"))
    assert "compacted" in _drain_text(committer)


def test_hook_run_commits_one_grouped_block() -> None:
    committer = _committer()
    committer.handle_event(HookRunStartEvent(scope=HookType.POST_AGENT_TURN))
    # Lines are buffered into the run, not committed individually.
    committer.handle_event(
        HookEndEvent(
            hook_name="format", status=HookMessageSeverity.OK, content="formatted"
        )
    )
    committer.handle_event(
        HookEndEvent(
            hook_name="lint", status=HookMessageSeverity.WARNING, content="2 warnings"
        )
    )
    assert committer.has_pending is False
    committer.handle_event(HookRunEndEvent(scope=HookType.POST_AGENT_TURN))
    text = _drain_text(committer)
    assert "post-agent-turn" in text
    assert "✓" in text
    assert "[format] formatted" in text
    assert "⚠" in text
    assert "[lint] 2 warnings" in text


def test_empty_hook_run_commits_nothing() -> None:
    committer = _committer()
    committer.handle_event(HookRunStartEvent(scope=HookType.POST_AGENT_TURN))
    committer.handle_event(HookRunEndEvent(scope=HookType.POST_AGENT_TURN))
    assert committer.has_pending is False
    assert committer.drain_lines() == []


def test_hook_run_omits_empty_content_lines() -> None:
    committer = _committer()
    committer.handle_event(HookRunStartEvent(scope=HookType.POST_AGENT_TURN))
    # A HookEndEvent with no content contributes no line; the run stays empty.
    committer.handle_event(
        HookEndEvent(hook_name="silent", status=HookMessageSeverity.OK, content=None)
    )
    committer.handle_event(HookRunEndEvent(scope=HookType.POST_AGENT_TURN))
    assert committer.drain_lines() == []


def test_before_and_after_tool_hook_runs_order_around_result() -> None:
    from vibe.core.tools.builtins.bash import Bash, BashResult

    committer = _committer()
    # before_tool run for call "a"
    committer.handle_event(
        HookRunStartEvent(
            scope=HookType.BEFORE_TOOL, tool_name="bash", tool_call_id="a"
        )
    )
    committer.handle_event(
        HookEndEvent(
            hook_name="guard",
            status=HookMessageSeverity.OK,
            content="precondition ok",
            scope=HookType.BEFORE_TOOL,
            tool_call_id="a",
        )
    )
    committer.handle_event(
        HookRunEndEvent(scope=HookType.BEFORE_TOOL, tool_call_id="a")
    )
    # the tool result itself
    committer.handle_event(
        ToolResultEvent(
            tool_name="bash",
            tool_class=Bash,
            result=BashResult(command="ls", stdout="out\n", stderr="", returncode=0),
            tool_call_id="a",
        )
    )
    # after_tool run for the same call
    committer.handle_event(
        HookRunStartEvent(scope=HookType.AFTER_TOOL, tool_name="bash", tool_call_id="a")
    )
    committer.handle_event(
        HookEndEvent(
            hook_name="audit",
            status=HookMessageSeverity.OK,
            content="logged",
            scope=HookType.AFTER_TOOL,
            tool_call_id="a",
        )
    )
    committer.handle_event(HookRunEndEvent(scope=HookType.AFTER_TOOL, tool_call_id="a"))
    text = _drain_text(committer)
    assert "before bash" in text
    assert "after bash" in text
    # before-tool block precedes the result; after-tool block follows it.
    assert text.index("precondition ok") < text.index("out")
    assert text.index("out") < text.index("logged")


def test_interleaved_tool_hook_runs_do_not_merge_across_call_ids() -> None:
    committer = _committer()
    committer.handle_event(
        HookRunStartEvent(
            scope=HookType.BEFORE_TOOL, tool_name="edit", tool_call_id="a"
        )
    )
    committer.handle_event(
        HookRunStartEvent(
            scope=HookType.BEFORE_TOOL, tool_name="read", tool_call_id="b"
        )
    )
    committer.handle_event(
        HookEndEvent(
            hook_name="ha",
            status=HookMessageSeverity.OK,
            content="for-a",
            scope=HookType.BEFORE_TOOL,
            tool_call_id="a",
        )
    )
    committer.handle_event(
        HookEndEvent(
            hook_name="hb",
            status=HookMessageSeverity.OK,
            content="for-b",
            scope=HookType.BEFORE_TOOL,
            tool_call_id="b",
        )
    )
    # Closing run "a" commits only its own line, not "b"'s.
    committer.handle_event(
        HookRunEndEvent(scope=HookType.BEFORE_TOOL, tool_call_id="a")
    )
    text_a = _drain_text(committer)
    assert "for-a" in text_a
    assert "for-b" not in text_a
    committer.handle_event(
        HookRunEndEvent(scope=HookType.BEFORE_TOOL, tool_call_id="b")
    )
    text_b = _drain_text(committer)
    assert "for-b" in text_b
    assert "for-a" not in text_b


def test_stray_hook_end_without_run_commits_single_line() -> None:
    committer = _committer()
    committer.handle_event(
        HookEndEvent(
            hook_name="legacy", status=HookMessageSeverity.ERROR, content="boom"
        )
    )
    text = _drain_text(committer)
    assert "✗" in text
    assert "[legacy] boom" in text


def test_commit_manual_bash_renders_durable_block() -> None:
    committer = _committer()
    committer.commit_manual_bash("echo hi", "hi", 0)
    text = _drain_text(committer)
    assert "$ echo hi" in text
    assert "hi" in text


def test_commit_manual_bash_flushes_streaming_buffer_first() -> None:
    committer = _committer()
    committer.handle_event(AssistantEvent(content="pending text"))
    committer.commit_manual_bash("ls", "out", 0)
    text = _drain_text(committer)
    # The streaming buffer is flushed before the bash block, preserving order.
    assert text.index("pending text") < text.index("$ ls")


def test_commit_manual_bash_no_output_and_interrupted() -> None:
    committer = _committer()
    committer.commit_manual_bash("sleep 5", "", 1, interrupted=True)
    text = _drain_text(committer)
    assert "(no output)" in text
    assert "interrupted" in text


def test_silent_event_queues_nothing() -> None:
    from vibe.core.types import SessionTitleUpdatedEvent

    committer = _committer()
    committer.handle_event(SessionTitleUpdatedEvent(title="t"))
    assert committer.has_pending is False


def test_unknown_event_falls_back_to_repr() -> None:
    committer = _committer()
    committer.handle_event(_UnknownEvent())
    assert "surprise" in _drain_text(committer)


def test_user_message_event_does_not_commit() -> None:
    # Local prompts are committed via render_widget (the app mounts a UserMessage
    # before the turn); UserMessageEvent must not commit them again or the prompt
    # is duplicated in scrollback.
    committer = _committer()
    committer.handle_event(UserMessageEvent(content="do the thing", message_id="m1"))
    assert committer.has_pending is False


def test_render_widget_user_message() -> None:
    committer = _committer()
    consumed = committer.render_widget(UserMessage("hello there"))
    assert consumed is True
    text = _drain_text(committer)
    assert "hello there" in text
    assert text.lstrip().startswith(">")


def test_user_message_commits_trailing_separator_rule() -> None:
    committer = _committer()
    committer.render_widget(UserMessage("first prompt"))
    text = _drain_text(committer)
    assert "first prompt" in text
    # A horizontal rule (ExpandingSeparator equivalent) follows the prompt.
    assert "─" in text


def test_user_message_attachment_carries_file_hyperlink(tmp_path: Path) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG")
    committer = ScrollbackCommitter(width_getter=lambda: 80, color_system="truecolor")
    committer.render_widget(
        UserMessage(
            "look",
            images=[
                ImageAttachment(
                    source=FileImageSource(path=image),
                    alias="shot.png",
                    mime_type="image/png",
                )
            ],
        )
    )
    text = _drain_text(committer)
    assert "shot.png" in text
    # The alias carries an OSC-8 terminal hyperlink to the file:// URI.
    assert image.as_uri() in text


def test_render_widget_slash_command_uses_prompt_char() -> None:
    committer = _committer()
    assert committer.render_widget(SlashCommandMessage("help")) is True
    assert _drain_text(committer).lstrip().startswith("/")


def test_render_widget_pending_user_message_not_consumed() -> None:
    committer = _committer()
    assert committer.render_widget(UserMessage("queued", pending=True)) is False
    assert committer.has_pending is False


def test_render_widget_error_and_warning() -> None:
    committer = _committer()
    assert committer.render_widget(ErrorMessage("kaboom")) is True
    assert "Error: kaboom" in _drain_text(committer)

    assert committer.render_widget(WarningMessage("careful")) is True
    assert "careful" in _drain_text(committer)


def test_render_widget_command_feedback_markdown() -> None:
    committer = _committer()
    assert committer.render_widget(UserCommandMessage("**bold** feedback")) is True
    text = _drain_text(committer)
    assert "bold" in text


def test_render_widget_unhandled_returns_false() -> None:
    committer = _committer()
    # A real widget the committer has no case for must be refused, not silently
    # consumed.
    assert committer.render_widget(_UnhandledWidget("ignore me")) is False


def test_render_widget_whats_new_not_consumed() -> None:
    from vibe.cli.textual_ui.widgets.messages import WhatsNewMessage

    # The what's-new notice is a transient live surface, never durable: the
    # committer must refuse it so it cannot reach native scrollback.
    committer = _committer()
    assert committer.render_widget(WhatsNewMessage("New things!")) is False
    assert committer.has_pending is False


def test_refresh_called_on_enqueue() -> None:
    calls: list[int] = []
    committer = ScrollbackCommitter(
        width_getter=lambda: 80, refresh=lambda: calls.append(1), color_system=None
    )
    committer.render_widget(UserMessage("hi"))
    assert calls  # refresh fired so the app schedules a frame


def test_close_is_idempotent_and_blocks_further_commits() -> None:
    committer = _committer()
    committer.handle_event(AssistantEvent(content="tail"))
    committer.close()
    # close() flushed the buffer.
    assert committer.has_pending is True
    committer.drain_lines()
    # After close, new content is dropped.
    committer.handle_event(UserMessageEvent(content="late", message_id="m"))
    assert committer.has_pending is False
    committer.close()  # idempotent


class _UnhandledWidget(Static):
    """A real widget the committer has no conversion case for, so render_widget
    must refuse it (isinstance checks for handled classes all fail).
    """


def test_split_commits_complete_paragraphs_keeps_trailing_live() -> None:
    committer = _committer()
    commit, remainder = committer._split_committable_blocks(
        "Para one.\n\nPara two.\n\nstill typing"
    )
    assert commit == "Para one.\n\nPara two.\n\n"
    assert remainder == "still typing"


def test_split_holds_single_incomplete_paragraph() -> None:
    committer = _committer()
    assert committer._split_committable_blocks("Hello world") == ("", "Hello world")


def test_split_never_breaks_a_fenced_code_block() -> None:
    committer = _committer()
    commit, remainder = committer._split_committable_blocks(
        "```\nline a\n\nline b\n```\n\ntail"
    )
    # The blank line inside the fence does not split the code block.
    assert commit == "```\nline a\n\nline b\n```\n\n"
    assert remainder == "tail"


def test_split_keeps_open_fence_live() -> None:
    committer = _committer()
    # An unterminated fence must stay buffered until it is closed.
    assert committer._split_committable_blocks("```py\ncode\n") == ("", "```py\ncode\n")


def test_split_groups_loose_list_so_numbering_is_preserved() -> None:
    committer = _committer()
    commit, remainder = committer._split_committable_blocks(
        "1. first\n\n2. second\n\nAfter the list.\n\ntail"
    )
    # The whole loose list commits as one prefix (never split mid-list), so its
    # numbering is never restarted.
    assert commit == "1. first\n\n2. second\n\nAfter the list.\n\n"
    assert remainder == "tail"


def test_split_keeps_streaming_list_live_until_a_non_list_block() -> None:
    committer = _committer()
    # A list still being streamed (no following non-list block) stays live so a
    # later item is not renumbered.
    assert committer._split_committable_blocks("1. a\n\n2. b\n\n") == (
        "",
        "1. a\n\n2. b\n\n",
    )


def test_assistant_paragraph_commits_before_flush() -> None:
    committer = _committer()
    committer.handle_event(AssistantEvent(content="First paragraph.\n\nSecond "))
    # The completed first paragraph is queued without waiting for a flush.
    assert committer.has_pending is True
    text = _drain_text(committer)
    assert "First paragraph." in text
    assert "Second" not in text  # trailing partial stays live
    # The trailing text flushes at the boundary.
    committer.flush()
    assert "Second" in _drain_text(committer)


def test_reasoning_multiparagraph_emits_one_thinking_header() -> None:
    committer = _committer()
    committer.handle_event(ReasoningEvent(content="step one.\n\nstep two.\n\n"))
    committer.flush()
    text = _drain_text(committer)
    assert text.count("Thinking") == 1
    assert "step one." in text
    assert "step two." in text


def test_reasoning_renders_markdown_structure() -> None:
    committer = _committer()
    committer.handle_event(
        ReasoningEvent(content="Plan:\n\n- first option\n- second option\n\n")
    )
    committer.flush()
    text = _drain_text(committer)
    # Markdown list markers are rendered (bulleted), not left as raw "- ".
    assert "first option" in text
    assert "second option" in text
    assert "•" in text


def test_tall_block_renders_every_line_without_clipping() -> None:
    committer = _committer()
    paragraphs = "\n\n".join(f"Paragraph number {i}." for i in range(40))
    committer.handle_event(AssistantEvent(content=paragraphs))
    committer.flush()
    text = _drain_text(committer)
    # A block far taller than any terminal still commits every line.
    for i in range(40):
        assert f"Paragraph number {i}." in text


def test_golden_session_orders_and_coalesces_transcript() -> None:
    """Replay a full turn: a local prompt widget, reasoning, a tool call/result,
    streamed assistant output across chunks, and the end-of-turn flush. The
    committed scrollback must be ordered and free of duplicated streamed text.
    """
    committer = _committer()

    # Local prompt arrives as a widget via _mount_and_scroll.
    assert committer.render_widget(UserMessage("refactor the parser")) is True

    # Agent events arrive via handle_event.
    committer.handle_event(ReasoningEvent(content="I should read the file first."))
    committer.handle_event(
        ToolCallEvent(
            tool_name="fake",
            tool_class=_FakeTool,
            args=_Args(path="parser.py"),
            tool_call_id="t1",
        )
    )
    committer.handle_event(
        ToolResultEvent(
            tool_name="fake", tool_class=_FakeTool, result=_Result(), tool_call_id="t1"
        )
    )
    # Assistant streams in three chunks.
    committer.handle_event(AssistantEvent(content="Done. "))
    committer.handle_event(AssistantEvent(content="The parser "))
    committer.handle_event(AssistantEvent(content="now handles edge cases."))
    # End of turn.
    from vibe.core.types import WaitingForInputEvent

    committer.handle_event(WaitingForInputEvent(task_id="x"))

    text = _drain_text(committer)

    # Streamed assistant text is coalesced into one block (no duplication).
    assert text.count("now handles edge cases") == 1
    assert "Done. The parser now handles edge cases." in text

    # Ordering: prompt -> reasoning -> tool result -> assistant.
    positions = [
        text.index("refactor the parser"),
        text.index("read the file first"),
        text.index("Reading parser.py"),
        text.index("Done. The parser"),
    ]
    assert positions == sorted(positions)
    assert committer.has_pending is False
