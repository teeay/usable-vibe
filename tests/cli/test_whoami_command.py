from __future__ import annotations

import asyncio

import pytest

from tests.conftest import build_test_vibe_app, committed_scrollback
from tests.stubs.fake_account_gateway import FakeAccountGateway
from tests.stubs.fake_identity_gateway import FakeIdentityGateway
from vibe.app_server._identity import IdentityResult
from vibe.cli.textual_ui.widgets.loading import LoadingWidget


@pytest.mark.asyncio
async def test_whoami_command_shows_identity() -> None:
    app = build_test_vibe_app(
        identity_gateway=FakeIdentityGateway(
            IdentityResult.model_validate({
                "id": "user-1",
                "email": "ada@example.com",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "workspace": {"id": "ws-1", "name": "Analytical Engine"},
                "organization": {"id": "org-1", "name": "Mistral"},
            })
        )
    )

    async with app.run_test() as pilot:
        handled = await app._handle_command("/whoami")
        await pilot.pause()
        text = committed_scrollback(app)

    assert handled is True
    assert "Ada Lovelace" in text
    assert "ada@example.com" in text
    assert "Analytical Engine" in text
    assert "Mistral" in text


@pytest.mark.asyncio
async def test_whoami_command_reports_missing_identity() -> None:
    app = build_test_vibe_app(identity_gateway=FakeIdentityGateway(unavailable=True))

    async with app.run_test() as pilot:
        handled = await app._handle_command("/whoami")
        await pilot.pause()
        text = committed_scrollback(app)

    assert handled is True
    assert "No identity information" in text
    assert not list(app.query(LoadingWidget))


@pytest.mark.asyncio
async def test_whoami_command_shows_identity_when_account_fails() -> None:
    app = build_test_vibe_app(
        identity_gateway=FakeIdentityGateway(
            IdentityResult.model_validate({
                "id": "user-1",
                "email": "ada@example.com",
                "first_name": "Ada",
                "last_name": "Lovelace",
            })
        ),
        account_gateway=FakeAccountGateway(unavailable=True),
    )

    async with app.run_test() as pilot:
        handled = await app._handle_command("/whoami")
        await pilot.pause()
        text = committed_scrollback(app)

    assert handled is True
    assert "Ada Lovelace" in text
    assert "ada@example.com" in text
    assert "Plan" not in text


@pytest.mark.asyncio
async def test_whoami_command_omits_name_line_when_name_falls_back_to_email() -> None:
    app = build_test_vibe_app(
        identity_gateway=FakeIdentityGateway(
            IdentityResult.model_validate({
                "id": "user-1",
                "email": "ada@example.com",
                "last_name": "Lovelace",
            })
        )
    )

    async with app.run_test() as pilot:
        handled = await app._handle_command("/whoami")
        await pilot.pause()
        text = committed_scrollback(app)

    assert handled is True
    assert "Name" not in text
    assert "ada@example.com" in text


class _GatedIdentityGateway:
    def __init__(self, result: IdentityResult) -> None:
        self._result = result
        self.release = asyncio.Event()

    async def read(self, *, base_url: str, api_key: str) -> IdentityResult:
        await self.release.wait()
        return self._result


@pytest.mark.asyncio
async def test_whoami_command_shows_spinner_while_fetching_identity() -> None:
    gateway = _GatedIdentityGateway(
        IdentityResult.model_validate({
            "id": "user-1",
            "email": "ada@example.com",
            "first_name": "Ada",
        })
    )
    app = build_test_vibe_app(identity_gateway=gateway)

    async with app.run_test() as pilot:
        task = asyncio.create_task(app._handle_command("/whoami"))
        await pilot.pause()
        assert app.query_one(LoadingWidget) is not None

        gateway.release.set()
        await pilot.pause()
        await task
        await pilot.pause()

        assert not list(app.query(LoadingWidget))
        assert "Ada" in committed_scrollback(app)
