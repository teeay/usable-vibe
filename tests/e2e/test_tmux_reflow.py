from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import re
import shlex
import shutil
import time
import uuid

import pytest

from tests import TESTS_ROOT
from tests.e2e.tmux_loop import TmuxSession, tmux_available

pytestmark = pytest.mark.skipif(
    not tmux_available(), reason="tmux is required for the reflow tier"
)

_COLS = 120
_ROWS = 36
_NARROW_COLS = 60
_BARE_PROMPT_LINE = re.compile(r"^>\s*$")
_ENV_KEYS = ("VIBE_HOME", "UVIBE_HOME", "MISTRAL_API_KEY", "VIBE_TEST_DISABLE_KEYRING")


@pytest.fixture
def short_workdir() -> Iterator[Path]:
    # The pytest tmp workdir path is ~100 characters, which over-fills the
    # bottom bar and clips the right-aligned label/context chrome. A short
    # path matches real usage: the bar paints content out to the last column.
    workdir = Path("/tmp") / f"uvr-{uuid.uuid4().hex[:8]}"
    workdir.mkdir(parents=True, exist_ok=False)
    try:
        yield workdir
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _spawn_tmux_vibe(e2e_workdir: Path, *, cols: int, rows: int) -> TmuxSession:
    env = {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}
    command = f"uv run uvibe --workdir {shlex.quote(str(e2e_workdir))}"
    return TmuxSession(command, cwd=TESTS_ROOT.parent, env=env, cols=cols, rows=rows)


def _assert_no_live_chrome(lines: list[str]) -> None:
    assert not any(_BARE_PROMPT_LINE.match(line) for line in lines), (
        "live input prompt row leaked into scrollback history:\n"
        + "\n".join(lines[-40:])
    )
    assert not any("tokens (" in line for line in lines), (
        "bottom-bar context progress leaked into scrollback history:\n"
        + "\n".join(lines[-40:])
    )
    assert not any("Generating" in line for line in lines), (
        "live status leaked into scrollback history:\n" + "\n".join(lines[-40:])
    )
    assert not any("[default]" in line for line in lines), (
        "bottom-bar agent label leaked into scrollback history:\n"
        + "\n".join(lines[-40:])
    )


@pytest.mark.timeout(90)
@pytest.mark.usefixtures("setup_e2e_env")
def test_tmux_decstbm_commit_reaches_history(e2e_workdir: Path) -> None:
    session = _spawn_tmux_vibe(e2e_workdir, cols=80, rows=12)
    try:
        session.wait_for_text("Usable Vibe v", timeout=45)
        session.send_text("!seq 1 40")
        session.send_enter()
        session.wait_for_line("40", timeout=30)
        history = session.history_lines()
        assert any(line.strip() == "5" for line in history), (
            "DECSTBM-committed lines did not reach tmux history; "
            "top-anchored scroll-region output may be discarded by this "
            "multiplexer.\n\nHistory:\n" + "\n".join(history[-40:])
        )
    finally:
        session.kill()


@pytest.mark.timeout(90)
@pytest.mark.usefixtures("setup_e2e_env")
def test_tmux_narrow_resize_keeps_live_chrome_out_of_history(
    short_workdir: Path,
) -> None:
    session = _spawn_tmux_vibe(short_workdir, cols=_COLS, rows=_ROWS)
    try:
        session.wait_for_text("Usable Vibe v", timeout=45)
        session.send_text("Greet")
        session.send_enter()
        session.wait_for_text("Hello from mock server", timeout=30)

        session.resize(_NARROW_COLS)
        time.sleep(1.5)

        combined = session.all_lines()
        assert any("Hello from mock server" in line for line in combined), (
            "committed assistant response lost after narrow resize:\n"
            + "\n".join(combined[-40:])
        )
        _assert_no_live_chrome(session.history_lines())

        # Reflowed live-chrome fragments initially sit on screen just above
        # the repainted live region; they only reach scrollback when later
        # commits scroll the transcript region. Flush them with a tall commit.
        session.send_text("!seq 1 60")
        session.send_enter()
        session.wait_for_line("60", timeout=30)
        time.sleep(1.0)

        history = session.history_lines()
        assert any(line.strip() == "5" for line in history)
        _assert_no_live_chrome(history)
    finally:
        session.kill()


@pytest.mark.timeout(120)
@pytest.mark.usefixtures("setup_e2e_env")
def test_tmux_drag_resize_storm_keeps_live_chrome_out_of_history(
    short_workdir: Path,
) -> None:
    session = _spawn_tmux_vibe(short_workdir, cols=_COLS, rows=_ROWS)
    try:
        session.wait_for_text("Usable Vibe v", timeout=45)
        session.send_text("Greet")
        session.send_enter()
        session.wait_for_text("Hello from mock server", timeout=30)

        # Emulate an interactive corner drag: many intermediate sizes in quick
        # succession — width and height together — each reflowing rows the app
        # has just repainted. 40 columns is narrow enough that the input band
        # and typed content wrap as well, not just the bottom bar.
        sizes = [
            (110, 34),
            (100, 32),
            (90, 30),
            (80, 28),
            (70, 26),
            (60, 24),
            (50, 22),
            (40, 20),
        ]
        for cols, rows in sizes:
            session.resize(cols, rows)
            time.sleep(0.06)
        time.sleep(1.5)

        session.send_text("!seq 1 60")
        session.send_enter()
        session.wait_for_line("60", timeout=30)
        time.sleep(1.0)

        combined = session.all_lines()
        assert any("Hello from mock server" in line for line in combined), (
            "committed assistant response lost after drag resize:\n"
            + "\n".join(combined[-40:])
        )
        _assert_no_live_chrome(session.history_lines())
    finally:
        session.kill()
