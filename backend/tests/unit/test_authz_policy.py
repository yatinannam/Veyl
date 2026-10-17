from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.authz import (
    Action,
    AuthContext,
    ChunkAcl,
    Classification,
    DocumentRef,
    Role,
    authorize,
    can_read_chunk,
    is_allowed,
    permissions,
    within_clearance,
)
from app.errors import Forbidden

T1, T2 = UUID(int=1), UUID(int=2)
U1, U2 = UUID(int=11), UUID(int=12)
SESSION = UUID(int=99)
C = Classification


def ctx(role: Role, tenant: UUID = T1, user: UUID = U1) -> AuthContext:
    return AuthContext(user_id=user, tenant_id=tenant, role=role, session_id=SESSION)


def doc(
    classification: Classification, uploaded_by: UUID | None = None, tenant: UUID = T1
) -> DocumentRef:
    return DocumentRef(tenant_id=tenant, uploaded_by=uploaded_by, classification=classification)


def acl(
    classification: Classification,
    roles: set[Role] | None = None,
    users: set[UUID] | None = None,
    tenant: UUID = T1,
) -> ChunkAcl:
    return ChunkAcl(
        tenant_id=tenant,
        allowed_roles=frozenset(roles or set()),
        allowed_user_ids=frozenset(users or set()),
        classification=classification,
    )


@pytest.mark.parametrize(
    ("role", "classification", "expected"),
    [
        (Role.MEMBER, C.PUBLIC, True),
        (Role.MEMBER, C.INTERNAL, True),
        (Role.MEMBER, C.CONFIDENTIAL, False),
        (Role.MEMBER, C.RESTRICTED, False),
        (Role.MANAGER, C.CONFIDENTIAL, True),
        (Role.MANAGER, C.RESTRICTED, False),
        (Role.ADMIN, C.RESTRICTED, True),
    ],
)
def test_clearance(role: Role, classification: Classification, expected: bool) -> None:
    assert within_clearance(role, classification) is expected


@pytest.mark.parametrize(
    ("action", "member", "manager", "admin"),
    [
        (Action.TENANT_READ, True, True, True),
        (Action.QUERY, True, True, True),
        (Action.TENANT_UPDATE, False, False, True),
        (Action.USER_MANAGE, False, False, True),
        (Action.AUDIT_READ, False, False, True),
    ],
)
def test_action_matrix(action: Action, member: bool, manager: bool, admin: bool) -> None:
    assert is_allowed(ctx(Role.MEMBER), action) is member
    assert is_allowed(ctx(Role.MANAGER), action) is manager
    assert is_allowed(ctx(Role.ADMIN), action) is admin


def test_upload_requires_manager_and_clearance() -> None:
    assert not is_allowed(ctx(Role.MEMBER), Action.DOCUMENT_UPLOAD, doc(C.PUBLIC))
    assert is_allowed(ctx(Role.MANAGER), Action.DOCUMENT_UPLOAD, doc(C.CONFIDENTIAL))
    assert not is_allowed(ctx(Role.MANAGER), Action.DOCUMENT_UPLOAD, doc(C.RESTRICTED))
    assert is_allowed(ctx(Role.ADMIN), Action.DOCUMENT_UPLOAD, doc(C.RESTRICTED))


def test_manager_manages_only_own_documents_within_clearance() -> None:
    manager = ctx(Role.MANAGER, user=U1)
    assert is_allowed(manager, Action.DOCUMENT_MANAGE, doc(C.CONFIDENTIAL, uploaded_by=U1))
    assert not is_allowed(manager, Action.DOCUMENT_MANAGE, doc(C.INTERNAL, uploaded_by=U2))
    assert not is_allowed(manager, Action.DOCUMENT_MANAGE, doc(C.RESTRICTED, uploaded_by=U1))
    assert is_allowed(ctx(Role.ADMIN), Action.DOCUMENT_MANAGE, doc(C.RESTRICTED, uploaded_by=U2))


@pytest.mark.parametrize("role", list(Role))
def test_document_actions_without_a_resource_are_denied(role: Role) -> None:
    assert not is_allowed(ctx(role), Action.DOCUMENT_UPLOAD)
    assert not is_allowed(ctx(role), Action.DOCUMENT_MANAGE)


