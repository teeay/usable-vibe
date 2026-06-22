from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.paths import (
    ACP_LOG_FILE,
    CACHE_FILE,
    GLOBAL_ENV_FILE,
    HISTORY_FILE,
    LOG_FILE,
    PLANS_DIR,
    SESSION_LOG_DIR,
    TRUSTED_FOLDERS_FILE,
    UVIBE_HOME,
    VIBE_HOME,
)


def test_shared_user_data_stays_under_vibe_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vibe_home = tmp_path / "shared"
    uvibe_home = tmp_path / "fork-state"
    monkeypatch.setenv("VIBE_HOME", str(vibe_home))
    monkeypatch.setenv("UVIBE_HOME", str(uvibe_home))

    assert VIBE_HOME.path == vibe_home
    assert GLOBAL_ENV_FILE.path == vibe_home / ".env"
    assert SESSION_LOG_DIR.path == vibe_home / "logs" / "session"
    assert TRUSTED_FOLDERS_FILE.path == vibe_home / "trusted_folders.toml"
    assert HISTORY_FILE.path == vibe_home / "vibehistory"
    assert PLANS_DIR.path == vibe_home / "plans"


def test_fork_runtime_state_uses_uvibe_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vibe_home = tmp_path / "shared"
    uvibe_home = tmp_path / "fork-state"
    monkeypatch.setenv("VIBE_HOME", str(vibe_home))
    monkeypatch.setenv("UVIBE_HOME", str(uvibe_home))

    assert UVIBE_HOME.path == uvibe_home
    assert CACHE_FILE.path == uvibe_home / "cache.toml"
    assert LOG_FILE.path == uvibe_home / "logs" / "vibe.log"
    assert ACP_LOG_FILE.path == uvibe_home / "logs" / "acp" / "messages.jsonl"
