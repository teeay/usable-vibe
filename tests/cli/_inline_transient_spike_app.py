"""Spike: a tall transient live surface that disappears cleanly while a durable
result is committed to native scrollback.

This exercises the Phase 1A live-surface lifecycle at the terminal level using
the production injection helpers (`build_bottom_anchor` / `build_commit_injection`):

- a multi-line "splash" surface is shown in a live region (never committed);
- it is then removed, so it must vanish without leaving any scrollback transcript;
- a durable result line is committed and must reach native scrollback;
- the live region stays pinned to the bottom (anchored), and no alternate
  screen is used.

Not a product path; driven by `test_inline_scrollback_spike.py` under a PTY.
"""

from __future__ import annotations

from rich.console import RenderableType
from textual._compositor import InlineUpdate
from textual.app import App, ComposeResult
from textual.containers import VerticalGroup
from textual.geometry import Offset
from textual.screen import Screen
from textual.widgets import Static

from vibe.cli.textual_ui.inline_inject import (
    build_bottom_anchor,
    build_commit_injection,
)

_SPLASH_LINES = 4
_DURABLE_LINES = 14


class TransientSurfaceSpike(App[None]):
    CSS = """
    Screen { height: auto; }
    #live-surface { height: auto; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._pending: list[str] = []
        self._inline_anchored = False
        self._phase = 0

    def compose(self) -> ComposeResult:
        yield VerticalGroup(id="live-surface")
        yield Static("> _", id="input")

    def on_mount(self) -> None:
        splash = "\n".join(f"SPLASH LINE {i}" for i in range(_SPLASH_LINES))
        self.query_one("#live-surface", VerticalGroup).mount(
            Static(splash, id="splash")
        )
        self.set_interval(0.2, self._advance)

    def _advance(self) -> None:
        self._phase += 1
        if self._phase == 2:
            # Remove the transient surface: it must disappear without becoming
            # transcript, and commit durable results through the committer path.
            # Commit more lines than the screen can hold so the earliest ones are
            # pushed into native scrollback (history), proving durable
            # finalization rather than merely remaining on the visible screen.
            self.query_one("#splash").remove()
            self._pending.extend(
                f"durable result line {i}" for i in range(_DURABLE_LINES)
            )
            self.refresh()
        elif self._phase >= 4:
            self.set_timer(0.3, self.exit)

    def _display(self, screen: Screen, renderable: RenderableType | None) -> None:
        if (
            isinstance(renderable, InlineUpdate)
            and self._driver is not None
            and self._driver.is_inline
        ):
            if not self._inline_anchored and self._driver.cursor_origin is not None:
                prev = self._previous_cursor_position
                import shutil

                sequence = build_bottom_anchor(
                    region_top=self._driver.cursor_origin[1],
                    region_height=len(renderable.strips),
                    terminal_height=shutil.get_terminal_size((80, 24)).lines,
                    cursor_offset=(prev.x, prev.y),
                )
                self._inline_anchored = True
                if sequence is not None:
                    self._driver.write(sequence)
                    self._previous_cursor_position = Offset(0, 0)
            if self._pending:
                prev = self._previous_cursor_position
                self._driver.write(
                    build_commit_injection(self._pending, (prev.x, prev.y))
                )
                self._pending.clear()
                self._previous_cursor_position = Offset(0, 0)
        super()._display(screen, renderable)


if __name__ == "__main__":
    TransientSurfaceSpike().run(inline=True, inline_no_clear=True)
