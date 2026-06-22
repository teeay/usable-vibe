"""Rich result-body renderers for native terminal-scroll mode.

Where the full-screen UI renders tool result bodies as Textual widgets
(``get_result_widget`` in ``tool_widgets.py``), native scroll commits the same
semantic inputs as Rich renderables straight into the host terminal's
scrollback. These renderers build from the typed result models
(:class:`BashResult`, :class:`EditResult`, :class:`AskUserQuestionResult`),
never by scraping a completed widget, so they are the durable rendering
architecture for tool output. Disposable agent tool output (bash/read/grep) is
shortened by default for terminal readability; manual ``!`` bash and
work-product/structured tools keep full result bodies. The renderers are pure
and Rich-only -- no Textual, no app state -- so the terminal output can be
unit-tested directly, mirroring ``inline_inject.py``.

Every tool with a dedicated full-screen result widget (``RESULT_WIDGETS`` in
``tool_widgets.py``) is covered here: shortened agent bash output, full manual
``!`` bash output, full edit diffs, full ask_user_question answers, full
write_file content, shortened read content, shortened grep matches, and full
todo lists. :func:`render_result_body` returns ``None`` for any other tool --
the builtins rendered by the generic ``ToolResultWidget`` (``webfetch``,
``websearch``, ``task``, ``skill``, ``exit_plan_mode``) and any future or
non-builtin tool -- so the committer keeps its ``format_result_display`` summary
line. Those are intentionally summary-only: their bodies are large model-facing
reference blobs the legacy UI hides by default (see ``private/ui-map.md``).
"""

from __future__ import annotations

import difflib
from pathlib import Path
import re

from pydantic import BaseModel
from rich.console import Group, RenderableType
from rich.syntax import Syntax
from rich.text import Text

from vibe.core.tools.builtins.ask_user_question import AskUserQuestionResult
from vibe.core.tools.builtins.bash import BashResult
from vibe.core.tools.builtins.edit import EditResult
from vibe.core.tools.builtins.grep import GrepResult
from vibe.core.tools.builtins.read import ReadResult
from vibe.core.tools.builtins.todo import TodoResult
from vibe.core.tools.builtins.write_file import WriteFileResult

# A unified-diff hunk header, e.g. ``@@ -1,3 +1,4 @@``.
_HUNK_HEADER_RE = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
# The model-facing ``   12→`` line-number prefix added to read output.
_LINE_NUMBER_PREFIX_RE = re.compile(r"^ *\d+→")
# Todo statuses in display order, with their checkbox icons.
_TODO_STATUS_ORDER = ("in_progress", "pending", "completed", "cancelled")
_TODO_STATUS_ICONS = {
    "pending": "☐",
    "in_progress": "☐",
    "completed": "☑",
    "cancelled": "☒",
}


def render_result_body(
    tool_name: str,
    result: BaseModel | None,
    *,
    dark: bool,
    ansi: bool,
    shorten: bool = True,
    head_lines: int = 3,
    tail_lines: int = 3,
) -> RenderableType | None:
    """Render a durable Rich result body for a tool, or ``None`` if unhandled.

    The committer appends the returned block under the result header and drops
    its generic summary line. ``None`` means the tool has no native body
    renderer, so the committer keeps the summary-only behavior.
    """
    body: RenderableType | None
    match (tool_name, result):
        case ("bash", BashResult()):
            body = _render_bash_result(
                result, shorten=shorten, head_lines=head_lines, tail_lines=tail_lines
            )
        case ("edit", EditResult()):
            body = _render_edit_result(result, dark=dark, ansi=ansi)
        case ("ask_user_question", AskUserQuestionResult()):
            body = _render_ask_user_question_result(result)
        case ("write_file", WriteFileResult()):
            body = _render_write_file_result(result, dark=dark, ansi=ansi)
        case ("read", ReadResult()):
            body = _render_read_result(
                result,
                dark=dark,
                ansi=ansi,
                shorten=shorten,
                head_lines=head_lines,
                tail_lines=tail_lines,
            )
        case ("grep", GrepResult()):
            body = _render_grep_result(
                result, shorten=shorten, head_lines=head_lines, tail_lines=tail_lines
            )
        case ("todo", TodoResult()):
            body = _render_todo_result(result)
        case _:
            body = None
    return body


