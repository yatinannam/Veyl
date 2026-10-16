import hmac
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy import Engine

from app.auth.service import resolve_session
from app.authz import AuthContext
from app.config import Settings
from app.errors import CsrfFailed, Unauthenticated

SESSION_COOKIE = "veyl_session"
CSRF_COOKIE = "veyl_csrf"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_auth_context(request: Request) -> AuthContext:
    """The only source of identity: the session cookie, resolved in the database.
    Database errors propagate and become 503 (fail closed)."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise Unauthenticated()
    ctx = resolve_session(get_engine(request), token)
    if ctx is None:
        raise Unauthenticated()
    return ctx


def require_csrf(request: Request) -> None:
    """Double-submit check for state-changing requests."""
    if request.method in _SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    # Compare bytes: compare_digest raises TypeError on non-ASCII str.
    if not cookie or not hmac.compare_digest(cookie.encode(), header.encode()):
        raise CsrfFailed()


EngineDep = Annotated[Engine, Depends(get_engine)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
CurrentUser = Annotated[AuthContext, Depends(get_auth_context)]
