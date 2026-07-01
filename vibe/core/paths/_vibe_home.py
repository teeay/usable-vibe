from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path

from vibe import VIBE_ROOT


class GlobalPath:
    def __init__(self, resolver: Callable[[], Path]) -> None:
        self._resolver = resolver

    @property
    def path(self) -> Path:
        return self._resolver()


_DEFAULT_VIBE_HOME = Path.home() / ".vibe"
_DEFAULT_UVIBE_HOME = Path.home() / ".uvibe"


def _get_vibe_home() -> Path:
    if vibe_home := os.getenv("VIBE_HOME"):
        return Path(vibe_home).expanduser().resolve()
    return _DEFAULT_VIBE_HOME


def _get_uvibe_home() -> Path:
    if uvibe_home := os.getenv("UVIBE_HOME"):
        return Path(uvibe_home).expanduser().resolve()
    return _DEFAULT_UVIBE_HOME


VIBE_HOME = GlobalPath(_get_vibe_home)
UVIBE_HOME = GlobalPath(_get_uvibe_home)
GLOBAL_ENV_FILE = GlobalPath(lambda: VIBE_HOME.path / ".env")
SESSION_LOG_DIR = GlobalPath(lambda: VIBE_HOME.path / "logs" / "session")
TRUSTED_FOLDERS_FILE = GlobalPath(lambda: VIBE_HOME.path / "trusted_folders.toml")
CONNECTOR_BOOTSTRAP_CACHE_FILE = GlobalPath(
    lambda: VIBE_HOME.path / "connector_bootstrap_cache.json"
)
HISTORY_FILE = GlobalPath(lambda: VIBE_HOME.path / "vibehistory")
PLANS_DIR = GlobalPath(lambda: VIBE_HOME.path / "plans")
CACHE_FILE = GlobalPath(lambda: UVIBE_HOME.path / "cache.toml")
LOG_DIR = GlobalPath(lambda: UVIBE_HOME.path / "logs")
LOG_FILE = GlobalPath(lambda: UVIBE_HOME.path / "logs" / "vibe.log")
ACP_LOG_DIR = GlobalPath(lambda: UVIBE_HOME.path / "logs" / "acp")
ACP_LOG_FILE = GlobalPath(lambda: UVIBE_HOME.path / "logs" / "acp" / "messages.jsonl")

DEFAULT_TOOL_DIR = GlobalPath(lambda: VIBE_ROOT / "core" / "tools" / "builtins")
