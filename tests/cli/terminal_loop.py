from __future__ import annotations

from collections.abc import Sequence
import errno
import re
from typing import Any, cast

import pexpect
import pyte

_DSR = b"\x1b[6n"
_ALT_SCREEN_ENTER = "\x1b[?1049h"


class TerminalLoop:
    """A terminal emulator in the test loop.

    Spawns a process under a real pseudo-terminal (`pexpect`) and feeds its
    output to a `pyte` emulator that models the visible screen and scrollback.
    Cursor-position queries (`\\x1b[6n`) are answered from the emulator so that
    Textual inline mode behaves as it would under a real terminal emulator.
    """

    def __init__(self, child: pexpect.spawn, rows: int, cols: int) -> None:
        self._child = child
        self._rows = rows
        self._cols = cols
        self._screen = pyte.HistoryScreen(cols, rows, history=5000)
        self._stream = pyte.ByteStream(self._screen)
        self._raw = bytearray()
        self._closed = False

    def pump(self, timeout: float = 0.3, idle_reads: int = 3) -> None:
        """Read available output until the child idles or exits."""
        idle = 0
        while idle < idle_reads and not self._closed:
            try:
                data = self._child.read_nonblocking(4096, timeout=timeout)
            except pexpect.TIMEOUT:
                idle += 1
                continue
            except pexpect.EOF:
                break
            if not data:
                idle += 1
                continue
            idle = 0
            self._feed(data)

    def _feed(self, data: bytes) -> None:
        self._raw.extend(data)
        for index, chunk in enumerate(data.split(_DSR)):
            self._stream.feed(chunk)
            is_last = index == data.count(_DSR)
            if not is_last:
                cursor = self._screen.cursor
                self._send_cursor_position(cursor.y + 1, cursor.x + 1)

    def _send_cursor_position(self, row: int, col: int) -> None:
        try:
            self._child.send(f"\x1b[{row};{col}R")
        except OSError as exc:
            if exc.errno not in {errno.EIO, errno.EPIPE}:
                raise
            self._closed = True

    @property
    def raw(self) -> bytes:
        return bytes(self._raw)

    @property
    def entered_alternate_screen(self) -> bool:
        return _ALT_SCREEN_ENTER in self._raw.decode("utf-8", "replace")

    def visible_lines(self) -> list[str]:
        return [self._screen.display[y].rstrip() for y in range(self._rows)]

    def scrollback_lines(self) -> list[str]:
        return [self._render_row(row) for row in self._screen.history.top]

    def all_lines(self) -> list[str]:
        return [*self.scrollback_lines(), *self.visible_lines()]

    def _render_row(self, row: object) -> str:
        cells = cast(dict[int, Any], row)
        return "".join(cells[x].data for x in range(self._cols)).rstrip()


def run_under_terminal(
    argv: Sequence[str],
    *,
    rows: int = 8,
    cols: int = 40,
    env: dict[str, str] | None = None,
    spawn_timeout: float = 20.0,
) -> TerminalLoop:
    """Run `argv` under a pseudo-terminal and return the settled emulator."""
    child = pexpect.spawn(
        argv[0],
        list(argv[1:]),
        dimensions=(rows, cols),
        encoding=None,
        timeout=spawn_timeout,
        env=env,
    )
    loop = TerminalLoop(child, rows=rows, cols=cols)
    loop.pump()
    child.close(force=True)
    return loop


def ink_commit_sequence(line: str, region: Sequence[str]) -> str:
    """The single-writer 'commit above a fixed live region' escape sequence.

    From the region's top-left: erase the region downward, print the committed
    line (which scrolls into native scrollback when the region is at the bottom),
    reprint the region below it, then park the cursor back at the region top.
    """
    height = len(region)
    parts = ["\x1b[0J", f"{line}\r\n", "\r\n".join(region)]
    if height > 1:
        parts.append(f"\x1b[{height - 1}A")
    parts.append("\r")
    return "".join(parts)


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)
