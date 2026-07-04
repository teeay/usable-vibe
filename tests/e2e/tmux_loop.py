from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

_SESSION = "main"
_TMUX_TIMEOUT = 10.0

# `set -g window-size manual` is deliberately absent: it segfaults tmux 3.6b
# (`default_window_size` -> `clients_calculate_size`) when creating a detached
# session. `resize-window -x/-y` switches the window to manual sizing itself.
_TMUX_CONF = """
set -g default-terminal "xterm-256color"
set -g escape-time 0
set -g history-limit 5000
set -g remain-on-exit on
"""


def tmux_available() -> bool:
    return shutil.which("tmux") is not None


class TmuxSession:
    """A detached tmux session driving the app under a reflowing emulator.

    tmux rewraps buffer lines on narrow resize like the real target emulators
    (iTerm2, Terminal.app, kitty, ...), which `pyte` cannot model. Each
    instance owns a private tmux server on a unique socket so user sessions
    are never touched.
    """

    def __init__(
        self, command: str, *, cwd: Path, env: Mapping[str, str], cols: int, rows: int
    ) -> None:
        self._socket = f"uvibe-e2e-{uuid.uuid4().hex[:12]}"
        conf = tempfile.NamedTemporaryFile(
            "w", suffix=".tmux.conf", delete=False, encoding="utf-8"
        )
        conf.write(_TMUX_CONF)
        conf.close()
        self._conf_path = Path(conf.name)
        env_args: list[str] = []
        for key, value in env.items():
            env_args.extend(["-e", f"{key}={value}"])
        self._run(
            "new-session",
            "-d",
            "-s",
            _SESSION,
            "-x",
            str(cols),
            "-y",
            str(rows),
            "-c",
            str(cwd),
            *env_args,
            command,
        )
        self._rows = rows

    def _run(self, *args: str) -> str:
        result = subprocess.run(
            ["tmux", "-f", str(self._conf_path), "-L", self._socket, *args],
            capture_output=True,
            text=True,
            timeout=_TMUX_TIMEOUT,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"tmux {' '.join(args)} failed ({result.returncode}): "
                f"{result.stderr.strip()}"
            )
        return result.stdout

    def send_text(self, text: str) -> None:
        self._run("send-keys", "-t", _SESSION, "-l", text)

    def send_enter(self) -> None:
        self._run("send-keys", "-t", _SESSION, "Enter")

    def resize(self, cols: int, rows: int | None = None) -> None:
        target_rows = self._rows if rows is None else rows
        self._run(
            "resize-window", "-t", _SESSION, "-x", str(cols), "-y", str(target_rows)
        )
        self._rows = target_rows

    def all_lines(self) -> list[str]:
        return self._capture("-S", "-")

    def visible_lines(self) -> list[str]:
        return self._capture()

    def history_lines(self) -> list[str]:
        if self._history_size() == 0:
            return []
        return self._capture("-S", "-", "-E", "-1")

    def _history_size(self) -> int:
        output = self._run("display-message", "-p", "-t", _SESSION, "#{history_size}")
        return int(output.strip() or "0")

    def _capture(self, *args: str) -> list[str]:
        output = self._run("capture-pane", "-p", "-t", _SESSION, *args)
        return output.split("\n")

    def wait_for_text(self, needle: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(needle in line for line in self.all_lines()):
                return
            time.sleep(0.2)
        tail = "\n".join(self.all_lines()[-30:])
        raise AssertionError(
            f"Timed out waiting for tmux pane text: {needle!r}\n\nPane tail:\n{tail}"
        )

    def wait_for_line(self, exact: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(line.strip() == exact for line in self.all_lines()):
                return
            time.sleep(0.2)
        tail = "\n".join(self.all_lines()[-30:])
        raise AssertionError(
            f"Timed out waiting for tmux pane line: {exact!r}\n\nPane tail:\n{tail}"
        )

    def kill(self) -> None:
        try:
            subprocess.run(
                ["tmux", "-L", self._socket, "kill-server"],
                capture_output=True,
                timeout=_TMUX_TIMEOUT,
                check=False,
            )
        finally:
            self._conf_path.unlink(missing_ok=True)
