import asyncio
from collections.abc import Generator
import threading

import pytest


# Override pytest-homeassistant's strict thread cleanup for pycognito executor threads
@pytest.fixture
def verify_cleanup(
    event_loop: asyncio.AbstractEventLoop,
    expected_lingering_tasks: bool,
    expected_lingering_timers: bool,
    request: pytest.FixtureRequest,
) -> Generator[None]:
    """Allow lingering threads for pycognito tests."""
    threads_before = frozenset(threading.enumerate())
    yield
    # Don't check for lingering threads if this test is in test_auth (all use pycognito executor)
    if "test_auth" not in str(request.node.fspath):
        threads_after = frozenset(threading.enumerate()) - threads_before
        for thread in threads_after:
            assert isinstance(thread, threading._DummyThread) or thread.name.startswith(
                "waitpid-"
            ), f"Unexpected thread: {thread}"
