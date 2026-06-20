"""Phase 1 spike: a minimal real Textual inline app that commits lines to the
host terminal scrollback through its own inline driver stream (a single writer),
while keeping a small live region pinned at the bottom.

Run standalone under a pseudo-terminal by the Phase 1 spike test. Not a product
path. The commit escape sequence is duplicated from
`tests.cli.terminal_loop.ink_commit_sequence` so this file stays import-free for
direct execution under `pexpect`.
"""

from __future__ import annotations

import os

from textual.app import App, ComposeResult
from textual.widgets import Static

_REGION = ("[status: idle]", "> _")
_COMMITS = int(os.environ.get("SPIKE_COMMITS", "8"))


def _ink_commit_sequence(line: str, region: tuple[str, ...]) -> str:
    height = len(region)
    parts = ["\x1b[0J", f"{line}\r\n", "\r\n".join(region)]
    if height > 1:
        parts.append(f"\x1b[{height - 1}A")
    parts.append("\r")
    return "".join(parts)


class InlineScrollbackSpike(App[None]):
    CSS = "Screen { height: auto; }"

    def compose(self) -> ComposeResult:
        yield Static("\n".join(_REGION))

    def on_mount(self) -> None:
        self.set_timer(0.4, self._commit_all)

    def _commit_all(self) -> None:
        driver = self._driver
        if driver is None:
            self.exit()
            return
        for index in range(_COMMITS):
            driver.write(_ink_commit_sequence(f"committed line {index}", _REGION))
        driver.flush()
        self.set_timer(0.2, self.exit)


if __name__ == "__main__":
    InlineScrollbackSpike().run(inline=True, inline_no_clear=True)
