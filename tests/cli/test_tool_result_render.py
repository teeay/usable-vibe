from __future__ import annotations

from io import StringIO

from rich.console import Console, RenderableType

from vibe.cli.textual_ui.tool_result_render import (
    render_manual_bash_body,
    render_result_body,
)
from vibe.core.tools.builtins.ask_user_question import Answer, AskUserQuestionResult
from vibe.core.tools.builtins.bash import BashResult
from vibe.core.tools.builtins.edit import EditResult
from vibe.core.tools.builtins.grep import GrepResult
from vibe.core.tools.builtins.read import ReadResult
from vibe.core.tools.builtins.todo import TodoItem, TodoResult, TodoStatus
from vibe.core.tools.builtins.write_file import WriteFileResult


def _plain(renderable: RenderableType | None, width: int = 80) -> str:
    assert renderable is not None
    console = Console(width=width, file=StringIO(), color_system=None)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def _edit_result(old: str, new: str, *, start_line: int | None = None) -> EditResult:
    result = EditResult(file="a.py", message="Edited", old_string=old, new_string=new)
    if start_line is not None:
        result._ui_start_lines = [start_line]
    return result


def test_bash_body_includes_stdout_and_stderr() -> None:
    result = BashResult(
        command="ls", stdout="line-out\n", stderr="line-err\n", returncode=1
    )
    text = _plain(render_result_body("bash", result, dark=True, ansi=False))
    assert "line-out" in text
    assert "line-err" in text


def test_bash_empty_output_renders_no_content() -> None:
    result = BashResult(command="true", stdout="", stderr="", returncode=0)
    text = _plain(render_result_body("bash", result, dark=True, ansi=False))
    assert "(no content)" in text


def test_manual_bash_body_success() -> None:
    text = _plain(render_manual_bash_body("ls -a", "a\nb", 0))
    assert "$ ls -a" in text
    assert "a" in text and "b" in text
    assert "exit" not in text


def test_manual_bash_body_failure_shows_exit_code() -> None:
    text = _plain(render_manual_bash_body("false", "", 1))
    assert "$ false" in text
    assert "(exit 1)" in text
    assert "(no output)" in text


def test_manual_bash_body_interrupted_marks_state() -> None:
    text = _plain(render_manual_bash_body("sleep 10", "partial", 1, interrupted=True))
    assert "$ sleep 10" in text
    assert "partial" in text
    assert "interrupted" in text
    # Interrupted state is shown by the marker, not an exit-code suffix.
    assert "(exit" not in text


def test_edit_diff_marks_added_and_removed_lines() -> None:
    text = _plain(
        render_result_body(
            "edit", _edit_result("a\nb\nc", "a\nB\nc"), dark=True, ansi=False
        )
    )
    assert "- b" in text
    assert "+ B" in text
    assert "  a" in text  # context line preserved


def test_edit_diff_includes_line_numbers_when_located() -> None:
    text = _plain(
        render_result_body(
            "edit",
            _edit_result("a\nb\nc", "a\nB\nc", start_line=10),
            dark=True,
            ansi=False,
        )
    )
    assert "11 - b" in text
    assert "11 + B" in text


def test_edit_diff_marks_hunk_gap_between_separate_changes() -> None:
    old = "\n".join(str(i) for i in range(1, 21))
    new = old.replace("2", "TWO").replace("19", "NINETEEN")
    text = _plain(
        render_result_body("edit", _edit_result(old, new), dark=True, ansi=False)
    )
    assert "⋯" in text


def test_edit_diff_repeats_replace_all_occurrences() -> None:
    result = EditResult(file="a.py", message="Edited", old_string="x", new_string="y")
    result._ui_start_lines = [3, 9]
    text = _plain(render_result_body("edit", result, dark=True, ansi=False))
    assert "3 - x" in text
    assert "3 + y" in text
    assert "9 - x" in text
    assert "9 + y" in text
    assert "⋯" in text


def test_ask_single_answer_has_no_question_header() -> None:
    result = AskUserQuestionResult(
        answers=[Answer(question="Which db?", answer="Postgres")], cancelled=False
    )
    text = _plain(
        render_result_body("ask_user_question", result, dark=True, ansi=False)
    )
    assert "Postgres" in text
    assert "Which db?" not in text


