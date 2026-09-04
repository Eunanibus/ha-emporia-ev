"""Shared fixtures for the bundled client library tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiohttp
import pytest


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    """A ClientSession backed by ThreadedResolver.

    The default resolver (c-ares via pycares) starts a daemon background thread
    (``_run_safe_shutdown_loop``) on the first DNS look-up. That thread lingers
    past the pytest-homeassistant-custom-component ``verify_cleanup`` fixture's
    thread check and fails it. aiohttp.ThreadedResolver uses stdlib
    getaddrinfo instead and never starts a background thread.
    """
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(connector=connector) as client:
        yield client
