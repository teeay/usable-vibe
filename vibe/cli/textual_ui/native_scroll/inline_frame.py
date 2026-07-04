"""Textual-aware inline frame rendering for native scroll mode."""

from __future__ import annotations

from typing import NamedTuple

from rich.console import COLOR_SYSTEMS, Console
from rich.segment import Segment
from rich.style import Style
from textual._compositor import InlineUpdate
from textual.strip import Strip


class TrimmedRow(NamedTuple):
    strip: Strip
    tail_style: Style | None


def _is_trim_safe_space_segment(segment: Segment) -> bool:
    text, style, control = segment
    if control or not text or not text.endswith(" "):
        return False
    if style is not None and style.meta:
        return False
    return True


def trim_row(strip: Strip) -> TrimmedRow:
    """Remove Textual right-margin padding spaces from a rendered strip.

    Textual inline frames are fixed-width strips, so rows are commonly padded to
    the terminal width with styled spaces. Those padding cells are visually
    empty but terminal emulators may still reflow them during resize. Segments
    with Textual metadata are preserved because they can represent meaningful
    text-area content or cursor cells rather than layout padding.

    Returns the trimmed strip together with the style of the rightmost trimmed
    padding, so the row tail can be repainted with background-color-erase
    (``SGR bg`` + ``ESC[K``) instead of written cells — full-width background
    bands keep their look without creating reflowable content.
    """
    preserve = 1 if strip.text.rstrip(" ") else 0
    segments = list(strip)
    removed = 0
    tail_style: Style | None = None
    while segments and _is_trim_safe_space_segment(segments[-1]):
        text, style, control = segments[-1]
        trailing_spaces = len(text) - len(text.rstrip(" "))
        remove_count = max(0, trailing_spaces - preserve)
        if remove_count == 0:
            break
        trimmed = text[:-remove_count]
        removed += remove_count
        preserve = 0
        if tail_style is None:
            tail_style = style
        if trimmed:
            segments[-1] = Segment(trimmed, style, control)
            break
        segments.pop()
    if removed == 0:
        return TrimmedRow(strip, None)
    return TrimmedRow(Strip(segments, max(0, strip.cell_length - removed)), tail_style)


def strip_trailing_padding(strip: Strip) -> Strip:
    """Remove Textual right-margin padding spaces from a rendered strip."""
    return trim_row(strip).strip


def _erase_row_tail(tail_style: Style | None, console: Console) -> str:
    """Erase to end of line, filling with the trimmed padding's background.

    Background-color-erase keeps a full-width band visually intact without
    writing cells the emulator could rewrap into scrollback on narrow resize.
    """
    if (
        tail_style is None
        or tail_style.bgcolor is None
        or tail_style.bgcolor.is_default
    ):
        return "\x1b[K"
    color_system = COLOR_SYSTEMS.get(console.color_system or "")
    if color_system is None:
        return "\x1b[K"
    return tail_style.background_style.render("\x1b[K", color_system=color_system)


class TrimmedInlineUpdate(InlineUpdate):
    """Inline update that clears row tails without writing padding spaces.

    Strips are trimmed once at construction; ``painted_widths`` exposes the
    per-row painted cell widths of the frame actually written to the terminal,
    which the resize repair sweep uses to compute how far the live block can
    expand when a reflowing emulator rewraps it on narrow resize.
    """

    def __init__(self, strips: list[Strip], clear: bool = False) -> None:
        rows = [trim_row(strip) for strip in strips]
        super().__init__([row.strip for row in rows], clear=clear)
        self._tail_styles = [row.tail_style for row in rows]

    @property
    def painted_widths(self) -> list[int]:
        return [strip.cell_length for strip in self.strips]

    def render_segments(self, console: Console) -> str:
        sequences: list[str] = []
        append = sequences.append
        strips = self.strips
        for index, strip in enumerate(strips):
            append(strip.render(console))
            append(_erase_row_tail(self._tail_styles[index], console))
            if index < len(strips) - 1:
                append("\r\n")
        if self.clear:
            if len(strips) > 1:
                append("\r\n")
            append("\x1b[J")
        if len(strips) > 1:
            back_lines = len(strips) if self.clear else len(strips) - 1
            append(f"\x1b[{back_lines}A\r")
        else:
            append("\r")
        append("\x1b[6n")
        return "".join(sequences)


def trim_inline_update(renderable: InlineUpdate) -> TrimmedInlineUpdate:
    """Return an inline update that avoids full-width padding writes."""
    return TrimmedInlineUpdate(renderable.strips, clear=renderable.clear)
