import asyncio
import time
from unittest.mock import MagicMock, patch

import aiohttp
from aioresponses import aioresponses
import pytest

from custom_components.emporia_ev.client.auth import COGNITO_URL, EmporiaAuth
from custom_components.emporia_ev.client.errors import AuthError


def _session() -> aiohttp.ClientSession:
    """Return a ClientSession backed by ThreadedResolver.

    The default resolver (c-ares via pycares) starts a daemon background
    thread (*_run_safe_shutdown_loop*) on the first DNS look-up.  That
    thread lingers past the pytest-homeassistant-custom-component
    ``verify_cleanup`` fixture's thread check, causing a false positive.
    aiohttp.ThreadedResolver uses Python's stdlib getaddrinfo instead and
    never starts a background thread, so the cleanup check stays clean.
    """
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    return aiohttp.ClientSession(connector=connector)


async def _inline_executor(executor, func, *args):
    """Run *func* synchronously in-coroutine so no real thread is spawned.

    ``async_login`` offloads pycognito's SRP handshake to the default
    ThreadPoolExecutor via ``run_in_executor``.  In tests we patch this
    helper onto the running loop so the callable executes on the event
    loop itself — no worker thread, no lingering thread after the test.
    """
    future = asyncio.get_running_loop().create_future()
    try:
        future.set_result(func(*args))
    except Exception as exc:
        future.set_exception(exc)
    return await future


@pytest.mark.asyncio
async def test_login_stores_tokens_via_pycognito() -> None:
    fake = MagicMock()
    fake.id_token = "ID_TOKEN"
    fake.access_token = "ACCESS_TOKEN"
    fake.refresh_token = "REFRESH_TOKEN"
    async with _session() as session:
        auth = EmporiaAuth(session, username="u@example.com", password="pw")
        with (
            patch("custom_components.emporia_ev.client.auth.Cognito", return_value=fake),
            patch.object(asyncio.get_running_loop(), "run_in_executor", _inline_executor),
        ):
            await auth.async_login()
        assert auth.refresh_token == "REFRESH_TOKEN"
        assert auth.auth_headers() == {"authtoken": "ID_TOKEN"}


@pytest.mark.asyncio
async def test_login_bad_credentials_raises_autherror() -> None:
    fake = MagicMock()
    fake.authenticate.side_effect = Exception("NotAuthorizedException")
    async with _session() as session:
        auth = EmporiaAuth(session, username="u@example.com", password="bad")
        with (
            patch("custom_components.emporia_ev.client.auth.Cognito", return_value=fake),
            patch.object(asyncio.get_running_loop(), "run_in_executor", _inline_executor),
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
    async with _session() as session:
        auth = EmporiaAuth(session, refresh_token="RT")
        with aioresponses() as mocked:
            mocked.post(COGNITO_URL, status=200, payload=body)
            await auth.async_refresh()
        assert auth.auth_headers() == {"authtoken": "NEW_ID"}
        token = await auth.async_get_access_token()
        assert token == "NEW_ACCESS"


@pytest.mark.asyncio
async def test_refresh_parses_cognito_amz_json_mimetype() -> None:
    # Regression: Cognito replies with Content-Type application/x-amz-json-1.1.
    # aiohttp's .json() rejects non-application/json unless content_type=None,
    # which previously surfaced as a confusing "token refresh transport error".
    body = {
        "AuthenticationResult": {
            "IdToken": "AMZ_ID",
            "AccessToken": "AMZ_ACCESS",
            "ExpiresIn": 3600,
            "TokenType": "Bearer",
        }
    }
    async with _session() as session:
        auth = EmporiaAuth(session, refresh_token="RT")
        with aioresponses() as mocked:
            mocked.post(
                COGNITO_URL,
                status=200,
                payload=body,
                content_type="application/x-amz-json-1.1",
            )
            await auth.async_refresh()
        assert auth.auth_headers() == {"authtoken": "AMZ_ID"}


@pytest.mark.asyncio
async def test_refresh_rejected_raises_autherror() -> None:
    async with _session() as session:
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
    async with _session() as session:
        auth = EmporiaAuth(session, refresh_token="RT")
        auth._access_token = "STALE"
        auth._expires_at = time.time() - 1
        with aioresponses() as mocked:
            mocked.post(COGNITO_URL, status=200, payload=body)
            token = await auth.async_get_access_token()
        assert token == "REFRESHED_ACCESS"
