from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.auth.deps import CurrentUser, EngineDep, require_csrf
from app.auth.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from app.authz import Role
from app.users import service
from app.users.service import UserRecord

router = APIRouter(prefix="/users", tags=["users"])


class UserOut(BaseModel):
    id: UUID
    email: str
    role: Role
    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
    role: Role


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _require_a_change(self) -> "UserUpdate":
        if self.role is None and self.is_active is None:
            raise ValueError("Provide role and/or is_active.")
        return self


def _out(record: UserRecord) -> UserOut:
    return UserOut(
        id=record.id,
        email=record.email,
        role=record.role,
        is_active=record.is_active,
        created_at=record.created_at,
    )


class DirectoryEntryOut(BaseModel):
    id: UUID
    email: str
    role: Role


@router.get("/directory")
def directory(ctx: CurrentUser, engine: EngineDep) -> list[DirectoryEntryOut]:
    return [
        DirectoryEntryOut(id=e.id, email=e.email, role=e.role)
        for e in service.directory(engine, ctx)
    ]


@router.get("")
def list_users(ctx: CurrentUser, engine: EngineDep) -> list[UserOut]:
    return [_out(r) for r in service.list_users(engine, ctx)]


@router.post("", status_code=201, dependencies=[Depends(require_csrf)])
def create_user(body: UserCreate, ctx: CurrentUser, engine: EngineDep) -> UserOut:
    record = service.create_user(
        engine, ctx, email=body.email, password=body.password, role=body.role
    )
    return _out(record)


@router.patch("/{user_id}", dependencies=[Depends(require_csrf)])
def update_user(user_id: UUID, body: UserUpdate, ctx: CurrentUser, engine: EngineDep) -> UserOut:
    record = service.update_user(engine, ctx, user_id, role=body.role, is_active=body.is_active)
    return _out(record)
