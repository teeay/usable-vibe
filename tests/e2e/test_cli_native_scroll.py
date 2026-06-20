from __future__ import annotations

import json
import time

import pexpect
import pytest

from tests.e2e.common import strip_ansi, wait_for_rendered_text, wait_for_request_count
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer

_ALT_SCREEN = "\x1b[?1049h"
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
        # The live input region is present.
        assert "> " in rendered
        # The chat scroll area and its full animated banner are hidden: transcript
        # ownership has moved out of the internal scroll. Instead, the durable
        # transcript opens with the compact native startup header (version, model,
        # cwd, /help) committed to scrollback — #14.
        assert "Usable Vibe v" in rendered
        assert "/help" in rendered

        child.sendcontrol("d")
        _pump(child, 3.0)

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

        child.sendcontrol("d")
        _pump(child, 3.0)

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
