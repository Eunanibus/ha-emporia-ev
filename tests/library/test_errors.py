from custom_components.emporia_ev.client.errors import (
    AuthError,
    EmporiaConnectionError,
    EmporiaError,
    RateLimitError,
)


def test_all_errors_subclass_base() -> None:
    for exc in (AuthError, RateLimitError, EmporiaConnectionError):
        assert issubclass(exc, EmporiaError)


def test_rate_limit_error_carries_retry_after() -> None:
    err = RateLimitError("slow down", retry_after=30.0)
    assert err.retry_after == 30.0
    assert str(err) == "slow down"


def test_rate_limit_error_retry_after_defaults_to_none() -> None:
    err = RateLimitError("slow down")
    assert err.retry_after is None


def test_base_error_is_exception() -> None:
    assert issubclass(EmporiaError, Exception)