def render_manual_bash_body(
    command: str, output: str, exit_code: int, *, interrupted: bool = False
) -> RenderableType:
    """Render a durable Rich block for a manual ``!`` / queued bash command.

    Built from raw subprocess data (command, combined output, exit/interrupt
    state), never by scraping ``BashOutputMessage``. Mirrors the widget's
    ``$ command`` prompt line followed by the captured output. Timeout and
    generic-failure reasons are carried by the separate ``ErrorMessage`` the
    handler commits, so only the interrupted marker is added here.
    """
    success = exit_code == 0 and not interrupted
    header = Text.assemble((f"$ {command}", "green" if success else "red"))
    if not success and not interrupted:
        header.append(f"  (exit {exit_code})", style="dim red")
    rows: list[RenderableType] = [header]
    body = output.strip("\n")
    rows.append(Text(body) if body else Text("(no output)", style="dim"))
    if interrupted:
        rows.append(Text("⚠ interrupted by user", style="yellow"))
    return Group(*rows)


def _render_bash_result(
    result: BashResult, *, shorten: bool, head_lines: int, tail_lines: int
) -> RenderableType:
    parts: list[str] = []
    if result.stdout:
        parts.append(result.stdout.strip("\n"))
    if result.stderr:
        parts.append(result.stderr.strip("\n"))
    output = "\n".join(part for part in parts if part)
    if not output:
        return Text("(no content)", style="dim")
    if shorten:
        output = shorten_text_middle(
            output, head_lines=head_lines, tail_lines=tail_lines
        )
    return Text(output)


def shorten_text_middle(text: str, *, head_lines: int, tail_lines: int) -> str:
    """Return ``text`` shortened to a head/tail preview with an omitted marker."""
    head_lines = max(0, head_lines)
    tail_lines = max(0, tail_lines)
    lines = text.splitlines()
    kept = head_lines + tail_lines
    if not lines or len(lines) <= kept:
        return text

    omitted = len(lines) - kept
    marker = f"... {omitted} {'line' if omitted == 1 else 'lines'} omitted ..."
    preview: list[str] = []
    if head_lines:
        preview.extend(lines[:head_lines])
    preview.append(marker)
    if tail_lines:
        preview.extend(lines[-tail_lines:])
    return "\n".join(preview)


def _render_ask_user_question_result(result: AskUserQuestionResult) -> RenderableType:
    if result.cancelled:
        return Text("User cancelled", style="yellow")
    rows: list[RenderableType] = []
    multi = len(result.answers) > 1
    for answer in result.answers:
        if multi:
            rows.append(Text(answer.question, style="dim"))
        prefix = "(Other) " if answer.is_other else ""
        rows.append(Text(f"{prefix}{answer.answer}", style="cyan"))
    if not rows:
        return Text("")
    return Group(*rows) if len(rows) > 1 else rows[0]


def _render_write_file_result(
    result: WriteFileResult, *, dark: bool, ansi: bool
) -> RenderableType:
    content = result.content.strip("\n")
    if not content:
        return Text("(no content)", style="dim")
    return _highlighted_block(
        content, _language_for_path(result.path), dark=dark, ansi=ansi
    )


def _render_read_result(
    result: ReadResult,
    *,
    dark: bool,
    ansi: bool,
    shorten: bool,
    head_lines: int,
    tail_lines: int,
) -> RenderableType:
    content = _strip_line_numbers(result.content).strip("\n")
    if not content:
        return Text("(no content)", style="dim")
    if shorten:
        content = shorten_text_middle(
            content, head_lines=head_lines, tail_lines=tail_lines
        )
    return _highlighted_block(
        content, _language_for_path(result.file_path), dark=dark, ansi=ansi
    )


def _render_grep_result(
    result: GrepResult, *, shorten: bool, head_lines: int, tail_lines: int
) -> RenderableType:
    matches = result.matches.strip("\n")
    if not matches:
        return Text("(no matches)", style="dim")
    if shorten:
        matches = shorten_text_middle(
            matches, head_lines=head_lines, tail_lines=tail_lines
        )
    return Text(matches)


