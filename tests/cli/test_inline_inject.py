"""Terminal-level tests for the pure inline-injection helpers.

These drive a `pyte` terminal emulator directly (no Textual) to verify the
escape-sequence math used by `VibeApp._display`: committed lines reach native
scrollback while the live region stays pinned to the bottom, and a region that
launched mid-screen is anchored to the bottom without losing content above it.
"""

from __future__ import annotations

import pyte

from vibe.cli.textual_ui.inline_inject import (
    build_bottom_anchor,
    build_commit_injection,
)

_REGION = ["[status: idle]", "> _"]


def _screen(cols: int, rows: int) -> tuple[pyte.HistoryScreen, pyte.ByteStream]:
    screen = pyte.HistoryScreen(cols, rows, history=500)
    return screen, pyte.ByteStream(screen)


def _visible(screen: pyte.HistoryScreen, rows: int) -> list[str]:
    return [screen.display[y].rstrip() for y in range(rows)]


def _scrollback(screen: pyte.HistoryScreen, cols: int) -> list[str]:
    return [
        "".join(row[x].data for x in range(cols)).rstrip() for row in screen.history.top
    ]


def _render_region(stream: pyte.ByteStream) -> None:
    """Render the region and park the cursor back at its top-left (0, 0)."""
    stream.feed("\r\n".join(_REGION).encode())
    stream.feed(f"\x1b[{len(_REGION) - 1}A\r".encode())


def test_build_commit_injection_structure() -> None:
    seq = build_commit_injection(["alpha", "beta"], (3, 1))
    # Erases the region downward and writes each completed line terminated by
    # CRLF so it scrolls into scrollback.
    assert "\x1b[0J" in seq
    assert "alpha\r\n" in seq
    assert "beta\r\n" in seq
    assert seq.index("alpha\r\n") < seq.index("beta\r\n")


def test_build_bottom_anchor_noop_when_region_already_at_bottom() -> None:
    assert (
        build_bottom_anchor(
            region_top=4, region_height=2, terminal_height=6, cursor_offset=(0, 0)
        )
        is None
    )


def test_build_bottom_anchor_noop_when_region_taller_than_terminal() -> None:
    assert (
        build_bottom_anchor(
            region_top=0, region_height=8, terminal_height=6, cursor_offset=(0, 0)
        )
        is None
    )


def test_anchor_pushes_top_region_to_bottom() -> None:
    cols, rows = 24, 6
    screen, stream = _screen(cols, rows)
    # Region launched at the top of the screen (rows 0-1), blank below.
    _render_region(stream)

    seq = build_bottom_anchor(
        region_top=0,
        region_height=len(_REGION),
        terminal_height=rows,
        cursor_offset=(0, 0),
    )
    assert seq is not None
    stream.feed(seq.encode())
    _render_region(stream)  # repaint where the cursor now sits

    visible = _visible(screen, rows)
    assert visible[-len(_REGION) :] == _REGION
    assert visible[: rows - len(_REGION)] == [""] * (rows - len(_REGION))
    assert _scrollback(screen, cols) == []  # nothing scrolled away


def test_anchor_preserves_content_above_the_region() -> None:
    cols, rows = 24, 6
    screen, stream = _screen(cols, rows)
    # Existing shell output on rows 0-1, region mid-screen at rows 2-3.
    stream.feed(b"old line one\r\nold line two\r\n")
    _render_region(stream)

    seq = build_bottom_anchor(
        region_top=2,
        region_height=len(_REGION),
        terminal_height=rows,
        cursor_offset=(0, 0),
    )
    assert seq is not None
    stream.feed(seq.encode())
    _render_region(stream)

    visible = _visible(screen, rows)
    assert visible[0] == "old line one"
    assert visible[1] == "old line two"
    assert visible[-len(_REGION) :] == _REGION


def test_anchor_then_commits_stream_into_scrollback() -> None:
    cols, rows = 24, 6
    screen, stream = _screen(cols, rows)
    _render_region(stream)  # mid-screen launch at the top

    seq = build_bottom_anchor(
        region_top=0,
        region_height=len(_REGION),
        terminal_height=rows,
        cursor_offset=(0, 0),
    )
    assert seq is not None
    stream.feed(seq.encode())
    _render_region(stream)

    for index in range(8):
        stream.feed(build_commit_injection([f"committed {index}"], (0, 0)).encode())
        _render_region(stream)  # the app's super()._display repaints the region

    visible = _visible(screen, rows)
    scrollback = _scrollback(screen, cols)
    # The region stays pinned to the bottom while completed lines scroll away
    # into native scrollback (oldest first).
    assert visible[-len(_REGION) :] == _REGION
    committed = [line for line in scrollback if line.startswith("committed ")]
    assert "committed 0" in committed
    assert len(committed) >= 8 - rows  # more commits than the screen can hold
    # Committed lines are intact and in order (no region remnants appended).
    assert committed == [f"committed {i}" for i in range(len(committed))]
