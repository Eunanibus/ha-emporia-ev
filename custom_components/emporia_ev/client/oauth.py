"""Authorization-code sign-in against Emporia's Cognito hosted UI.

Emporia fronts Google and Apple sign-in through a hosted UI at
auth.emporiaenergy.com. A federated user has no password in the pool and its
username is ``Google_<id>`` rather than an email, so SRP cannot authenticate
them and the authorization-code flow is the only path.

Home Assistant's OAuth helper owns the browser hand-off, the signed ``state``
and the code exchange, so this module holds only the parts the helper does not:
the provider identifiers, PKCE generation, the id-token email claim, and
refresh-token revocation.

Setup needs only the refresh token. ``EmporiaAuth`` refreshes from it and the
resulting id token authenticates to the product API unchanged.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import secrets
from typing import Any

import aiohttp

from .auth import CLIENT_ID
from .errors import EmporiaError

HOSTED_UI = "https://auth.emporiaenergy.com"
AUTHORIZE_URL = f"{HOSTED_UI}/oauth2/authorize"
TOKEN_URL = f"{HOSTED_UI}/oauth2/token"
REVOKE_URL = f"{HOSTED_UI}/oauth2/revoke"

SCOPE = "openid email"

PROVIDER_GOOGLE = "Google"
PROVIDER_APPLE = "SignInWithApple"

# Menu id -> (Cognito identity_provider, display name). The single place the
# three naming forms for each provider are related.
PROVIDERS: dict[str, tuple[str, str]] = {
    "apple": (PROVIDER_APPLE, "Apple"),
    "google": (PROVIDER_GOOGLE, "Google"),
}

# Inverse of PROVIDERS, built from it so the two cannot disagree.
_PROVIDER_TO_MENU: dict[str, str] = {
    identity_provider: menu_id for menu_id, (identity_provider, _) in PROVIDERS.items()
}


def menu_id_for_provider(identity_provider: str) -> str:
    """Return the menu id for a stored Cognito ``identity_provider`` value.

    Falls back to ``"google"`` for an unrecognised value, so a config entry
    written by a future version cannot crash re-authentication with a KeyError.
    """
    return _PROVIDER_TO_MENU.get(identity_provider, "google")


@dataclass(frozen=True)
class PkceChallenge:
    """A PKCE verifier and its S256 challenge."""

    verifier: str
    challenge: str


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def generate_pkce() -> PkceChallenge:
    """Return a fresh PKCE verifier and its S256 challenge.

    Emporia's app client is public and holds no secret, so without PKCE a
    leaked authorization code would be redeemable by anyone.
    """
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return PkceChallenge(verifier=verifier, challenge=challenge)


def email_from_id_token(id_token: str) -> str | None:
    """Read the email claim without verifying the signature.

    OIDC Core 3.1.3.7 permits skipping signature validation for a token
    received directly from the token endpoint over TLS, which is the case here.
    The value is used only for the username field, never for an authorization
    decision. Returns None when the claim is absent, which happens for
    providers whose attribute mapping omits it.
    """
    parts = id_token.split(".")
    if len(parts) < 2:
        return None
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims: Any = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return None
    if not isinstance(claims, dict):
        return None
    email = claims.get("email")
    return email if isinstance(email, str) else None


async def async_revoke(session: aiohttp.ClientSession, refresh_token: str) -> None:
    """Revoke a refresh token.

    Raises EmporiaError on a non-200. Callers treat any failure as non-fatal,
    so the call site catches transport errors too.
    """
    payload = {"token": refresh_token, "client_id": CLIENT_ID}
    async with session.post(REVOKE_URL, data=payload) as resp:
        if resp.status != 200:
            raise EmporiaError(f"token revoke rejected: {resp.status}")
