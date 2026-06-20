from __future__ import annotations

import os
from pathlib import Path
import sys

import pyte
import pytest

from tests.cli.terminal_loop import ink_commit_sequence, run_under_terminal

_SPIKE_APP = Path(__file__).parent / "_inline_spike_app.py"
_LIVE_SPIKE_APP = Path(__file__).parent / "_inline_live_spike_app.py"
_TRANSIENT_SPIKE_APP = Path(__file__).parent / "_inline_transient_spike_app.py"


def test_ink_commit_dance_pushes_lines_to_scrollback() -> None:
    """The single-writer commit sequence keeps a fixed live region pinned at the
    bottom while completed lines flow into native scrollback.
    """
    cols, rows = 30, 5
    screen = pyte.HistoryScreen(cols, rows, history=500)
    stream = pyte.ByteStream(screen)
    region = ("[status: idle]", "> _")

    stream.feed("\r\n".join(region).encode())
    stream.feed(f"\x1b[{len(region) - 1}A\r".encode())
    for index in range(8):
        stream.feed(ink_commit_sequence(f"committed line {index}", region).encode())

    visible = [screen.display[y].rstrip() for y in range(rows)]
    scrollback = [
        "".join(row[x].data for x in range(cols)).rstrip() for row in screen.history.top
    ]

    assert visible[-2:] == ["[status: idle]", "> _"]
    assert "committed line 0" in scrollback
    assert "committed line 4" in scrollback
    assert len(scrollback) >= rows


@pytest.mark.skipif(sys.platform == "win32", reason="PTY spike is Unix-only")
def test_textual_inline_commits_survive_in_scrollback() -> None:
    """A real Textual inline app, driven under a PTY with a terminal emulator in
    the loop, pushes more committed lines than the screen height into scrollback
    while the live region stays pinned and no alternate screen is used.
    """
    rows, cols, commits = 8, 40, 14
    env = {**os.environ, "SPIKE_COMMITS": str(commits), "TERM": "xterm-256color"}

    terminal = run_under_terminal(
        [sys.executable, str(_SPIKE_APP)], rows=rows, cols=cols, env=env
    )

    all_lines = terminal.all_lines()
    committed = [line for line in all_lines if line.startswith("committed line ")]

    assert not terminal.entered_alternate_screen
    # More committed lines than the screen can hold must have scrolled into history.
    scrollback = [
        line
        for line in terminal.scrollback_lines()
        if line.startswith("committed line ")
    ]
    assert len(scrollback) >= commits - rows, (
        f"expected scrollback to retain committed lines; "
        f"scrollback={terminal.scrollback_lines()} visible={terminal.visible_lines()}"
    )
    assert len({*committed}) == commits, f"missing committed lines: {committed}"


@pytest.mark.skipif(sys.platform == "win32", reason="PTY spike is Unix-only")
def test_committed_lines_inject_cleanly_while_region_repaints() -> None:
    """Lines injected from inside Textual's own frame production land in native
    scrollback intact and in order, even while Textual's compositor is actively
    repainting the live region. This is the Phase 3 integration mechanism.
    """
    rows, cols, commits = 8, 44, 14
    env = {**os.environ, "SPIKE_COMMITS": str(commits), "TERM": "xterm-256color"}

    terminal = run_under_terminal(
        [sys.executable, str(_LIVE_SPIKE_APP)], rows=rows, cols=cols, env=env
    )

    assert not terminal.entered_alternate_screen

    scrollback = [
        line
        for line in terminal.scrollback_lines()
        if line.startswith("committed line ")
    ]
    numbers = [int(line.split()[-1]) for line in scrollback]

    assert len(scrollback) >= commits - rows
    # Intact: no region-border remnants appended to short committed lines. This
    # is the decisive check — the remnant bug and any region/commit collision
    # both corrupt these lines.
    assert all(
        line == f"committed line {n}"
        for line, n in zip(scrollback, numbers, strict=True)
    )
    # In contiguous commit order.
    assert numbers == list(range(numbers[0], numbers[0] + len(numbers)))


@pytest.mark.skipif(sys.platform == "win32", reason="PTY spike is Unix-only")
def test_transient_live_surface_disappears_without_becoming_transcript() -> None:
    """A tall transient live surface (splash) is shown and then removed. It must
    leave no trace in native scrollback; the durable result committed through the
    injection path must actually land in native scrollback (history, not just the
    visible screen); and the live region (prompt) must be restored at the bottom
    after the surface is removed and the region redrawn.
    """
    rows, cols = 10, 40
    env = {**os.environ, "TERM": "xterm-256color"}

    terminal = run_under_terminal(
        [sys.executable, str(_TRANSIENT_SPIKE_APP)], rows=rows, cols=cols, env=env
    )

    assert not terminal.entered_alternate_screen

    scrollback = terminal.scrollback_lines()
    visible = terminal.visible_lines()

    # The transient splash never becomes durable scrollback transcript, and is
    # gone from the visible region after removal.
    assert all("SPLASH LINE" not in line for line in scrollback)
    assert all("SPLASH LINE" not in line for line in visible)

    # Durable finalization: enough result lines were committed to overflow the
    # screen, so the earliest ones must be in native scrollback (history), proving
    # they are durable terminal content rather than merely the live region.
    durable_in_scrollback = [
        line for line in scrollback if line.startswith("durable result line ")
    ]
    assert "durable result line 0" in durable_in_scrollback
    assert len(durable_in_scrollback) >= 14 - rows
    # Committed lines are intact (no region remnants appended to short lines).
    numbers = [int(line.rsplit(" ", 1)[-1]) for line in durable_in_scrollback]
    assert durable_in_scrollback == [f"durable result line {n}" for n in numbers]
    assert numbers == sorted(numbers)

    # Prompt restoration after the redraw: the live input region is present,
    # pinned to the bottom of the visible screen (not drifted up with blank space
    # below it), and the durable transcript flows above it -- never interleaved
    # below the live region.
    assert "> _" in visible, f"prompt not restored: {visible}"
    prompt_row = visible.index("> _")
    assert prompt_row >= rows - 4, f"live region drifted up: {visible}"
    assert not any(
        line.startswith("durable result line ") for line in visible[prompt_row:]
    )
