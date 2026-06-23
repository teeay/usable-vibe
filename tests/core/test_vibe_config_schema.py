from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.config._settings import VibeConfig
from vibe.core.config.vibe_schema import VibeConfigSchema


def test_native_scroll_tool_output_config_defaults() -> None:
    config = VibeConfig()
    assert config.native_scroll_shorten_tool_output is True
    assert config.native_scroll_tool_output_head_lines == 3
    assert config.native_scroll_tool_output_tail_lines == 3


def test_native_scroll_tool_output_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBE_NATIVE_SCROLL_SHORTEN_TOOL_OUTPUT", "false")
    monkeypatch.setenv("VIBE_NATIVE_SCROLL_TOOL_OUTPUT_HEAD_LINES", "4")
    monkeypatch.setenv("VIBE_NATIVE_SCROLL_TOOL_OUTPUT_TAIL_LINES", "2")

    config = VibeConfig()

    assert config.native_scroll_shorten_tool_output is False
    assert config.native_scroll_tool_output_head_lines == 4
    assert config.native_scroll_tool_output_tail_lines == 2


def test_native_scroll_cursor_shape_defaults_to_block() -> None:
    assert VibeConfig().native_scroll_cursor_shape == "block"


def test_native_scroll_cursor_shape_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBE_NATIVE_SCROLL_CURSOR_SHAPE", "underscore")
    assert VibeConfig().native_scroll_cursor_shape == "underscore"


def test_vibe_config_schema_covers_all_vibe_config_fields() -> None:
    legacy_fields = set(VibeConfig.model_fields.keys())
    schema_fields = set(VibeConfigSchema.model_fields.keys())
    missing = legacy_fields - schema_fields
    assert not missing, (
        f"VibeConfigSchema is missing {len(missing)} field(s) that exist in VibeConfig: "
        f"{sorted(missing)}. "
        f"When you add a new field to VibeConfig, also add it to VibeConfigSchema "
        f"(vibe/core/config/vibe_schema.py) with the appropriate merge annotation."
    )


@pytest.mark.asyncio
async def test_full_toml_to_vibe_config_schema(tmp_path: Path) -> None:
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        """\
vim_keybindings = true
api_timeout = 300.0
api_retry_max_elapsed_time = 120.0
active_model = "codestral"
disabled_tools = ["bash"]
default_agent = "plan"
enabled_skills = ["search"]
enable_otel = true
native_scroll_shorten_tool_output = false
native_scroll_tool_output_head_lines = 4
native_scroll_tool_output_tail_lines = 2

[[models]]
alias = "codestral"
name = "codestral-latest"
provider = "mistral"
"""
    )

    from vibe.core.config.layers.user import UserConfigLayer
    from vibe.core.config.orchestrator import ConfigOrchestrator

    class VibeConfig(VibeConfigSchema):
        pass

    layer = UserConfigLayer(path=toml_path, name="user-toml")
    orchestrator = await ConfigOrchestrator[VibeConfig].create(
        schema=VibeConfig, layers=[layer]
    )
    config = orchestrator.config

    assert config.vim_keybindings is True
    assert config.api_timeout == 300.0
    assert config.api_retry_max_elapsed_time == 120.0
    assert config.active_model == "codestral"
    assert config.models[0].alias == "codestral"
    assert "bash" in config.disabled_tools
    assert config.default_agent == "plan"
    assert "search" in config.enabled_skills
    assert config.enable_otel is True
    assert config.native_scroll_shorten_tool_output is False
    assert config.native_scroll_tool_output_head_lines == 4
    assert config.native_scroll_tool_output_tail_lines == 2
