from __future__ import annotations

import importlib.metadata

import pytest

from mistralai_vibe_local_harness.vibe._mcp_transport import _vibe_distribution_version
from vibe import __version__


def test_distribution_version_matches_installed_vibe_package() -> None:
    assert _vibe_distribution_version() == __version__


def test_distribution_version_uses_owning_vibe_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_version = importlib.metadata.version
    installed = __version__
    monkeypatch.setattr(
        importlib.metadata, "packages_distributions", lambda: {"vibe": ["uvibe"]}
    )

    def version(name: str) -> str:
        if name == "uvibe":
            return installed
        if name in {"mistral-vibe", "mistralai-vibe-local-harness"}:
            raise importlib.metadata.PackageNotFoundError(name)
        return real_version(name)

    monkeypatch.setattr(importlib.metadata, "version", version)
    assert _vibe_distribution_version() == installed
