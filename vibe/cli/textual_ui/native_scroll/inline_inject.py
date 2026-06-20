"""Pure terminal escape-sequence builders for the single-writer inline region.

These helpers contain the cursor math used by ``VibeApp._display`` to inject
committed transcript lines into the host terminal's native scrollback and to
keep the live inline region anchored at the bottom of the terminal. They are
deliberately free of Textual and app state so the terminal behavior can be
verified directly against a terminal emulator (see
``tests/cli/test_inline_inject.py``).

The model is the same one validated by the inline scrollback spikes: from the
in-region text-cursor offset, move to the region's top-left, erase the region
downward, then write completed lines. When the region is flush with the bottom
of the terminal, each ``\\r\\n`` scrolls a committed line up into native
scrollback while the region is repainted directly below.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.control import Control


def _move_to_region_top(cursor_x: int, cursor_y: int) -> str:
    """Sequence moving from the in-region text cursor to the region top-left."""
    return Control.move(-cursor_x, -cursor_y).segment.text


def build_commit_injection(
    committed_lines: Sequence[str], cursor_offset: tuple[int, int]
) -> str:
    """Build the escape sequence that commits ``committed_lines`` above the region.

    Args:
        committed_lines: Already-rendered transcript lines (no trailing newline).
        cursor_offset: The region's recorded in-region text-cursor offset
            (``_previous_cursor_position``) so the cursor can be returned to the
            region's top-left before writing.

    The caller must reset its recorded cursor position to ``(0, 0)`` after
    writing this, because the region is re-rendered from the post-injection
    cursor position.
    """
    cursor_x, cursor_y = cursor_offset
    parts = [_move_to_region_top(cursor_x, cursor_y), "\x1b[0J"]
    parts.extend(f"{line}\r\n" for line in committed_lines)
    return "".join(parts)


def build_bottom_anchor(
    *,
    region_top: int,
    region_height: int,
    terminal_height: int,
    cursor_offset: tuple[int, int],
) -> str | None:
    """Build a sequence that pins a mid-screen region to the terminal bottom.

    Returns ``None`` when the region is already flush with the bottom (or taller
    than the terminal), so the caller can skip the write and treat the region as
    anchored. Otherwise it erases the stale region and pushes it down by the gap
    so the next region repaint renders flush against the bottom row, which is the
    precondition for committed lines to scroll into native scrollback.

    The caller must reset its recorded cursor position to ``(0, 0)`` after
    writing this.
    """
    gap = terminal_height - (region_top + region_height)
    if gap <= 0:
        return None
    cursor_x, cursor_y = cursor_offset
    return _move_to_region_top(cursor_x, cursor_y) + "\x1b[0J" + ("\n" * gap)
