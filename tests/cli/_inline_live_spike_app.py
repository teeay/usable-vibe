"""Phase 3 spike: inject committed lines into native scrollback from inside
Textual's own inline frame production, while the live region is actively
repainted by Textual's compositor.

The injection happens inside an `App._display` override, right after Textual
moves the cursor back to the region's top-left and before it renders the inline
region. Because `_display` builds and writes the whole frame synchronously on
Textual's single message-loop thread, the committed lines and the region redraw
cannot interleave with another repaint. Not a product path; run by the spike
test under a pseudo-terminal.
"""

from __future__ import annotations

import os

from rich.console import RenderableType
from rich.control import Control
from textual._compositor import InlineUpdate
from textual.app import App, ComposeResult
from textual.geometry import Offset
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Static

_COMMITS = int(os.environ.get("SPIKE_COMMITS", "14"))


class LiveInjectSpike(App[None]):
    CSS = "Screen { height: auto; }"

    counter: reactive[int] = reactive(0)

    def __init__(self) -> None:
        super().__init__()
        self._pending: list[str] = []
        self._committed = 0

    def compose(self) -> ComposeResult:
        yield Static("live region · tick 0", id="status")

    def watch_counter(self, value: int) -> None:
        status = self.query("#status")
        if status:
            self.query_one("#status", Static).update(f"live region · tick {value}")

    def on_mount(self) -> None:
        self.set_interval(0.08, self._tick)
        self.set_interval(0.2, self._commit)

    def _tick(self) -> None:
        self.counter += 1

    def _commit(self) -> None:
        if self._committed >= _COMMITS:
            if not self._pending:
                self.set_timer(0.3, self.exit)
            return
        self._pending.append(f"committed line {self._committed}")
        self._committed += 1
        self.refresh()

    def _display(self, screen: Screen, renderable: RenderableType | None) -> None:
        if (
            self._pending
            and isinstance(renderable, InlineUpdate)
            and self._driver is not None
            and self._driver.is_inline
        ):
            prev = self._previous_cursor_position
            inject = Control.move(-prev.x, -prev.y).segment.text
            inject += "\x1b[0J"  # erase the region so short lines leave no remnants
            inject += "".join(f"{line}\r\n" for line in self._pending)
            self._pending.clear()
            self._driver.write(inject)
            self._previous_cursor_position = Offset(0, 0)
        super()._display(screen, renderable)


if __name__ == "__main__":
    LiveInjectSpike().run(inline=True, inline_no_clear=True)
