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
    build_bottom_reset,
    build_commit_injection,
    build_inline_terminal_reset,
    build_inline_terminal_setup,
    build_relocated_anchor,
    build_resize_sweep,
    build_scroll_region_commit_injection,
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


def _write_row(stream: pyte.ByteStream, row: int, text: str) -> None:
    stream.feed(f"\x1b[{row};1H\x1b[2K{text}".encode())


def test_build_commit_injection_structure() -> None:
    seq = build_commit_injection(["alpha", "beta"], (3, 1))
    # Erases the region downward and writes each completed line terminated by
    # CRLF so it scrolls into scrollback.
    assert "\x1b[0J" in seq
    assert "alpha\r\n" in seq
    assert "beta\r\n" in seq
    assert seq.index("alpha\r\n") < seq.index("beta\r\n")


def test_commit_injection_fallback_growth_preserves_transcript() -> None:
    cols, rows = 32, 6
    screen, stream = _screen(cols, rows)
    for row, text in enumerate(
        ["old 1", "old 2", "old 3", "old 4", "[status: live]", "> typed"], start=1
    ):
        _write_row(stream, row, text)

    # The live region grows from 2 to 5 rows (streaming bash on a short
    # terminal) in the same frame as a commit: the screen scrolls up by the
    # growth first, so committed transcript reaches history intact instead of
    # being erased by the fallback's ESC[0J.
    stream.feed(
        build_commit_injection(
            ["new A"],
            (2, 1),
            live_region_height=5,
            terminal_height=rows,
            previous_live_region_height=2,
        ).encode()
    )

    scrollback = _scrollback(screen, cols)
    assert scrollback[-3:] == ["old 1", "old 2", "old 3"]
    visible = _visible(screen, rows)
    assert visible[0] == "old 4"
    assert visible[1] == "new A"
    assert "[status: live]" not in visible


def test_build_scroll_region_commit_injection_structure() -> None:
    seq = build_scroll_region_commit_injection(
        ["alpha", "beta"], live_region_height=2, terminal_height=6
    )

    assert seq.startswith("\x1b[1;4r\x1b[4;1H")
    assert "\n\r\x1b[2Kalpha" in seq
    assert "\n\r\x1b[2Kbeta" in seq
    assert seq.index("alpha") < seq.index("beta")
    assert seq.endswith("\x1b[r\x1b[5;1H")


def test_scroll_region_commit_preserves_live_rows() -> None:
    cols, rows = 32, 6
    screen, stream = _screen(cols, rows)
    for row, text in enumerate(
        ["old 1", "old 2", "old 3", "old 4", "[status: live]", "> typed"], start=1
    ):
        _write_row(stream, row, text)

    stream.feed(
        build_scroll_region_commit_injection(
            ["new A", "new B"], live_region_height=2, terminal_height=rows
        ).encode()
    )

    visible = _visible(screen, rows)
    assert visible[:4] == ["old 3", "old 4", "new A", "new B"]
    assert visible[-2:] == ["[status: live]", "> typed"]


def test_scroll_region_commit_taller_than_region_keeps_every_line() -> None:
    cols, rows = 32, 6
    screen, stream = _screen(cols, rows)
    for row, text in enumerate(
        ["old 1", "old 2", "old 3", "old 4", "[status: live]", "> typed"], start=1
    ):
        _write_row(stream, row, text)

    committed = [f"line {index}" for index in range(1, 9)]
    stream.feed(
        build_scroll_region_commit_injection(
            committed, live_region_height=2, terminal_height=rows
        ).encode()
    )

    visible = _visible(screen, rows)
    assert visible[:4] == ["line 5", "line 6", "line 7", "line 8"]
    assert visible[-2:] == ["[status: live]", "> typed"]
    scrollback = _scrollback(screen, cols)
    assert scrollback[-8:] == [
        "old 1",
        "old 2",
        "old 3",
        "old 4",
        "line 1",
        "line 2",
        "line 3",
        "line 4",
    ]


