from __future__ import annotations

from pathlib import Path
import re

import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
    committed_scrollback,
)
from vibe.core.config import SessionLoggingConfig


@pytest.mark.asyncio
async def test_log_command_shows_persisted_session_directory(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    agent_loop = build_test_agent_loop(config=config)
    await agent_loop.persist_empty_session()
    app = build_test_vibe_app(config=config, agent_loop=agent_loop)

    async with app.run_test() as pilot:
        handled = await app._handle_command("/log")
        await pilot.pause()

        text = committed_scrollback(app)

    assert handled is True
    assert text.count("Current Log Directory") == 1
    assert re.sub(r"\s+", "", str(tmp_path)) in re.sub(r"\s+", "", text)
    assert "You can send this directory to share your interaction." in text