def test_document_actions_in_another_tenant_are_denied() -> None:
    assert not is_allowed(ctx(Role.ADMIN), Action.DOCUMENT_MANAGE, doc(C.PUBLIC, tenant=T2))


def test_authorize_raises_forbidden_when_denied() -> None:
    with pytest.raises(Forbidden):
        authorize(ctx(Role.MEMBER), Action.USER_MANAGE)


def test_authorize_returns_none_when_allowed() -> None:
    assert authorize(ctx(Role.ADMIN), Action.USER_MANAGE) is None


def test_permission_hints_per_role() -> None:
    assert permissions(Role.MEMBER) == ["rag.query", "tenant.read"]
    assert permissions(Role.MANAGER) == [
        "document.upload",
        "rag.query",
        "tenant.read",
        "user.directory",
    ]
    assert permissions(Role.ADMIN) == [
        "audit.read",
        "document.upload",
        "rag.query",
        "tenant.read",
        "tenant.update",
        "user.directory",
        "user.manage",
    ]


@pytest.mark.parametrize("role", list(Role))
def test_permission_hints_agree_with_policy(role: Role) -> None:
    for action in (
        Action.TENANT_READ,
        Action.TENANT_UPDATE,
        Action.USER_MANAGE,
        Action.AUDIT_READ,
        Action.QUERY,
        Action.USER_DIRECTORY,
    ):
        assert (str(action) in permissions(role)) is is_allowed(ctx(role), action)


def test_member_reads_chunk_shared_with_members() -> None:
    assert can_read_chunk(ctx(Role.MEMBER), acl(C.INTERNAL, roles={Role.MEMBER}))


def test_member_cannot_read_admin_only_chunk() -> None:
    assert not can_read_chunk(ctx(Role.MEMBER), acl(C.RESTRICTED, roles={Role.ADMIN}))


def test_explicit_user_grant_is_still_bounded_by_clearance() -> None:
    granted = acl(C.CONFIDENTIAL, users={U1})
    assert not can_read_chunk(ctx(Role.MEMBER, user=U1), granted)
    assert can_read_chunk(ctx(Role.MANAGER, user=U1), granted)
    assert not can_read_chunk(ctx(Role.MANAGER, user=U2), granted)


def test_admin_reads_any_chunk_in_own_tenant() -> None:
    assert can_read_chunk(ctx(Role.ADMIN), acl(C.RESTRICTED))


def test_no_chunk_is_readable_across_tenants() -> None:
    assert not can_read_chunk(ctx(Role.ADMIN, tenant=T1), acl(C.PUBLIC, roles=set(Role), tenant=T2))


_ids = st.sampled_from([UUID(int=i) for i in range(3)])
_roles = st.sampled_from(list(Role))
_classes = st.sampled_from(list(Classification))
_contexts = st.builds(
    AuthContext, user_id=_ids, tenant_id=_ids, role=_roles, session_id=st.just(SESSION)
)
_acls = st.builds(
    ChunkAcl,
    tenant_id=_ids,
    allowed_roles=st.frozensets(_roles),
    allowed_user_ids=st.frozensets(_ids),
    classification=_classes,
)
_docs = st.builds(
    DocumentRef, tenant_id=_ids, uploaded_by=st.none() | _ids, classification=_classes
)


@given(_contexts, _acls)
def test_readable_chunks_are_same_tenant_and_within_clearance(c: AuthContext, a: ChunkAcl) -> None:
    if can_read_chunk(c, a):
        assert a.tenant_id == c.tenant_id
        assert within_clearance(c.role, a.classification)


@given(_contexts, _acls)
def test_readable_chunks_have_a_grant(c: AuthContext, a: ChunkAcl) -> None:
    if can_read_chunk(c, a):
        assert c.role is Role.ADMIN or c.role in a.allowed_roles or c.user_id in a.allowed_user_ids


@given(_contexts, _docs, st.sampled_from([Action.DOCUMENT_UPLOAD, Action.DOCUMENT_MANAGE]))
def test_document_actions_never_cross_tenant_or_exceed_clearance(
    c: AuthContext, d: DocumentRef, action: Action
) -> None:
    if is_allowed(c, action, d):
        assert d.tenant_id == c.tenant_id
        assert within_clearance(c.role, d.classification)
        assert c.role is not Role.MEMBER
