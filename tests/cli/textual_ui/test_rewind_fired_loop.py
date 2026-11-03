from __future__ import annotations

from unittest.mock import PropertyMock, patch

import pytest

from vibe.app_server.models import (
    PublicEntryGenerationStatus,
    PublicMessageEntry,
    TextContentBlock,
)
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.fired_loop import SCHEDULED_LOOP_DISPLAY_BLOCK
from vibe.user_content import UserDisplayContent


def _user_entry(entry_id: str, text: str, *, fired: bool = False) -> PublicMessageEntry:
    return PublicMessageEntry(
        id=entry_id,
        session_id="s",
        turn_id="t",
        created_at=1,
        updated_at=1,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role="user",
        content=[TextContentBlock(text=text)],
        user_display_content=(
            UserDisplayContent(
                version="1",
                host="test",
                content=[{"type": SCHEDULED_LOOP_DISPLAY_BLOCK, "loopId": "abc"}],
            )
            if fired
            else None
        ),
    )


@pytest.mark.asyncio
async def test_rewind_skips_prompts_a_scheduled_loop_sent(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test():
        with patch.object(
            type(vibe_app.app_server),
            "history",
            new_callable=PropertyMock,
            return_value=[
                _user_entry("u0", "typed"),
                _user_entry("u1", "fired", fired=True),
            ],
        ):
            assert [entry.id for entry in vibe_app._get_rewind_user_entries()] == ["u0"]