def _render_todo_result(result: TodoResult) -> RenderableType:
    if not result.todos:
        return Text("No todos", style="dim")
    rows: list[RenderableType] = []
    for status in _TODO_STATUS_ORDER:
        icon = _TODO_STATUS_ICONS[status]
        for todo in result.todos:
            todo_status = (
                todo.status.value if hasattr(todo.status, "value") else str(todo.status)
            )
            if todo_status == status:
                rows.append(Text(f"{icon} {todo.content}"))
    return Group(*rows) if len(rows) > 1 else rows[0]


def _highlighted_block(
    content: str, language: str, *, dark: bool, ansi: bool
) -> RenderableType:
    return Syntax(
        content,
        language,
        theme=_syntax_theme(dark=dark, ansi=ansi),
        background_color="default",
        word_wrap=True,
    )


def _strip_line_numbers(content: str) -> str:
    return "\n".join(
        _LINE_NUMBER_PREFIX_RE.sub("", line) for line in content.split("\n")
    )


def _render_edit_result(
    result: EditResult, *, dark: bool, ansi: bool
) -> RenderableType:
    rows = _diff_rows(
        result.old_string,
        result.new_string,
        _language_for_path(result.file),
        result.ui_start_lines,
        dark=dark,
        ansi=ansi,
    )
    return Group(*rows) if len(rows) != 1 else rows[0]


def _diff_rows(
    old_string: str,
    new_string: str,
    language: str,
    start_lines: list[int] | None,
    *,
    dark: bool,
    ansi: bool,
) -> list[RenderableType]:
    syntax = Syntax("", language, theme=_syntax_theme(dark=dark, ansi=ansi))
    diff_lines = list(
        difflib.unified_diff(
            old_string.strip("\n").split("\n"),
            new_string.strip("\n").split("\n"),
            lineterm="",
            n=2,
        )
    )[2:]  # drop the ``--- / +++`` file headers; the gutter carries position.

    if not start_lines:
        return _diff_occurrence_rows(diff_lines, None, syntax, ansi=ansi)

    rows: list[RenderableType] = []
    for index, start_line in enumerate(start_lines):
        if index:
            rows.append(Text("⋯", style="dim"))  # gap between occurrences
        rows.extend(_diff_occurrence_rows(diff_lines, start_line, syntax, ansi=ansi))
    return rows


def _diff_occurrence_rows(
    diff_lines: list[str], start_line: int | None, syntax: Syntax, *, ansi: bool
) -> list[RenderableType]:
    offset = (start_line - 1) if start_line else 0
    old_lineno = new_lineno = 0  # overwritten by the first @@ header
    rows: list[RenderableType] = []
    first_hunk = True

    for line in diff_lines:
        prefix_char = line[0]
        code = line[1:]

        if prefix_char == "@":
            if not first_hunk:
                rows.append(Text("⋯", style="dim"))  # gap between hunks
            first_hunk = False
            if match := _HUNK_HEADER_RE.match(line):
                old_lineno = int(match.group(1)) + offset
                new_lineno = int(match.group(2)) + offset
            continue

        if prefix_char == "-":
            lineno = old_lineno
            old_lineno += 1
        elif prefix_char == "+":
            lineno = new_lineno
            new_lineno += 1
        else:
            lineno = new_lineno
            old_lineno += 1
            new_lineno += 1

        rows.append(
            _diff_row(
                code, prefix_char, lineno if start_line else None, syntax, ansi=ansi
            )
        )

    return rows or [Text("")]


def _diff_row(
    code: str, prefix_char: str, lineno: int | None, syntax: Syntax, *, ansi: bool
) -> RenderableType:
    body = syntax.highlight(code)
    body.rstrip()  # ``highlight`` appends a trailing newline; drop it in place.

    if prefix_char == "-":
        sign_style, lineno_style = "bold red", "dim red"
        if ansi:
            body.stylize("dim")  # ANSI themes lack row backgrounds; dim the body.
    elif prefix_char == "+":
        sign_style, lineno_style = "green", "dim green"
    else:
        sign_style, lineno_style = "dim", "dim"

    lineno_str = f"{lineno:>4} " if lineno is not None else ""
    return (
        Text.assemble((lineno_str, lineno_style), (f"{prefix_char} ", sign_style))
        + body
    )


def _syntax_theme(*, dark: bool, ansi: bool) -> str:
    if ansi:
        return "ansi_dark" if dark else "ansi_light"
    return "monokai" if dark else "default"


def _language_for_path(file_path: str) -> str:
    return Path(file_path).suffix.lstrip(".") or "text"
