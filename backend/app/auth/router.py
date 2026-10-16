from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.auth import service
from app.auth.deps import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    CurrentUser,
    EngineDep,
    SettingsDep,
    require_csrf,
)
from app.auth.passwords import MAX_PASSWORD_LENGTH
from app.authz import Role, permissions
from app.config import Settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_slug: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class MeUser(BaseModel):
    id: UUID
    email: str
    role: Role


class MeTenant(BaseModel):
    id: UUID
    slug: str
    name: str


class MeResponse(BaseModel):
    user: MeUser
    tenant: MeTenant
    permissions: list[str]


def _me(identity: service.Identity) -> MeResponse:
    return MeResponse(
        user=MeUser(id=identity.user_id, email=identity.email, role=identity.role),
        tenant=MeTenant(
            id=identity.tenant_id, slug=identity.tenant_slug, name=identity.tenant_name
        ),
        permissions=permissions(identity.role),
    )


def _set_cookie(
    response: Response, settings: Settings, name: str, value: str, *, httponly: bool
) -> None:
    response.set_cookie(
        name,
        value,
        max_age=settings.session_ttl_minutes * 60,
        path="/",
        secure=settings.cookie_secure,
        httponly=httponly,
        samesite="lax",
    )


@router.post("/login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    engine: EngineDep,
    settings: SettingsDep,
) -> MeResponse:
    # No CSRF token exists before login. The body must be JSON, the API sends no CORS
    # headers, and cookies are SameSite=Lax, so a cross-site page cannot submit this.
    result = service.login(
        engine,
        request.app.state.login_limiters,
        tenant_slug=body.tenant_slug,
        email=body.email,
        password=body.password,
        client_ip=request.client.host if request.client else "unknown",
        session_ttl=timedelta(minutes=settings.session_ttl_minutes),
    )
    _set_cookie(response, settings, SESSION_COOKIE, result.session_token, httponly=True)
    _set_cookie(response, settings, CSRF_COOKIE, result.csrf_token, httponly=False)
    return _me(result.identity)


@router.post("/logout", status_code=204, dependencies=[Depends(require_csrf)])
def logout(ctx: CurrentUser, engine: EngineDep, settings: SettingsDep) -> Response:
    service.logout(engine, ctx)
    response = Response(status_code=204)
    for name, httponly in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(
            name, path="/", secure=settings.cookie_secure, httponly=httponly, samesite="lax"
        )
    return response


@router.get("/me")
def me(ctx: CurrentUser, engine: EngineDep) -> MeResponse:
    return _me(service.load_identity(engine, ctx))
