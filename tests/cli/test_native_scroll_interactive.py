"""Phase 7 native-scroll routing: interactive outcomes and navigation.

Covers bottom-app outcome commits (approval allow/deny, model/theme selection)
and the index-based rewind redesign. Integration tests assert committed terminal
scrollback and live-region behavior, and that durable content does not depend on
hidden ``#messages`` widgets.
"""

from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock

from pydantic import BaseModel
import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.native_scroll.app_surfaces import (
    render_approval_outcome,
    render_rewind_outcome,
)
from vibe.cli.textual_ui.scrollback_committer import ScrollbackCommitter
from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp
from vibe.cli.textual_ui.widgets.connector_auth_app import ConnectorAuthApp
from vibe.cli.textual_ui.widgets.model_picker import ModelPickerApp
from vibe.cli.textual_ui.widgets.rewind_app import RewindApp
from vibe.cli.textual_ui.widgets.theme_picker import ThemePickerApp
from vibe.cli.textual_ui.widgets.thinking_picker import ThinkingPickerApp
from vibe.core.rewind import RewindError
from vibe.core.types import LLMMessage, Role


class _Args(BaseModel):
    pass


def _committer() -> ScrollbackCommitter:
    return ScrollbackCommitter(width_getter=lambda: 80, color_system=None)


def _lines(committer: ScrollbackCommitter) -> str:
    return "\n".join(committer.drain_lines())


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(committer: ScrollbackCommitter) -> str:
    return _ANSI_RE.sub("", "\n".join(committer.drain_lines()))


# -- pure renderers --------------------------------------------------------


def test_render_approval_outcome_approved_and_scope() -> None:
    committer = _committer()
    committer._enqueue(render_approval_outcome(tool_name="bash", approved=True))
    assert "Approved bash" in _lines(committer)

    committer._enqueue(
        render_approval_outcome(
            tool_name="edit", approved=True, scope="always for this tool"
        )
    )
    text = _lines(committer)
    assert "Approved edit" in text
    assert "always for this tool" in text


def test_render_approval_outcome_denied() -> None:
    committer = _committer()
    committer._enqueue(render_approval_outcome(tool_name="bash", approved=False))
    assert "Denied bash" in _lines(committer)


def test_render_rewind_outcome_has_preview_count_and_files() -> None:
    committer = _committer()
    committer._enqueue(
        render_rewind_outcome("fix the bug", restored_files=True, discarded=3)
    )
    text = _lines(committer)
    assert "Rewound to: fix the bug" in text
    assert "3 later messages discarded" in text
    assert "files" in text and "restored" in text


def test_render_rewind_outcome_singular_and_kept() -> None:
    committer = _committer()
    committer._enqueue(render_rewind_outcome("redo", restored_files=False, discarded=1))
    text = _lines(committer)
    assert "1 later message discarded" in text
    assert "files kept" in text


# -- committer commit methods ----------------------------------------------


def test_commit_approval_and_rewind() -> None:
    committer = _committer()
    committer.commit_approval(tool_name="bash", approved=True, scope="always, saved")
    text = _lines(committer)
    assert "Approved bash" in text
    assert "always, saved" in text

    committer.commit_rewind("hello", restored_files=True, discarded=2)
    assert "Rewound to: hello" in _lines(committer)


# -- integration: bottom-app outcomes --------------------------------------


@pytest.mark.asyncio
async def test_approval_granted_commits_outcome() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()  # clear startup header

        app._pending_approval = asyncio.Future()
        await app.on_approval_app_approval_granted(
            ApprovalApp.ApprovalGranted(tool_name="bash", tool_args=_Args())
        )

        assert app._pending_approval.done()
        assert "Approved bash" in _plain(app._committer)
        assert len(list(app._messages_area.children)) == 0


