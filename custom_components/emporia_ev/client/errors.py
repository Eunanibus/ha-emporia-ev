"""Typed exceptions for the Emporia client library."""

from __future__ import annotations


class EmporiaError(Exception):
    """Base class for all Emporia client errors."""


class AuthError(EmporiaError):
    """Authentication or token refresh failed."""


class RateLimitError(EmporiaError):
    """The Emporia API returned HTTP 429.

    ``retry_after`` carries the parsed ``Retry-After`` header value in seconds
    when the server provided one, otherwise ``None``.
    """

    def __init__(self, message: str = "", *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class EmporiaConnectionError(EmporiaError):
    """A transport-level failure (connection error or timeout)."""
