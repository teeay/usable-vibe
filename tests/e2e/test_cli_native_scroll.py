from __future__ import annotations

from collections.abc import Callable
import json
import os
import re
import time

import pexpect
import pyte
import pytest

from tests import TESTS_ROOT
from tests.cli.terminal_loop import TerminalLoop, strip_ansi as strip_terminal_ansi
from tests.e2e.common import strip_ansi, wait_for_rendered_text, wait_for_request_count
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer

_ALT_SCREEN = "\x1b[?1049h"
_CLEAR_SCREEN = "\x1b[2J"
_DISABLE_AUTOWRAP = "\x1b[?7l"
_ENABLE_AUTOWRAP = "\x1b[?7h"
_PTY_COLUMNS = 120
_PTY_ROWS = 36
_MANUAL_BASH_OUTPUT = "__NATIVE_MANUAL_BASH_OK__"
_QUEUED_REPLY = "NATIVE QUEUED REPLY"


def _pump(child: pexpect.spawn, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            child.read_nonblocking(4096, timeout=0.3)
        except pexpect.TIMEOUT:
            pass
        except pexpect.EOF:
            break


def _assert_native_terminal_contract(raw: str) -> None:
    assert _ALT_SCREEN not in raw


def _terminal_lines(
    raw: str, *, columns: int = _PTY_COLUMNS, rows: int = _PTY_ROWS
) -> list[str]:
    screen = pyte.HistoryScreen(columns, rows, history=5000)
    stream = pyte.ByteStream(screen)
    stream.feed(raw.encode("utf-8", "ignore"))
    return _screen_lines(screen, columns=columns, rows=rows)


def _screen_lines(screen: pyte.HistoryScreen, *, columns: int, rows: int) -> list[str]:
    def render_row(row) -> str:
        return "".join(row[x].data for x in range(columns)).rstrip()

    return [
        *(render_row(row) for row in screen.history.top),
        *(screen.display[y].rstrip() for y in range(rows)),
    ]


def _terminal_loop_text(loop: TerminalLoop) -> str:
    return "\n".join(strip_terminal_ansi(line) for line in loop.all_lines())


def _wait_for_terminal_text(loop: TerminalLoop, needle: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        loop.pump(timeout=0.1, idle_reads=1)
        if needle in _terminal_loop_text(loop):
            return
    rendered_tail = _terminal_loop_text(loop)[-1200:]
    raise AssertionError(
        f"Timed out waiting for rendered text: {needle!r}\n\nRendered tail:\n{rendered_tail}"
    )


def _spawn_terminal_loop(e2e_workdir) -> tuple[pexpect.spawn, TerminalLoop]:
    child = pexpect.spawn(
        "uv",
        ["run", "uvibe", "--workdir", str(e2e_workdir)],
        cwd=str(TESTS_ROOT.parent),
        env=os.environ.copy(),
        encoding=None,
        timeout=30,
        dimensions=(_PTY_ROWS, _PTY_COLUMNS),
    )
    return child, TerminalLoop(child, rows=_PTY_ROWS, cols=_PTY_COLUMNS)


def _wait_for_backend_requests(
    loop: TerminalLoop,
    request_count_getter: Callable[[], int],
    *,
    expected_count: int,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if request_count_getter() >= expected_count:
            return
        loop.pump(timeout=0.1, idle_reads=1)
    raise AssertionError(f"Timed out waiting for {expected_count} backend request(s).")


def _slow_queue_factory(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index == 0:
        chunks = [
            StreamingMockServer.build_chunk(
                created=i,
                delta={"role": "assistant", "content": f"first-stream-{i}\n"}
                if i == 0
                else {"content": f"first-stream-{i}\n"},
                finish_reason=None,
            )
            for i in range(200)
        ]
        chunks.append(
            StreamingMockServer.build_chunk(
                created=100,
                delta={},
                finish_reason="stop",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            )
        )
        return chunks

    return [
        StreamingMockServer.build_chunk(
            created=101,
            delta={"role": "assistant", "content": _QUEUED_REPLY},
            finish_reason=None,
        ),
        StreamingMockServer.build_chunk(
            created=102,
            delta={},
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        ),
    ]


def _question_factory(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index == 0:
        args = {
            "questions": [
                {
                    "question": "Which database?",
                    "header": "DB",
                    "options": [
                        {"label": "Postgres", "description": "durable"},
                        {"label": "SQLite", "description": "local"},
                    ],
                    "hide_other": True,
                }
            ]
        }
        return [
            StreamingMockServer.build_chunk(
                created=1,
                delta=StreamingMockServer.build_tool_call_delta(
                    call_id="call_question_1",
                    tool_name="ask_user_question",
                    arguments=json.dumps(args),
                ),
                finish_reason=None,
            ),
            StreamingMockServer.build_chunk(
                created=2,
                delta={},
                finish_reason="tool_calls",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
            ),
        ]

    return [
        StreamingMockServer.build_chunk(
            created=3,
            delta={"role": "assistant", "content": "Question answer recorded."},
            finish_reason=None,
        ),
        StreamingMockServer.build_chunk(
            created=4,
            delta={},
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        ),
    ]


def _interrupt_factory(
    _request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    chunks = [
        StreamingMockServer.build_chunk(
            created=i,
            delta={"role": "assistant", "content": f"interrupt-stream-{i}\n"}
            if i == 0
            else {"content": f"interrupt-stream-{i}\n"},
            finish_reason=None,
        )
        for i in range(800)
    ]
    chunks.append(
        StreamingMockServer.build_chunk(
            created=999,
            delta={},
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
        )
    )
    return chunks


@pytest.mark.timeout(60)
@pytest.mark.usefixtures("setup_e2e_env")
def test_native_scroll_shell_starts_as_bottom_region(
    e2e_workdir, spawned_vibe_process
) -> None:
    """In native-scroll mode VibeApp runs inline: no alternate screen, the chat
    transcript scroll area is gone, and the live control region (input prompt and
    workdir bar) renders at the bottom; Ctrl-C/Ctrl-D exits cleanly.
    """
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        _pump(child, 10.0)
        raw = captured.getvalue()
        rendered = strip_ansi(raw)

        assert child.isalive()
        # Never enters the alternate screen buffer.
        _assert_native_terminal_contract(raw)
        assert _DISABLE_AUTOWRAP in raw
        # The live input region is present.
        assert "> " in rendered
        # The chat scroll area and its full animated banner are hidden: transcript
        # ownership has moved out of the internal scroll. Instead, the durable
        # transcript opens with the compact native startup header (version, model,
        # cwd, /help) committed to scrollback — #14.
        assert "Usable Vibe v" in rendered
        assert "/help" in rendered

        child.sendcontrol("d")
        _pump(child, 0.5)
        child.sendcontrol("d")
        _pump(child, 3.0)

        assert _ENABLE_AUTOWRAP in captured.getvalue()

    assert not child.isalive()


@pytest.mark.timeout(60)
@pytest.mark.usefixtures("setup_e2e_env")
def test_native_scroll_commits_assistant_response_to_scrollback(
    e2e_workdir, spawned_vibe_process
) -> None:
    """A submitted prompt drives the real AgentLoop, and the streamed assistant
    response is committed into the host terminal output (native scrollback) by
    the single-writer _display injection -- not trapped in an internal widget.
    """
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        # Wait for the inline live region (input prompt) rather than the banner,
        # which is hidden in native mode.
        wait_for_rendered_text(child, captured, "> ", timeout=15)

        child.send("Greet")
        child.send("\r")

        # The mock backend streams "Hello from mock server"; it must reach the
        # terminal output as committed transcript text.
        wait_for_rendered_text(child, captured, "Hello from mock server", timeout=30)

        raw = captured.getvalue()
        _assert_native_terminal_contract(raw)
        assert re.search(r"\x1b\[1;\d+r", raw)
        assert "\x1b[r" in raw

        child.sendcontrol("d")
        _pump(child, 3.0)

    assert not child.isalive()


@pytest.mark.timeout(60)
@pytest.mark.usefixtures("setup_e2e_env")
def test_native_scroll_narrow_resize_preserves_committed_scrollback(
    e2e_workdir,
) -> None:
    child, loop = _spawn_terminal_loop(e2e_workdir)
    try:
        _wait_for_terminal_text(loop, ">", timeout=15)

        child.send(b"Greet")
        child.send(b"\r")
        _wait_for_terminal_text(loop, "Hello from mock server", timeout=30)

        before_resize = len(loop.raw)
        loop.resize(_PTY_ROWS, 60)
        loop.pump(timeout=0.1, idle_reads=20)

        raw = loop.raw.decode("utf-8", "replace")
        after_resize = raw[before_resize:]
        terminal_lines = [
            strip_terminal_ansi(line)
            for line in [*loop.scrollback_lines(), *loop.visible_lines()]
        ]
        terminal_text = "\n".join(terminal_lines)

        _assert_native_terminal_contract(raw)
        assert _CLEAR_SCREEN not in after_resize
        assert "Hello from mock server" in terminal_text
        hello_index = next(
            index
            for index, line in enumerate(terminal_lines)
            if "Hello from mock server" in line
        )
        assert not any(
            "Generating" in line for line in terminal_lines[hello_index + 1 : -4]
        )

        child.sendcontrol("d")
        loop.pump(timeout=0.1, idle_reads=20)
    finally:
        if child.isalive():
            child.terminate(force=True)
        if not child.closed:
            child.close()

    assert not child.isalive()


@pytest.mark.timeout(90)
@pytest.mark.parametrize("streaming_mock_server", [_slow_queue_factory], indirect=True)
@pytest.mark.usefixtures("setup_e2e_env")
def test_native_scroll_active_narrow_resize_does_not_capture_live_chrome(
    streaming_mock_server: StreamingMockServer, e2e_workdir
) -> None:
    child, loop = _spawn_terminal_loop(e2e_workdir)
    try:
        _wait_for_terminal_text(loop, ">", timeout=15)

        child.send(b"Start slow turn")
        child.send(b"\r")
        _wait_for_backend_requests(
            loop,
            lambda: len(streaming_mock_server.requests),
            expected_count=1,
            timeout=10,
        )

        before_resize = len(loop.raw)
        loop.resize(_PTY_ROWS, 60)
        loop.pump(timeout=0.1, idle_reads=30)

        raw = loop.raw.decode("utf-8", "replace")
        after_resize = raw[before_resize:]
        scrollback_lines = [
            strip_terminal_ansi(line) for line in loop.scrollback_lines()
        ]

        _assert_native_terminal_contract(raw)
        assert _CLEAR_SCREEN not in after_resize
        assert not any("Generating" in line for line in scrollback_lines)
        assert not any("─" in line for line in scrollback_lines)
        assert not any("[default]" in line for line in scrollback_lines)

        child.sendcontrol("c")
        loop.pump(timeout=0.1, idle_reads=20)
    finally:
        if child.isalive():
            child.terminate(force=True)
        if not child.closed:
            child.close()

    assert not child.isalive()


@pytest.mark.timeout(90)
@pytest.mark.parametrize("resize_columns", [_PTY_COLUMNS - 20, 60])
@pytest.mark.usefixtures("setup_e2e_env")
def test_native_scroll_typed_input_survives_narrow_resize(
    e2e_workdir, resize_columns: int
) -> None:
    child, loop = _spawn_terminal_loop(e2e_workdir)
    prompt = b"typed resize prompt stays editable"
    try:
        _wait_for_terminal_text(loop, ">", timeout=15)

        child.send(prompt)
        _wait_for_terminal_text(loop, "typed resize prompt", timeout=15)

        before_resize = len(loop.raw)
        loop.resize(_PTY_ROWS, resize_columns)
        loop.pump(timeout=0.1, idle_reads=20)

        raw = loop.raw.decode("utf-8", "replace")
        after_resize = raw[before_resize:]
        visible_text = "\n".join(
            strip_terminal_ansi(line) for line in loop.visible_lines()
        )
        scrollback_text = "\n".join(
            strip_terminal_ansi(line) for line in loop.scrollback_lines()
        )

        _assert_native_terminal_contract(raw)
        assert _CLEAR_SCREEN not in after_resize
        assert "typed resize prompt" in visible_text
        assert "typed resize prompt" not in scrollback_text

        child.sendcontrol("c")
        loop.pump(timeout=0.1, idle_reads=10)
        child.sendcontrol("d")
        loop.pump(timeout=0.1, idle_reads=20)
    finally:
        if child.isalive():
            child.terminate(force=True)
        if not child.closed:
            child.close()

    assert not child.isalive()


@pytest.mark.timeout(60)
@pytest.mark.usefixtures("setup_e2e_env")
def test_native_scroll_manual_bash_commits_output(
    e2e_workdir, spawned_vibe_process
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_rendered_text(child, captured, "> ", timeout=15)

        child.send(f"! printf '{_MANUAL_BASH_OUTPUT}\\n'")
        child.send("\r")

        wait_for_rendered_text(child, captured, _MANUAL_BASH_OUTPUT, timeout=15)

        raw = captured.getvalue()
        rendered = strip_ansi(raw)
        _assert_native_terminal_contract(raw)
        assert _MANUAL_BASH_OUTPUT in rendered
        assert rendered.count(_MANUAL_BASH_OUTPUT) <= 3

        child.sendcontrol("d")
        _pump(child, 3.0)

    assert not child.isalive()


@pytest.mark.timeout(60)
@pytest.mark.usefixtures("setup_e2e_env")
def test_native_scroll_manual_bash_keeps_long_output_full(
    e2e_workdir, spawned_vibe_process
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_rendered_text(child, captured, "> ", timeout=15)

        child.send(
            "! for i in 01 02 03 04 05 06 07 08 09 10; do "
            "printf '__NATIVE_LONG_BASH_LINE_%s__\\n' \"$i\"; done"
        )
        child.send("\r")

        wait_for_rendered_text(
            child, captured, "__NATIVE_LONG_BASH_LINE_10__", timeout=15
        )

        child.sendcontrol("d")
        _pump(child, 3.0)

    raw = captured.getvalue()
    terminal_text = "\n".join(_terminal_lines(raw))
    _assert_native_terminal_contract(raw)
    assert "__NATIVE_LONG_BASH_LINE_01__" in terminal_text
    assert "__NATIVE_LONG_BASH_LINE_03__" in terminal_text
    assert "__NATIVE_LONG_BASH_LINE_04__" in terminal_text
    assert "__NATIVE_LONG_BASH_LINE_07__" in terminal_text
    assert "__NATIVE_LONG_BASH_LINE_08__" in terminal_text
    assert "__NATIVE_LONG_BASH_LINE_10__" in terminal_text
    assert "lines omitted" not in terminal_text
    assert not child.isalive()


@pytest.mark.timeout(90)
@pytest.mark.parametrize("streaming_mock_server", [_slow_queue_factory], indirect=True)
@pytest.mark.usefixtures("setup_e2e_env")
def test_native_scroll_queued_prompt_drains_to_scrollback(
    streaming_mock_server: StreamingMockServer, e2e_workdir, spawned_vibe_process
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_rendered_text(child, captured, "> ", timeout=15)

        child.send("Start slow turn")
        child.send("\r")
        wait_for_request_count(
            lambda: len(streaming_mock_server.requests),
            expected_count=1,
            timeout=10,
            child=child,
        )

        child.send("Queued prompt")
        child.send("\r")
        wait_for_request_count(
            lambda: len(streaming_mock_server.requests),
            expected_count=2,
            timeout=30,
            child=child,
        )
        wait_for_rendered_text(child, captured, _QUEUED_REPLY, timeout=20)

        raw = captured.getvalue()
        rendered = strip_ansi(raw)
        _assert_native_terminal_contract(raw)
        assert "Queued prompt" in rendered
        assert _QUEUED_REPLY in rendered
        assert rendered.count(_QUEUED_REPLY) <= 3

        child.sendcontrol("d")
        _pump(child, 3.0)

    assert not child.isalive()


@pytest.mark.timeout(90)
@pytest.mark.parametrize("streaming_mock_server", [_question_factory], indirect=True)
@pytest.mark.usefixtures("setup_e2e_env")
def test_native_scroll_question_answer_commits_structured_result(
    streaming_mock_server: StreamingMockServer, e2e_workdir, spawned_vibe_process
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_rendered_text(child, captured, "> ", timeout=15)

        child.send("Ask me a question")
        child.send("\r")
        wait_for_request_count(
            lambda: len(streaming_mock_server.requests),
            expected_count=1,
            timeout=10,
            child=child,
        )
        wait_for_rendered_text(child, captured, "Which database?", timeout=15)

        _pump(child, 0.7)
        child.send("1")

        wait_for_request_count(
            lambda: len(streaming_mock_server.requests),
            expected_count=2,
            timeout=20,
            child=child,
        )
        wait_for_rendered_text(child, captured, "Question answer recorded.", timeout=20)

        raw = captured.getvalue()
        rendered = strip_ansi(raw)
        _assert_native_terminal_contract(raw)
        assert "Postgres" in rendered
        assert "Question answer recorded." in rendered

        child.sendcontrol("d")
        _pump(child, 3.0)

    assert not child.isalive()


@pytest.mark.timeout(90)
@pytest.mark.parametrize("streaming_mock_server", [_interrupt_factory], indirect=True)
@pytest.mark.usefixtures("setup_e2e_env")
def test_native_scroll_interrupt_restores_prompt_and_commits_marker(
    streaming_mock_server: StreamingMockServer, e2e_workdir, spawned_vibe_process
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_rendered_text(child, captured, "> ", timeout=15)

        child.send("Start interruptible turn")
        child.send("\r")
        wait_for_request_count(
            lambda: len(streaming_mock_server.requests),
            expected_count=1,
            timeout=10,
            child=child,
        )
        child.sendcontrol("c")
        wait_for_rendered_text(child, captured, "Interrupted", timeout=15)
        wait_for_rendered_text(child, captured, "> ", timeout=15)

        raw = captured.getvalue()
        rendered = strip_ansi(raw)
        _assert_native_terminal_contract(raw)
        assert "Interrupted" in rendered
        assert "> " in rendered

        child.sendcontrol("d")
        _pump(child, 3.0)

    assert not child.isalive()
