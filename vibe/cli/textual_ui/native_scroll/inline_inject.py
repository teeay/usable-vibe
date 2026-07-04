"""Pure terminal escape-sequence builders for the single-writer inline region.

These helpers contain the cursor math used by ``VibeApp._display`` to inject
committed transcript lines into the host terminal's native scrollback and to
keep the live inline region anchored at the bottom of the terminal. They are
deliberately free of Textual and app state so the terminal behavior can be
verified directly against a terminal emulator (see
``tests/cli/test_inline_inject.py``).

The primary model reserves the bottom live region by setting terminal scrolling
margins to the transcript rows above it, scrolling only that upper region, then
resetting the margins before Textual repaints the live rows. This mirrors
Codex-style inline TUIs and prevents status/input rows from being part of the
scrolling operation.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.control import Control

_MIN_SCROLL_REGION_BOTTOM = 2
DISABLE_AUTOWRAP = "\x1b[?7l"
ENABLE_AUTOWRAP = "\x1b[?7h"


def _move_to_region_top(cursor_x: int, cursor_y: int) -> str:
    """Sequence moving from the in-region text cursor to the region top-left."""
    return Control.move(-cursor_x, -cursor_y).segment.text


def build_commit_injection(
    committed_lines: Sequence[str],
    cursor_offset: tuple[int, int],
    *,
    live_region_height: int | None = None,
    terminal_height: int | None = None,
    previous_live_region_height: int | None = None,
) -> str:
    """Build the escape sequence that commits ``committed_lines`` above the region.

    Args:
        committed_lines: Already-rendered transcript lines (no trailing newline).
        cursor_offset: The region's recorded in-region text-cursor offset
            (``_previous_cursor_position``) so the cursor can be returned to the
            region's top-left before writing.
        live_region_height: Current live-region height. Together with
            ``terminal_height`` it switches the erase point to absolute
            coordinates, which is required for growth handling.
        terminal_height: Current terminal height.
        previous_live_region_height: Live-region height of the previous frame.
            When the region grew, the whole screen is first scrolled up by the
            growth delta so the transcript rows sitting where the taller
            region will repaint move toward native scrollback instead of being
            erased. Without this, a live region that grows to near-screen
            height (streaming bash on a short terminal) erases committed
            transcript with its ``ESC[0J``.

    The caller must reset its recorded cursor position to ``(0, 0)`` after
    writing this, because the region is re-rendered from the post-injection
    cursor position.
    """
    parts: list[str] = []
    if live_region_height is not None and terminal_height is not None:
        erase_height = live_region_height
        if previous_live_region_height is not None:
            growth = live_region_height - previous_live_region_height
            if growth > 0:
                parts.append(Control.move_to(0, terminal_height - 1).segment.text)
                parts.append("\n" * growth)
            else:
                # Shrink: rows of the old, taller live block above the new
                # region top are stale live content — erase them too so they
                # cannot scroll into scrollback as garbage.
                erase_height = previous_live_region_height
        region_top = max(0, terminal_height - erase_height)
        parts.append(Control.move_to(0, region_top).segment.text)
    else:
        cursor_x, cursor_y = cursor_offset
        parts.append(_move_to_region_top(cursor_x, cursor_y))
    parts.append("\x1b[0J")
    parts.extend(f"{line}\r\n" for line in committed_lines)
    return "".join(parts)


def build_scroll_region_commit_injection(
    committed_lines: Sequence[str],
    *,
    live_region_height: int,
    terminal_height: int,
    previous_live_region_height: int | None = None,
) -> str:
    """Build a scroll-region injection that preserves bottom live rows.

    The terminal scroll region is set to rows ``1..B``, where ``B`` is the row
    immediately above the live region. Each committed line is streamed through
    row ``B``: a linefeed scrolls only the region (the top region row moves into
    native scrollback), then the line is written into the freshly opened bottom
    row. Batches taller than the region therefore keep every line — the older
    lines scroll up into scrollback while the newest fill the visible region.
    ``CSI r`` resets scrolling margins before the caller asks Textual to repaint
    the live region.

    ``previous_live_region_height`` (the last *painted* frame's height) drives
    two corrections that keep live rows and transcript from mixing:

    - **Growth** (region taller than before): the transcript rows sitting where
      the taller live block will repaint are first scrolled up by the growth
      delta — inside margins covering the *old* transcript extent — so they
      move toward native scrollback instead of being painted over.
    - **Shrink** (region shorter than before, e.g. a tall streaming widget just
      finished): the rows between the old and new live-region tops still hold
      stale live content. They are *overwritten* with the first committed lines
      (or erased) rather than scrolled, so live-only rows never enter native
      scrollback and no blank gap is left behind.

    The returned sequence ends at the live region's top-left, so the app should
    reset ``_previous_cursor_position`` to ``Offset(0, 0)`` before delegating to
    Textual's inline repaint.
    """
    if not committed_lines:
        return ""

    region_bottom = terminal_height - live_region_height
    if region_bottom < _MIN_SCROLL_REGION_BOTTOM:
        return ""

    parts: list[str] = []
    stale_rows: list[int] = []
    if previous_live_region_height is not None:
        growth = live_region_height - previous_live_region_height
        old_region_bottom = terminal_height - previous_live_region_height
        if growth > 0 and old_region_bottom >= _MIN_SCROLL_REGION_BOTTOM:
            parts.append(f"\x1b[1;{old_region_bottom}r")
            parts.append(Control.move_to(0, old_region_bottom - 1).segment.text)
            parts.append("\n" * growth)
        elif growth < 0:
            first_stale = max(1, old_region_bottom + 1)
            stale_rows = list(range(first_stale, region_bottom + 1))
    parts.append(f"\x1b[1;{region_bottom}r")
    index = 0
    for row in stale_rows:
        parts.append(Control.move_to(0, row - 1).segment.text)
        parts.append("\x1b[2K")
        if index < len(committed_lines):
            parts.append(committed_lines[index])
            index += 1
    parts.append(Control.move_to(0, region_bottom - 1).segment.text)
    for line in committed_lines[index:]:
        parts.append("\n\r\x1b[2K")
        parts.append(line)
    parts.append("\x1b[r")
    live_top = max(0, terminal_height - live_region_height)
    parts.append(Control.move_to(0, live_top).segment.text)
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


def build_bottom_reset(*, region_height: int, terminal_height: int) -> str:
    """Build a sequence that resets the live region to the terminal bottom.

    This is used when the previous inline cursor position cannot be trusted,
    such as the first frame before a cursor-origin report or the first frame
    after a terminal resize. It clears only the live region and rows below it,
    not the whole visible terminal.
    """
    region_top = max(0, terminal_height - region_height)
    return Control.move_to(0, region_top).segment.text + "\x1b[0J"


def build_relocated_anchor(
    *, reply_row: int, caret_row_offset: int, region_height: int, terminal_height: int
) -> str:
    """Anchor the live region after a resize using a fresh cursor report.

    On resize the emulator reflows/relocates the buffer before the app is
    notified, so bottom-anchored geometry math cannot locate the old live
    block: after a height shrink the emulator may trim the block's trailing
    blank rows and pull committed transcript right next to it. The reliable
    signal is the cursor: emulators keep the cursor on its logical row through
    reflow, and the cursor was last placed on the live block's caret row. A
    fresh ``ESC[6n`` reply therefore gives ``reply_row``; subtracting the
    caret's in-region row offset yields the relocated block top.

    The sequence erases from the relocated top downward (exactly the old live
    block and any wrap fragments below it — never transcript above), then pads
    with linefeeds so the following repaint lands flush with the terminal
    bottom. Rows are 0-based.
    """
    top = max(0, reply_row - caret_row_offset)
    gap = max(0, terminal_height - top - region_height)
    return Control.move_to(0, top).segment.text + "\x1b[0J" + "\n" * gap


def build_resize_sweep(
    *,
    painted_widths: Sequence[int],
    new_width: int,
    region_height: int,
    terminal_height: int,
) -> str:
    """Build the post-resize erase that removes reflowed live-block fragments.

    Reflowing emulators rewrap buffer rows before the app receives
    ``SIGWINCH``: every previously painted live row wider than the new width
    expands into continuation rows, so the old live block can occupy more
    rows than the freshly computed bottom region. ``painted_widths`` is the
    per-row painted cell width of the last frame actually written (recorded by
    ``TrimmedInlineUpdate``); the sweep erases the worst-case expanded extent
    ``sum(ceil(width / new_width))`` from the terminal bottom so no fragment
    survives above the repainted region.

    The expansion counts only wrap-induced *extra* rows
    (``ceil(width / new_width) - 1`` per recorded row), added to the current
    region height. It deliberately does not erase the old frame's full row
    count: after a height shrink the emulator relocates transcript adjacent to
    the live block, and erasing by old row count would destroy it. On
    truncating emulators (xterm, pyte) rows never expand, so the extra erased
    rows above the region are bounded by the wrap-delta count.
    """
    extra = 0
    if painted_widths and new_width > 0:
        extra = sum(max(0, -(-width // new_width) - 1) for width in painted_widths)
    sweep_top = max(0, terminal_height - region_height - extra)
    region_top = max(0, terminal_height - region_height)
    return (
        Control.move_to(0, sweep_top).segment.text
        + "\x1b[0J"
        + Control.move_to(0, region_top).segment.text
    )


def build_inline_terminal_setup() -> str:
    """Build terminal setup used while Textual inline rendering is active.

    Textual's full-screen Linux driver disables autowrap while it owns the
    terminal. Inline mode does not, but Textual still paints full-width strips
    padded with literal spaces. Disabling autowrap prevents those last-column
    padding cells from becoming wrapped/reflowed terminal content during resize.
    """
    return DISABLE_AUTOWRAP


def build_inline_terminal_reset() -> str:
    """Build terminal reset for modes changed by ``build_inline_terminal_setup``."""
    return ENABLE_AUTOWRAP
