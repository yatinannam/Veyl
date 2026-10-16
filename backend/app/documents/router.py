from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.auth.deps import CurrentUser, EngineDep, SettingsDep, require_csrf
from app.authz import AuthContext, Classification, Role
from app.documents import service
from app.documents.storage import LocalStorage
from app.documents.validation import validate_upload

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentOut(BaseModel):
    id: UUID
    title: str
    original_filename: str
    mime_type: str
    size_bytes: int
    classification: Classification
    status: str
    error: str | None
    uploaded_by: UUID
    created_at: datetime
    updated_at: datetime
    can_manage: bool
    allowed_roles: list[Role] | None
    allowed_user_ids: list[UUID] | None


class PermissionsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: Classification
    allowed_roles: list[Role] = Field(default_factory=list, max_length=3)
    allowed_user_ids: list[UUID] = Field(default_factory=list, max_length=500)


def _storage(request: Request) -> LocalStorage:
    return cast(LocalStorage, request.app.state.storage)


def _out(ctx: AuthContext, record: service.DocumentRecord) -> DocumentOut:
    manage = service.can_manage(ctx, record)
    return DocumentOut(
        id=record.id,
        title=record.title,
        original_filename=record.original_filename,
        mime_type=record.mime_type,
        size_bytes=record.size_bytes,
        classification=record.classification,
        status=record.status,
        error=record.error,
        uploaded_by=record.uploaded_by,
        created_at=record.created_at,
        updated_at=record.updated_at,
        can_manage=manage,
        allowed_roles=record.allowed_roles if manage else None,
        allowed_user_ids=record.allowed_user_ids if manage else None,
    )


@router.post("", status_code=202, dependencies=[Depends(require_csrf)])
def upload_document(
    ctx: CurrentUser,
    engine: EngineDep,
    settings: SettingsDep,
    request: Request,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1, max_length=300)],
    classification: Annotated[Classification, Form()],
    allowed_roles: Annotated[list[Role] | None, Form()] = None,
    allowed_user_ids: Annotated[list[UUID] | None, Form()] = None,
) -> DocumentOut:
    max_bytes = settings.max_upload_mb * 1024 * 1024
    data = file.file.read(max_bytes + 1)
    validated = validate_upload(file.filename or "", data, max_bytes=max_bytes)
    record = service.upload_document(
        engine,
        _storage(request),
        ctx,
        upload=validated,
        data=data,
        title=title,
        classification=classification,
        allowed_roles=allowed_roles or [],
        allowed_user_ids=allowed_user_ids or [],
    )
    return _out(ctx, record)


@router.get("")
def list_documents(ctx: CurrentUser, engine: EngineDep) -> list[DocumentOut]:
    return [_out(ctx, r) for r in service.list_documents(engine, ctx)]


@router.get("/{document_id}")
def get_document(document_id: UUID, ctx: CurrentUser, engine: EngineDep) -> DocumentOut:
    return _out(ctx, service.get_document(engine, ctx, document_id))


@router.put("/{document_id}/permissions", dependencies=[Depends(require_csrf)])
def update_permissions(
    document_id: UUID, body: PermissionsUpdate, ctx: CurrentUser, engine: EngineDep
) -> DocumentOut:
    record = service.update_permissions(
        engine,
        ctx,
        document_id,
        classification=body.classification,
        allowed_roles=body.allowed_roles,
        allowed_user_ids=body.allowed_user_ids,
    )
    return _out(ctx, record)


@router.delete("/{document_id}", status_code=204, dependencies=[Depends(require_csrf)])
def delete_document(
    document_id: UUID, ctx: CurrentUser, engine: EngineDep, request: Request
) -> Response:
    service.delete_document(engine, _storage(request), ctx, document_id)
    return Response(status_code=204)
