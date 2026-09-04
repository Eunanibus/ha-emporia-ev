"""Tests for the hosted-UI OAuth helpers.

Home Assistant's OAuth helper owns URL building, ``state`` and the code
exchange, so what is tested here is only what this module still contributes.
"""

from __future__ import annotations

import base64
import hashlib
import json

import aiohttp
from aioresponses import aioresponses
import pytest

from custom_components.emporia_ev.client.errors import EmporiaError
from custom_components.emporia_ev.client.oauth import (
    PROVIDER_APPLE,
    PROVIDER_GOOGLE,
    PROVIDERS,
    REVOKE_URL,
    async_revoke,
    email_from_id_token,
    generate_pkce,
    menu_id_for_provider,
)


def _id_token(payload_segment: str) -> str:
    return f"header.{payload_segment}.signature"


def _encode(claims: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")


def test_generate_pkce_challenge_is_s256_of_verifier() -> None:
    """A challenge that is not the hash of the verifier disables PKCE silently."""
    pkce = generate_pkce()
    digest = hashlib.sha256(pkce.verifier.encode()).digest()
    assert pkce.challenge == base64.urlsafe_b64encode(digest).decode().rstrip("=")


def test_generate_pkce_is_unpadded_base64url() -> None:
    """Cognito rejects '=' padding and the '+' and '/' of standard base64."""
    pkce = generate_pkce()
    for value in (pkce.verifier, pkce.challenge):
        assert "=" not in value
        assert "+" not in value
        assert "/" not in value
    # RFC 7636 requires a verifier of 43 to 128 characters.
    assert 43 <= len(pkce.verifier) <= 128


def test_generate_pkce_is_fresh_each_call() -> None:
    """Reusing a verifier across flows would let one flow redeem another's code."""
    assert generate_pkce().verifier != generate_pkce().verifier


def test_provider_table_maps_menu_ids_to_cognito_values() -> None:
    assert PROVIDERS["google"][0] == PROVIDER_GOOGLE
    assert PROVIDERS["apple"][0] == PROVIDER_APPLE
    assert PROVIDERS["google"][1] == "Google"
    assert PROVIDERS["apple"][1] == "Apple"


@pytest.mark.parametrize("menu_id", list(PROVIDERS))
def test_menu_id_for_provider_round_trips(menu_id: str) -> None:
    identity_provider, _display = PROVIDERS[menu_id]
    assert menu_id_for_provider(identity_provider) == menu_id


def test_menu_id_for_provider_falls_back_for_unknown_value() -> None:
    """A stored value from a future version must not KeyError mid-reauth."""
    assert menu_id_for_provider("SignInWithSomethingNew") == "google"
    assert menu_id_for_provider("") == "google"


def test_email_from_id_token_reads_the_claim() -> None:
    token = _id_token(_encode({"email": "user@example.com", "email_verified": False}))
    assert email_from_id_token(token) == "user@example.com"


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(_id_token(_encode({"sub": "abc"})), id="claim absent"),
        pytest.param(_id_token(_encode({"email": 42})), id="claim not a string"),
        pytest.param(_id_token(_encode(["not", "a", "dict"])), id="payload not an object"),
        pytest.param(_id_token("!!!not-base64!!!"), id="payload not decodable"),
        pytest.param("only-one-segment", id="not a jwt"),
        pytest.param("", id="empty"),
    ],
)
def test_email_from_id_token_returns_none_rather_than_raising(token: str) -> None:
    """Apple may omit the claim, and a crash here would block setup entirely."""
    assert email_from_id_token(token) is None


async def test_async_revoke_posts_the_token(session: aiohttp.ClientSession) -> None:
    with aioresponses() as mocked:
        mocked.post(REVOKE_URL, status=200)
        await async_revoke(session, "refresh-abc")
        request = next(iter(mocked.requests.values()))[0]
        assert request.kwargs["data"]["token"] == "refresh-abc"
        assert request.kwargs["data"]["client_id"]


async def test_async_revoke_raises_on_error_status(session: aiohttp.ClientSession) -> None:
    """The call site treats this as non-fatal, but it must still be reported."""
    with aioresponses() as mocked:
        mocked.post(REVOKE_URL, status=400)
        with pytest.raises(EmporiaError):
            await async_revoke(session, "refresh-abc")
