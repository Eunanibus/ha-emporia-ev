import time
from unittest.mock import MagicMock, patch

import aiohttp
from aioresponses import aioresponses
import pytest

from custom_components.emporia_ev.client.auth import COGNITO_URL, EmporiaAuth
from custom_components.emporia_ev.client.errors import AuthError


@pytest.mark.asyncio
async def test_login_stores_tokens_via_pycognito() -> None:
    fake = MagicMock()
    fake.id_token = "ID_TOKEN"
    fake.access_token = "ACCESS_TOKEN"
    fake.refresh_token = "REFRESH_TOKEN"
    async with aiohttp.ClientSession() as session:
        auth = EmporiaAuth(session, username="u@example.com", password="pw")
        with patch("custom_components.emporia_ev.client.auth.Cognito", return_value=fake):
            await auth.async_login()
        assert auth.refresh_token == "REFRESH_TOKEN"
        assert auth.auth_headers() == {"authtoken": "ID_TOKEN"}


@pytest.mark.asyncio
async def test_login_bad_credentials_raises_autherror() -> None:
    fake = MagicMock()
    fake.authenticate.side_effect = Exception("NotAuthorizedException")
    async with aiohttp.ClientSession() as session:
        auth = EmporiaAuth(session, username="u@example.com", password="bad")
        with (
            patch("custom_components.emporia_ev.client.auth.Cognito", return_value=fake),
            pytest.raises(AuthError),
        ):
            await auth.async_login()


@pytest.mark.asyncio
async def test_refresh_exchanges_refresh_token_for_new_access_token() -> None:
    body = {
        "AuthenticationResult": {
            "IdToken": "NEW_ID",
            "AccessToken": "NEW_ACCESS",
            "ExpiresIn": 3600,
            "TokenType": "Bearer",
        }
    }
    async with aiohttp.ClientSession() as session:
        auth = EmporiaAuth(session, refresh_token="RT")
        with aioresponses() as mocked:
            mocked.post(COGNITO_URL, status=200, payload=body)
            await auth.async_refresh()
        assert auth.auth_headers() == {"authtoken": "NEW_ID"}
        token = await auth.async_get_access_token()
        assert token == "NEW_ACCESS"


@pytest.mark.asyncio
async def test_refresh_rejected_raises_autherror() -> None:
    async with aiohttp.ClientSession() as session:
        auth = EmporiaAuth(session, refresh_token="BAD_RT")
        with aioresponses() as mocked:
            mocked.post(COGNITO_URL, status=400, payload={"__type": "NotAuthorizedException"})
            with pytest.raises(AuthError):
                await auth.async_refresh()


@pytest.mark.asyncio
async def test_get_access_token_refreshes_when_expired() -> None:
    body = {
        "AuthenticationResult": {
            "IdToken": "REFRESHED_ID",
            "AccessToken": "REFRESHED_ACCESS",
            "ExpiresIn": 3600,
            "TokenType": "Bearer",
        }
    }
    async with aiohttp.ClientSession() as session:
        auth = EmporiaAuth(session, refresh_token="RT")
        auth._access_token = "STALE"
        auth._expires_at = time.time() - 1
        with aioresponses() as mocked:
            mocked.post(COGNITO_URL, status=200, payload=body)
            token = await auth.async_get_access_token()
        assert token == "REFRESHED_ACCESS"