def test_scroll_region_commit_makes_room_when_live_region_grows() -> None:
    cols, rows = 32, 8
    screen, stream = _screen(cols, rows)
    for row, text in enumerate(
        [
            "old 1",
            "old 2",
            "old 3",
            "old 4",
            "last committed",
            "tail committed",
            "[status: live]",
            "> typed",
        ],
        start=1,
    ):
        _write_row(stream, row, text)

    # Live region grows from 2 to 4 rows in the same frame as a commit: the
    # two transcript rows under the taller block must scroll up, not be
    # overwritten by the repaint.
    stream.feed(
        build_scroll_region_commit_injection(
            ["new A"],
            live_region_height=4,
            terminal_height=rows,
            previous_live_region_height=2,
        ).encode()
    )

    visible = _visible(screen, rows)
    assert visible[:4] == ["old 4", "last committed", "tail committed", "new A"]
    assert _scrollback(screen, cols)[-3:] == ["old 1", "old 2", "old 3"]


def test_scroll_region_commit_shrink_overwrites_stale_live_rows() -> None:
    cols, rows = 32, 8
    screen, stream = _screen(cols, rows)
    # A tall (6-row) live block just collapsed to 2 rows: rows 3-6 hold stale
    # live content that must never scroll into scrollback.
    for row, text in enumerate(
        [
            "old 1",
            "old 2",
            "| live stream 1",
            "| live stream 2",
            "| live stream 3",
            "| live stream 4",
            "[status: live]",
            "> typed",
        ],
        start=1,
    ):
        _write_row(stream, row, text)

    committed = [f"done {index}" for index in range(1, 8)]
    stream.feed(
        build_scroll_region_commit_injection(
            committed,
            live_region_height=2,
            terminal_height=rows,
            previous_live_region_height=6,
        ).encode()
    )

    scrollback = _scrollback(screen, cols)
    assert not any("live stream" in line for line in scrollback)
    assert scrollback[-3:] == ["old 1", "old 2", "done 1"]
    visible = _visible(screen, rows)
    assert visible[:6] == ["done 2", "done 3", "done 4", "done 5", "done 6", "done 7"]


def test_scroll_region_commit_resets_margins_for_later_full_screen_scroll() -> None:
    cols, rows = 32, 6
    screen, stream = _screen(cols, rows)
    for row, text in enumerate(
        ["old 1", "old 2", "old 3", "old 4", "[status: live]", "> typed"], start=1
    ):
        _write_row(stream, row, text)

    stream.feed(
        build_scroll_region_commit_injection(
            ["new A"], live_region_height=2, terminal_height=rows
        ).encode()
    )
    stream.feed(b"\x1b[6;1Hafter reset")

    visible = _visible(screen, rows)
    assert visible[-1] == "after reset"


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


def test_build_bottom_reset_moves_region_to_bottom_and_clears_down() -> None:
    seq = build_bottom_reset(region_height=2, terminal_height=6)
    assert seq == "\x1b[5;1H\x1b[0J"


def test_build_bottom_reset_handles_region_taller_than_terminal() -> None:
    seq = build_bottom_reset(region_height=8, terminal_height=6)
    assert seq == "\x1b[1;1H\x1b[0J"


def test_build_relocated_anchor_erases_old_block_and_pads_to_bottom() -> None:
    seq = build_relocated_anchor(
        reply_row=10, caret_row_offset=2, region_height=5, terminal_height=24
    )
    # Relocated block top = 10 - 2 = row 8 (0-based); erase down, then pad
    # 24 - 8 - 5 = 11 linefeeds so the repaint lands flush with the bottom.
    assert seq == "\x1b[9;1H\x1b[0J" + "\n" * 11


