from __future__ import annotations

import time

import pexpect
import pyte
import pytest

from tests.e2e.common import strip_ansi
from tests.e2e.mock_server import StreamingMockServer

_PTY_COLUMNS = 120
_PTY_ROWS = 36
_LONG = (
    "Here are the recent commits:\n\n"
    "1. **First commit** — this is the body of the first commit and it "
    "explains what changed in some detail across a line.\n\n"
    "2. **Second commit** — body of the second commit with its own "
    "explanation that should be visible.\n\n"
    "3. **Third commit** — third body text that must also render.\n"
)


def _factory(index, payload):
    return [
        StreamingMockServer.build_chunk(
            created=1, delta={"role": "assistant", "content": _LONG}, finish_reason=None
        ),
        StreamingMockServer.build_chunk(
            created=2,
            delta={},
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        ),
    ]


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


def _pump(child: pexpect.spawn, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            child.read_nonblocking(4096, timeout=0.3)
        except pexpect.TIMEOUT:
            pass
        except pexpect.EOF:
            break


@pytest.mark.timeout(60)
@pytest.mark.parametrize("streaming_mock_server", [_factory], indirect=True)
@pytest.mark.usefixtures("setup_e2e_env")
def test_native_scroll_commits_full_multiparagraph_answer(
    e2e_workdir, spawned_vibe_process
) -> None:
    """A multi-paragraph assistant answer reaches native scrollback in full (not
    clipped to one screen), and the prompt is not committed twice.
    """
    with spawned_vibe_process(e2e_workdir) as (child, captured):
        _pump(child, 6.0)
        child.send("Explain the recent commits.")
        child.send("\r")
        _pump(child, 10.0)
        raw = captured.getvalue()
        rendered = strip_ansi(raw)
        child.sendcontrol("d")
        _pump(child, 2.0)

    # Every paragraph body made it into scrollback (no per-screen clipping).
    assert "body of the first commit" in rendered
    assert "body of the second commit" in rendered
    assert "third body text" in rendered
    # Raw PTY output includes live input repaint frames before submit. The
    # terminal model is the settled native-scroll state; there the prompt should
    # exist only as the durable committed prompt, not as repeated live echoes.
    terminal_text = "\n".join(_terminal_lines(raw))
    assert terminal_text.count("Explain the recent commits.") == 1
