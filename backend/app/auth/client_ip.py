"""The client IP used for login rate limiting. Forwarded headers are only believed when
the deployment says who sets them; uvicorn's own proxy-header handling is turned off."""

import hmac
import ipaddress

from fastapi import Request

from app.config import Settings

PROXY_SECRET_HEADER = "X-Veyl-Proxy-Secret"  # noqa: S105 (a header name)
CLIENT_IP_HEADER = "X-Veyl-Client-IP"
UNKNOWN = "unknown"


def _ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request: Request, settings: Settings) -> str:
    secret = settings.proxy_shared_secret
    if secret is not None:
        sent = request.headers.get(PROXY_SECRET_HEADER, "")
        if hmac.compare_digest(sent.encode(), secret.get_secret_value().encode()):
            named = _ip(request.headers.get(CLIENT_IP_HEADER))
            if named is not None:
                return named
    if settings.forwarded_for_hops:
        # Clients can prepend anything; each trusted proxy appends the address it saw, so
        # only entries counted from the right are trustworthy.
        entries = ",".join(request.headers.getlist("X-Forwarded-For")).split(",")
        if len(entries) >= settings.forwarded_for_hops:
            appended = _ip(entries[-settings.forwarded_for_hops])
            if appended is not None:
                return appended
    peer = request.client.host if request.client else None
    return _ip(peer) or UNKNOWN