def test_build_relocated_anchor_preserves_relocated_transcript() -> None:
    cols, rows = 32, 8
    screen, stream = _screen(cols, rows)
    # Post-reflow screen: transcript pulled adjacent to the relocated live
    # block (rows 4-6), with the caret row at row 6 (offset 2 in the block).
    for row, text in enumerate(
        [
            "hello reply",
            "prompt echo",
            "tail line",
            "[old status]",
            "[old queue]",
            "> old caret",
            "",
            "",
        ],
        start=1,
    ):
        _write_row(stream, row, text)

    stream.feed(
        build_relocated_anchor(
            reply_row=5, caret_row_offset=2, region_height=3, terminal_height=rows
        ).encode()
    )

    visible = _visible(screen, rows)
    assert visible[:3] == ["hello reply", "prompt echo", "tail line"]
    assert visible[3:] == ["", "", "", "", ""]
    assert _scrollback(screen, cols) == []


def test_build_relocated_anchor_clamps_negative_top() -> None:
    seq = build_relocated_anchor(
        reply_row=1, caret_row_offset=5, region_height=4, terminal_height=6
    )
    assert seq == "\x1b[1;1H\x1b[0J" + "\n" * 2


def test_build_resize_sweep_matches_bottom_reset_when_no_row_wraps() -> None:
    seq = build_resize_sweep(
        painted_widths=[20, 10, 30], new_width=60, region_height=3, terminal_height=24
    )
    assert seq == build_bottom_reset(region_height=3, terminal_height=24) + "\x1b[22;1H"


def test_build_resize_sweep_erases_reflow_expanded_extent() -> None:
    # 120-wide row rewraps into 2 rows at width 60: the old live block can
    # occupy 4 rows, so the sweep starts one row above the plain bottom reset.
    seq = build_resize_sweep(
        painted_widths=[120, 10, 10], new_width=60, region_height=3, terminal_height=24
    )
    assert seq == "\x1b[21;1H\x1b[0J\x1b[22;1H"


def test_build_resize_sweep_counts_multiple_wrap_rows_and_blank_rows() -> None:
    # 130 cells at width 40 -> 4 rows; blank (0-width) rows still occupy 1 row.
    seq = build_resize_sweep(
        painted_widths=[130, 0, 40], new_width=40, region_height=3, terminal_height=24
    )
    assert seq == "\x1b[19;1H\x1b[0J\x1b[22;1H"


def test_build_resize_sweep_clamps_to_screen_top() -> None:
    seq = build_resize_sweep(
        painted_widths=[400] * 10, new_width=20, region_height=10, terminal_height=12
    )
    assert seq == "\x1b[1;1H\x1b[0J\x1b[3;1H"


def test_build_resize_sweep_ignores_stale_row_count_when_no_row_wraps() -> None:
    # Widths recorded from a taller old frame must not widen the erase when
    # no row exceeds the new width: after a height shrink the emulator
    # relocates transcript next to the live block, and erasing by old row
    # count would destroy it.
    seq = build_resize_sweep(
        painted_widths=[10] * 13, new_width=40, region_height=7, terminal_height=24
    )
    assert seq == build_bottom_reset(region_height=7, terminal_height=24) + "\x1b[18;1H"


def test_build_resize_sweep_without_recorded_widths_uses_region_height() -> None:
    seq = build_resize_sweep(
        painted_widths=[], new_width=60, region_height=2, terminal_height=6
    )
    assert seq == build_bottom_reset(region_height=2, terminal_height=6) + "\x1b[5;1H"


def test_inline_terminal_setup_disables_autowrap_for_full_width_spaces() -> None:
    screen, stream = _screen(cols=5, rows=3)

    stream.feed(build_inline_terminal_setup().encode())
    stream.feed(b"     X")

    assert _visible(screen, 3) == ["    X", "", ""]


def test_inline_terminal_reset_restores_autowrap() -> None:
    screen, stream = _screen(cols=5, rows=3)

    stream.feed(build_inline_terminal_setup().encode())
    stream.feed(build_inline_terminal_reset().encode())
    stream.feed(b"     X")

    assert _visible(screen, 3) == ["", "X", ""]


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
