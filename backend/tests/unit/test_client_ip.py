"""Which client IP login rate limiting sees: forwarded headers are believed only as far as
the deployment says they can be."""

import pytest
from starlette.requests import Request

from app.auth.client_ip import client_ip
from app.config import Settings

URL = "postgresql+psycopg://u:p@h:5432/d"
SECRET = "s" * 40


def _request(headers: dict[str, str], peer: str | None = "10.0.0.7") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 1234) if peer else None,
        }
    )


def _settings(**values: object) -> Settings:
    return Settings(database_url=URL, **values)  # type: ignore[arg-type]


def test_by_default_only_the_peer_counts() -> None:
    request = _request({"X-Forwarded-For": "198.51.100.1", "X-Veyl-Client-IP": "198.51.100.2"})
    assert client_ip(request, _settings()) == "10.0.0.7"


def test_the_proxy_may_name_the_visitor_with_the_shared_secret() -> None:
    request = _request({"X-Veyl-Proxy-Secret": SECRET, "X-Veyl-Client-IP": "203.0.113.5"})
    assert client_ip(request, _settings(proxy_shared_secret=SECRET)) == "203.0.113.5"


@pytest.mark.parametrize("sent", ["", "wrong", SECRET[:-1], SECRET + "x"])
def test_without_the_right_secret_a_named_ip_is_ignored(sent: str) -> None:
    request = _request({"X-Veyl-Proxy-Secret": sent, "X-Veyl-Client-IP": "203.0.113.5"})
    assert client_ip(request, _settings(proxy_shared_secret=SECRET)) == "10.0.0.7"


def test_a_named_ip_that_is_not_an_address_falls_back_to_the_peer() -> None:
    request = _request({"X-Veyl-Proxy-Secret": SECRET, "X-Veyl-Client-IP": "ip:evil"})
    assert client_ip(request, _settings(proxy_shared_secret=SECRET)) == "10.0.0.7"


def test_forwarded_for_is_read_from_the_right() -> None:
    # The client forged the first two entries; the trusted proxy appended the third.
    request = _request({"X-Forwarded-For": "1.1.1.1, 2.2.2.2, 198.51.100.9"})
    assert client_ip(request, _settings(forwarded_for_hops=1)) == "198.51.100.9"
    assert client_ip(request, _settings(forwarded_for_hops=2)) == "2.2.2.2"


def test_too_few_forwarded_entries_fall_back_to_the_peer() -> None:
    request = _request({"X-Forwarded-For": "198.51.100.9"})
    assert client_ip(request, _settings(forwarded_for_hops=2)) == "10.0.0.7"


def test_addresses_are_normalised() -> None:
    request = _request({"X-Forwarded-For": " 2001:DB8::0001 "})
    assert client_ip(request, _settings(forwarded_for_hops=1)) == "2001:db8::1"


def test_no_peer_is_unknown() -> None:
    assert client_ip(_request({}, peer=None), _settings()) == "unknown"


def test_a_short_proxy_secret_is_rejected() -> None:
    with pytest.raises(ValueError, match="PROXY_SHARED_SECRET"):
        _settings(proxy_shared_secret="short")