@pytest.mark.asyncio
async def test_approval_rejected_commits_denied() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()

        app._pending_approval = asyncio.Future()
        await app.on_approval_app_approval_rejected(
            ApprovalApp.ApprovalRejected(tool_name="edit", tool_args=_Args())
        )

        assert "Denied edit" in _plain(app._committer)
        assert len(list(app._messages_area.children)) == 0


@pytest.mark.asyncio
async def test_approval_always_tool_commits_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()
        monkeypatch.setattr(app.agent_loop, "approve_always", lambda *a, **k: None)

        app._pending_approval = asyncio.Future()
        await app.on_approval_app_approval_granted_always_tool(
            ApprovalApp.ApprovalGrantedAlwaysTool(
                tool_name="bash", tool_args=_Args(), required_permissions=[]
            )
        )

        text = _plain(app._committer)
        assert "Approved bash" in text
        assert "always for this tool" in text


@pytest.mark.asyncio
async def test_approval_always_permanent_commits_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()
        monkeypatch.setattr(app.agent_loop, "approve_always", lambda *a, **k: None)

        app._pending_approval = asyncio.Future()
        await app.on_approval_app_approval_granted_always_permanent(
            ApprovalApp.ApprovalGrantedAlwaysPermanent(
                tool_name="edit", tool_args=_Args(), required_permissions=[]
            )
        )

        text = _plain(app._committer)
        assert "Approved edit" in text
        assert "always, saved" in text


