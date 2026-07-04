from __future__ import annotations

from rich.console import Console
from rich.segment import Segment
from rich.style import Style
from textual._compositor import InlineUpdate
from textual.strip import Strip

from vibe.cli.textual_ui.native_scroll.inline_frame import (
    strip_trailing_padding,
    trim_inline_update,
)


def _console() -> Console:
    return Console(force_terminal=True, color_system=None, width=20)


def test_strip_trailing_padding_removes_layout_spaces() -> None:
    strip = Strip([Segment("value"), Segment("     ", Style(dim=True))], 10)

    trimmed = strip_trailing_padding(strip)

    assert trimmed.text == "value "
    assert trimmed.cell_length == 6


def test_strip_trailing_padding_preserves_metadata_spaces() -> None:
    meta_style = Style(meta={"offset": (4, 0)})
    strip = Strip([Segment("value"), Segment("   ", meta_style)], 8)

    trimmed = strip_trailing_padding(strip)

    assert trimmed is strip
    assert trimmed.text == "value   "
    assert trimmed.cell_length == 8


def test_trimmed_inline_update_clears_rows_without_padding_to_margin() -> None:
    update = InlineUpdate(
        [
            Strip([Segment("status"), Segment("     ")], 11),
            Strip([Segment("prompt"), Segment("     ")], 11),
        ],
        clear=True,
    )

    sequence = trim_inline_update(update).render_segments(_console())

    assert "status     " not in sequence
    assert "prompt     " not in sequence
    assert "status \x1b[K\r\nprompt \x1b[K\r\n\x1b[J\x1b[2A\r\x1b[6n" == sequence


def test_trimmed_inline_update_fills_band_tail_with_background_erase() -> None:
    band = Style(bgcolor="red")
    update = InlineUpdate(
        [Strip([Segment("> typed", band), Segment("      ", band)], 13)], clear=False
    )
    console = Console(force_terminal=True, color_system="standard", width=20)

    sequence = trim_inline_update(update).render_segments(console)

    # The band background is restored via background-color-erase instead of
    # written spaces: SGR bg + EL + reset, no padding cells on the wire.
    assert "\x1b[41m\x1b[K\x1b[0m" in sequence
    assert "      " not in sequence


def test_trimmed_inline_update_uses_plain_erase_for_default_background() -> None:
    update = InlineUpdate(
        [Strip([Segment("status"), Segment("     ", Style(dim=True))], 11)], clear=False
    )
    console = Console(force_terminal=True, color_system="standard", width=20)

    sequence = trim_inline_update(update).render_segments(console)

    assert "\x1b[K" in sequence
    assert "\x1b[41m" not in sequence
    assert "m\x1b[K\x1b[0m" not in sequence


def test_trimmed_inline_update_records_painted_widths() -> None:
    update = InlineUpdate(
        [
            Strip([Segment("status"), Segment("     ")], 11),
            Strip([Segment("           ")], 11),
            Strip([Segment("> typed"), Segment("    ")], 11),
        ],
        clear=False,
    )

    trimmed = trim_inline_update(update)

    # Trailing padding is excluded from the recorded painted width (one
    # trailing space is preserved after content for the caret cell).
    assert trimmed.painted_widths == [7, 0, 8]
