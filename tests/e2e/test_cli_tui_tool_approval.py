from __future__ import annotations

from pathlib import Path

import pexpect
import pyte
import pytest

from tests.e2e.common import (
    SpawnedVibeProcessFixture,
    send_ctrl_c_until_quit_confirmation,
    strip_ansi,
    wait_for_main_screen,
    wait_for_rendered_text,
    wait_for_request_count,
)
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer

_ALT_SCREEN = "\x1b[?1049h"
_PTY_COLUMNS = 120
_PTY_ROWS = 36
PREDICTABLE_OUTPUT = "__E2E_BASH_OK__"
TOOL_ARGUMENTS = f'{{"command":"printf \\"{PREDICTABLE_OUTPUT}\\\\n\\""}}'


def _terminal_lines(raw: str) -> list[str]:
    screen = pyte.HistoryScreen(_PTY_COLUMNS, _PTY_ROWS, history=5000)
    stream = pyte.ByteStream(screen)
    stream.feed(raw.encode("utf-8", "ignore"))

    def render_row(row) -> str:
        return "".join(row[x].data for x in range(_PTY_COLUMNS)).rstrip()

    return [
        *(render_row(row) for row in screen.history.top),
        *(screen.display[y].rstrip() for y in range(_PTY_ROWS)),
    ]


def _tool_call_factory(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index == 0:
        return [
            StreamingMockServer.build_chunk(
                created=1,
                delta=StreamingMockServer.build_tool_call_delta(
                    call_id="call_bash_1", tool_name="bash", arguments=TOOL_ARGUMENTS
                ),
                finish_reason=None,
            ),
            StreamingMockServer.build_chunk(
                created=2,
                delta={},
                finish_reason="tool_calls",
                usage={"prompt_tokens": 3, "completion_tokens": 4},
            ),
        ]

    return [
        StreamingMockServer.build_chunk(
            created=3,
            delta={
                "role": "assistant",
                "content": f"The string {PREDICTABLE_OUTPUT} has been printed successfully.",
            },
            finish_reason=None,
        ),
        StreamingMockServer.build_chunk(
            created=4, delta={"content": PREDICTABLE_OUTPUT}, finish_reason=None
        ),
        StreamingMockServer.build_chunk(
            created=5,
            delta={},
            finish_reason="stop",
            usage={"prompt_tokens": 3, "completion_tokens": 4},
        ),
    ]


@pytest.mark.timeout(25)
@pytest.mark.parametrize(
    "streaming_mock_server",
    [pytest.param(_tool_call_factory, id="tool-call-stream")],
    indirect=True,
)
def test_spawn_cli_asks_bash_permission_and_shows_tool_output_after_approval(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    spawned_vibe_process: SpawnedVibeProcessFixture,
) -> None:
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        wait_for_main_screen(child, timeout=15)
        child.send("Run a shell command")
        child.send("\r")

        wait_for_request_count(
            lambda: len(streaming_mock_server.requests),
            expected_count=1,
            timeout=10,
            child=child,
        )
        wait_for_rendered_text(
            child, captured, needle="Permission for the bash tool", timeout=10
        )
        child.send("y")
        child.send("\r")
        wait_for_rendered_text(child, captured, needle=PREDICTABLE_OUTPUT, timeout=10)

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)

    raw = captured.getvalue()
    rendered = strip_ansi(raw)
    terminal_lines = _terminal_lines(raw)
    assert _ALT_SCREEN not in raw
    assert "Approved bash" in rendered
    assert PREDICTABLE_OUTPUT in rendered
    assert [line for line in terminal_lines if "Approved bash" in line] == [
        "✓ Approved bash"
    ]
    assert [line for line in terminal_lines if PREDICTABLE_OUTPUT in line] == [
        f'✓ bash: printf "{PREDICTABLE_OUTPUT}\\n"',
        PREDICTABLE_OUTPUT,
    ]