def _neutralize_reload(app: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Let `_reload_config` run its single notice commit without heavy IO."""
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.VibeConfig.load",
        classmethod(lambda cls: app.config),  # type: ignore[attr-defined]
    )
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.VibeConfig.save_updates",
        staticmethod(lambda *a, **k: None),
    )
    monkeypatch.setattr(
        app.agent_loop,  # type: ignore[attr-defined]
        "reload_with_initial_messages",
        AsyncMock(),
    )
    monkeypatch.setattr(app, "_resolve_plan", AsyncMock())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_model_selection_commits_single_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()
        _neutralize_reload(app, monkeypatch)

        await app.on_model_picker_app_model_selected(
            ModelPickerApp.ModelSelected(alias="big[high]")
        )
        await pilot.pause()

        text = _plain(app._committer)
        # Exactly one outcome line: the specific one, not the generic reload notice.
        assert "Model set to" in text
        assert "Configuration reloaded" not in text


@pytest.mark.asyncio
async def test_thinking_selection_commits_single_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()
        _neutralize_reload(app, monkeypatch)
        # set_thinking writes the TOML; stub it on the class (pydantic instances
        # reject attribute assignment) so the test isolates the outcome commit.
        monkeypatch.setattr(type(app.config), "set_thinking", lambda self, level: None)

        await app.on_thinking_picker_app_thinking_selected(
            ThinkingPickerApp.ThinkingSelected(level="high")
        )
        await pilot.pause()

        text = _plain(app._committer)
        assert "Thinking level set to" in text
        assert "Configuration reloaded" not in text


@pytest.mark.asyncio
async def test_config_save_commits_single_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()
        _neutralize_reload(app, monkeypatch)

        await app._handle_config_settings_closed({"some_setting": "value"})
        await pilot.pause()

        text = _plain(app._committer)
        assert "Configuration updated." in text
        assert "Configuration reloaded" not in text


@pytest.mark.asyncio
async def test_theme_selection_commits_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()

        monkeypatch.setattr(
            "vibe.cli.textual_ui.app.VibeConfig.save_updates",
            staticmethod(lambda *a, **k: None),
        )

        theme = app.config.theme
        await app.on_theme_picker_app_theme_selected(
            ThemePickerApp.ThemeSelected(theme=theme)
        )
        await pilot.pause()

        # Theme does not call _reload_config, so its single line is the only one.
        text = _plain(app._committer)
        assert "Theme set to" in text
        assert "Configuration reloaded" not in text


@pytest.mark.asyncio
async def test_connector_auth_refresh_commits_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        app._committer.drain_lines()
        monkeypatch.setattr(app.agent_loop, "refresh_system_prompt", AsyncMock())
        monkeypatch.setattr(app, "_show_mcp", AsyncMock())

        await app.on_connector_auth_app_connector_auth_closed(
            ConnectorAuthApp.ConnectorAuthClosed(
                refreshed=True, connector_name="github"
            )
        )
        await pilot.pause()

        # UserCommandMessage renders Markdown, so the inline-code backticks around
        # the connector name are not present in the rendered text.
        assert "Connector github authenticated." in _plain(app._committer)


# -- integration: index-based rewind ---------------------------------------


def _seed_two_turns(app: object) -> tuple[int, int]:
    messages = app.agent_loop.messages  # type: ignore[attr-defined]
    first_index = len(messages)
    messages.append(LLMMessage(role=Role.user, content="first prompt"))
    messages.append(LLMMessage(role=Role.assistant, content="first reply"))
    second_index = len(messages)
    messages.append(LLMMessage(role=Role.user, content="second prompt"))
    messages.append(LLMMessage(role=Role.assistant, content="second reply"))
    return first_index, second_index


@pytest.mark.asyncio
async def test_rewind_navigates_by_message_index() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        first_index, second_index = _seed_two_turns(app)

        app.action_rewind_prev()
        await pilot.pause()
        await pilot.pause()

        # Newest user message selected by index; live RewindApp panel is shown.
        assert app._rewind_mode is True
        assert app._rewind_target_index == second_index
        rewind_app = app.query_one(RewindApp)
        assert "second prompt" in rewind_app._message_preview

        # Step to the older message, then back to the newer one.
        app.action_rewind_prev()
        await pilot.pause()
        await pilot.pause()
        assert app._rewind_target_index == first_index

        app.action_rewind_next()
        await pilot.pause()
        await pilot.pause()
        assert app._rewind_target_index == second_index

        # No hidden transcript widgets are involved in selection.
        assert len(list(app._messages_area.children)) == 0


@pytest.mark.asyncio
async def test_execute_rewind_commits_marker_and_prefills_input() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        _first_index, second_index = _seed_two_turns(app)
        app._committer.drain_lines()

        app.agent_loop.rewind_manager.rewind_to_message = AsyncMock(  # type: ignore[method-assign]
            return_value=("second prompt", [])
        )

        app.action_rewind_prev()
        await pilot.pause()
        await pilot.pause()
        assert app._rewind_target_index == second_index

        await app.on_rewind_app_rewind_without_restore(RewindApp.RewindWithoutRestore())
        await pilot.pause()

        text = _plain(app._committer)
        assert "Rewound to: second prompt" in text
        # Rewinding to the last user turn discards only its assistant reply; the
        # selected user message is pulled back into the input, not discarded.
        assert "1 later message discarded" in text
        assert "files kept" in text
        assert app._rewind_mode is False
        assert app._rewind_target_index is None
        assert app._chat_input_container is not None
        assert app._chat_input_container.value == "second prompt"


@pytest.mark.asyncio
async def test_rewind_error_is_transient_not_durable() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._committer is not None
        _seed_two_turns(app)
        app._committer.drain_lines()

        async def _raise(*_args: object, **_kwargs: object) -> tuple[str, list[str]]:
            raise RewindError("invalid index")

        app.agent_loop.rewind_manager.rewind_to_message = _raise  # type: ignore[method-assign]
        notices: list[str] = []
        app.notify = lambda message, **_k: notices.append(str(message))  # type: ignore[assignment,method-assign]

        app.action_rewind_prev()
        await pilot.pause()
        await pilot.pause()

        await app.on_rewind_app_rewind_without_restore(RewindApp.RewindWithoutRestore())
        await pilot.pause()

        # The error is a transient toast, never durable scrollback; rewind state
        # is left intact so the user can retry.
        assert any("invalid index" in n for n in notices)
        assert app._committer.has_pending is False
        assert app._rewind_mode is True
