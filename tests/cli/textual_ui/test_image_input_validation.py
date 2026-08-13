from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
    committed_scrollback,
)
from vibe.app_server._workspace import PromptPreparationError, prepare_prompt
from vibe.app_server.models import ImageAttachment
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.types import Backend
from vibe.utils.images import MAX_IMAGE_BYTES, MAX_IMAGES_PER_MESSAGE

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _vision_config(*, supports_images: bool = True):
    models = [
        ModelConfig(
            name="mistral-vibe-cli-latest",
            provider="mistral",
            alias="devstral-latest",
            supports_images=supports_images,
        )
    ]
    providers = [
        ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
            backend=Backend.MISTRAL,
        )
    ]
    return build_test_vibe_config(
        active_model="devstral-latest", models=models, providers=providers
    )


def test_prepare_prompt_snapshots_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "shot.png").write_bytes(PNG_BYTES)
    agent_loop = build_test_agent_loop(config=_vision_config())

    prompt = prepare_prompt(agent_loop, "look at @shot.png")

    assert len(prompt.images) == 1
    assert isinstance(prompt.images[0], ImageAttachment)
    assert prompt.images[0].alias == "shot.png"
    assert prompt.images[0].mime_type == "image/png"


def test_prepare_prompt_returns_empty_images_when_no_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.md").write_text("hi")
    agent_loop = build_test_agent_loop(config=_vision_config())

    prompt = prepare_prompt(agent_loop, "read @notes.md")

    assert prompt.images == []


def test_prepare_prompt_rejects_too_many_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    mentions = []
    for i in range(MAX_IMAGES_PER_MESSAGE + 1):
        name = f"img{i}.png"
        (tmp_path / name).write_bytes(PNG_BYTES)
        mentions.append(f"@{name}")
    agent_loop = build_test_agent_loop(config=_vision_config())

    with pytest.raises(PromptPreparationError) as exc_info:
        prepare_prompt(agent_loop, " ".join(mentions))
    assert "Too many image attachments" in str(exc_info.value)
    assert str(MAX_IMAGES_PER_MESSAGE) in str(exc_info.value)


def test_prepare_prompt_rejects_non_vision_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "shot.png").write_bytes(PNG_BYTES)
    agent_loop = build_test_agent_loop(config=_vision_config(supports_images=False))

    with pytest.raises(PromptPreparationError) as exc_info:
        prepare_prompt(agent_loop, "look at @shot.png")
    assert "does not support images" in str(exc_info.value)
    assert "devstral-latest" in str(exc_info.value)


def test_prepare_prompt_rejects_oversize_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Patch the cap down so we don't need to write 10MB to disk.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("vibe.core.session.image_snapshot.MAX_IMAGE_BYTES", 32)

    (tmp_path / "shot.png").write_bytes(PNG_BYTES + b"\x00" * 64)
    agent_loop = build_test_agent_loop(config=_vision_config())

    with pytest.raises(PromptPreparationError) as exc_info:
        prepare_prompt(agent_loop, "look at @shot.png")
    assert "shot.png" in str(exc_info.value)
    assert "large" in str(exc_info.value).lower()


def test_max_image_bytes_default_is_10_mib() -> None:
    assert MAX_IMAGE_BYTES == 10 * 1024 * 1024


def test_max_images_per_message_default_is_8() -> None:
    assert MAX_IMAGES_PER_MESSAGE == 8


@pytest.mark.asyncio
async def test_submit_restores_input_when_image_validation_fails(
    tmp_path: Path,
) -> None:
    (tmp_path / "shot.png").write_bytes(PNG_BYTES)
    app = build_test_vibe_app(config=_vision_config(supports_images=False))
    typed = f"look at @{tmp_path / 'shot.png'}"

    async with app.run_test() as pilot:
        chat_input = app.query_one(ChatInputContainer)
        chat_input.value = typed
        await pilot.press("enter")
        await pilot.pause()

        assert chat_input.value == typed
        # The validation error is committed to scrollback (not the widget tree).
        assert "does not support images" in committed_scrollback(app)