def test_ask_multiple_answers_show_question_headers() -> None:
    result = AskUserQuestionResult(
        answers=[
            Answer(question="Which db?", answer="Postgres"),
            Answer(question="Which cache?", answer="Redis"),
        ],
        cancelled=False,
    )
    text = _plain(
        render_result_body("ask_user_question", result, dark=True, ansi=False)
    )
    assert "Which db?" in text
    assert "Postgres" in text
    assert "Which cache?" in text
    assert "Redis" in text


def test_ask_other_answer_is_prefixed() -> None:
    result = AskUserQuestionResult(
        answers=[Answer(question="Which db?", answer="SQLite", is_other=True)],
        cancelled=False,
    )
    text = _plain(
        render_result_body("ask_user_question", result, dark=True, ansi=False)
    )
    assert "(Other) SQLite" in text


def test_ask_cancelled_renders_cancel_notice() -> None:
    result = AskUserQuestionResult(answers=[], cancelled=True)
    text = _plain(
        render_result_body("ask_user_question", result, dark=True, ansi=False)
    )
    assert "User cancelled" in text


def test_write_file_body_includes_content() -> None:
    result = WriteFileResult(path="hello.py", bytes_written=11, content="print('hi')\n")
    text = _plain(render_result_body("write_file", result, dark=True, ansi=False))
    assert "print('hi')" in text


def test_write_file_empty_content_renders_no_content() -> None:
    result = WriteFileResult(path="empty.txt", bytes_written=0, content="")
    text = _plain(render_result_body("write_file", result, dark=True, ansi=False))
    assert "(no content)" in text


def test_read_body_strips_line_number_prefixes() -> None:
    result = ReadResult(
        file_path="a.py",
        content="   1→import os\n   2→print(os.getcwd())",
        num_lines=2,
        start_line=1,
    )
    text = _plain(render_result_body("read", result, dark=True, ansi=False))
    assert "import os" in text
    assert "print(os.getcwd())" in text
    assert "1→" not in text
    assert "2→" not in text


def test_read_empty_content_renders_no_content() -> None:
    result = ReadResult(file_path="a.py", content="", num_lines=0, start_line=1)
    text = _plain(render_result_body("read", result, dark=True, ansi=False))
    assert "(no content)" in text


def test_grep_body_includes_matches() -> None:
    result = GrepResult(
        matches="src/a.py:1:hit\nsrc/b.py:5:hit", match_count=2, was_truncated=False
    )
    text = _plain(render_result_body("grep", result, dark=True, ansi=False))
    assert "src/a.py:1:hit" in text
    assert "src/b.py:5:hit" in text


def test_grep_empty_matches_renders_no_matches() -> None:
    result = GrepResult(matches="", match_count=0, was_truncated=False)
    text = _plain(render_result_body("grep", result, dark=True, ansi=False))
    assert "(no matches)" in text


def test_todo_body_groups_by_status_with_icons() -> None:
    result = TodoResult(
        message="ok",
        total_count=3,
        todos=[
            TodoItem(id="1", content="done item", status=TodoStatus.COMPLETED),
            TodoItem(id="2", content="active item", status=TodoStatus.IN_PROGRESS),
            TodoItem(id="3", content="todo item", status=TodoStatus.PENDING),
        ],
    )
    text = _plain(render_result_body("todo", result, dark=True, ansi=False))
    # in_progress is grouped before pending before completed.
    assert text.index("active item") < text.index("todo item") < text.index("done item")
    assert "☑ done item" in text
    assert "☐ active item" in text
    assert "☐ todo item" in text


def test_todo_empty_renders_no_todos() -> None:
    result = TodoResult(message="ok", total_count=0, todos=[])
    text = _plain(render_result_body("todo", result, dark=True, ansi=False))
    assert "No todos" in text


def test_unknown_tool_has_no_body() -> None:
    result = BashResult(command="ls", stdout="x", stderr="", returncode=0)
    assert render_result_body("webfetch", result, dark=True, ansi=False) is None


def test_mismatched_result_type_has_no_body() -> None:
    result = BashResult(command="ls", stdout="x", stderr="", returncode=0)
    assert render_result_body("edit", result, dark=True, ansi=False) is None


def test_none_result_has_no_body() -> None:
    assert render_result_body("bash", None, dark=True, ansi=False) is None
