"""Cognito auth for the Emporia client.

SRP login is delegated to ``pycognito`` (documented fallback — see the design's
Cognito note); token refresh is a plain ``InitiateAuth`` POST over the injected
aiohttp session, so the hot path (refresh on every poll cycle) never touches
boto3. POOL_ID / CLIENT_ID / REGION are pinned by the capture task.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp
from pycognito import Cognito

from .errors import AuthError, EmporiaConnectionError

REGION = "us-east-2"
POOL_ID = "us-east-2_ghlOXVLi1"
CLIENT_ID = "4qte47jbstod8apnfic0bunmrq"
COGNITO_URL = f"https://cognito-idp.{REGION}.amazonaws.com/"

_REFRESH_SKEW_SECONDS = 60.0


class EmporiaAuth:
    """Owns Emporia's Cognito tokens and hands out a valid access token."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        username: str | None = None,
        password: str | None = None,
        refresh_token: str | None = None,
        pool_id: str = POOL_ID,
        client_id: str = CLIENT_ID,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._pool_id = pool_id
        self._client_id = client_id
        self._refresh_token = refresh_token
        self._id_token: str | None = None
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    @property
    def refresh_token(self) -> str | None:
        """The current refresh token (for HA config-entry persistence)."""
        return self._refresh_token

    def auth_headers(self) -> dict[str, str]:
        if not self._id_token:
            raise AuthError("no id token available; call async_get_access_token first")
        return {"authtoken": self._id_token}

    async def async_login(self) -> None:
        if not self._username or not self._password:
            raise AuthError("username and password are required for login")

        def _do_login() -> Cognito:
            user = Cognito(self._pool_id, self._client_id, username=self._username)
            user.authenticate(password=self._password)
            return user

        try:
            user = await asyncio.get_running_loop().run_in_executor(None, _do_login)
        except Exception as err:
            raise AuthError(f"login failed: {err}") from err
        self._id_token = user.id_token
        self._access_token = user.access_token
        self._refresh_token = user.refresh_token
        self._expires_at = time.time() + 3600.0

    async def async_refresh(self) -> None:
        if not self._refresh_token:
            raise AuthError("no refresh token available to refresh")
        payload = {
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "ClientId": self._client_id,
            "AuthParameters": {"REFRESH_TOKEN": self._refresh_token},
        }
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        }
        try:
            async with self._session.post(COGNITO_URL, json=payload, headers=headers) as resp:
                # Cognito replies with Content-Type application/x-amz-json-1.1;
                # aiohttp's .json() rejects non-application/json unless we opt out
                # of the content-type check.
                body: dict[str, Any] = await resp.json(content_type=None)
                if resp.status != 200:
                    raise AuthError(f"token refresh rejected: {body.get('__type', resp.status)}")
        except aiohttp.ClientError as err:
            raise EmporiaConnectionError(f"token refresh transport error: {err}") from err
        result = body.get("AuthenticationResult") or {}
        self._id_token = result.get("IdToken")
        self._access_token = result.get("AccessToken")
        if not self._access_token or not self._id_token:
            raise AuthError("token refresh returned no tokens")
        self._expires_at = time.time() + float(result.get("ExpiresIn", 3600))

    async def async_get_access_token(self) -> str:
        if self._access_token and time.time() < self._expires_at - _REFRESH_SKEW_SECONDS:
            return self._access_token
        if self._refresh_token:
            await self.async_refresh()
        else:
            await self.async_login()
        assert self._access_token is not None
        return self._access_token
