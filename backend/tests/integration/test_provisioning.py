import uuid

import pytest
from sqlalchemy import Engine, text

from app import cli
from app.auth.passwords import verify_password
from app.provisioning import provision_tenant
from tests.conftest import DatabaseUrls

pytestmark = pytest.mark.integration

PASSWORD = "a-strong-password-123"


def _slug() -> str:
    return f"p-{uuid.uuid4().hex[:10]}"


def test_provisioned_admin_can_be_resolved_for_login(
    owner_engine: Engine, app_engine: Engine
) -> None:
    slug = _slug()
    result = provision_tenant(
        owner_engine,
        slug=slug,
        name="Prov Co",
        admin_email=" Admin@Prov.test ",
        admin_password=PASSWORD,
    )
    with app_engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM auth_resolve_login(:s, :e)"),
            {"s": slug, "e": "admin@prov.test"},
        ).one()
    assert (row.user_id, row.tenant_id, row.role) == (
        result.admin_user_id,
        result.tenant_id,
        "admin",
    )
    assert verify_password(row.password_hash, PASSWORD)


@pytest.mark.parametrize(
    ("slug", "password"),
    [("Bad Slug!", PASSWORD), ("ok-slug", "short")],
    ids=["bad-slug", "short-password"],
)
def test_provision_rejects_invalid_input(owner_engine: Engine, slug: str, password: str) -> None:
    with pytest.raises(ValueError):
        provision_tenant(
            owner_engine, slug=slug, name="X", admin_email="a@b.test", admin_password=password
        )


def test_provision_rejects_duplicate_slug(owner_engine: Engine) -> None:
    slug = _slug()
    provision_tenant(
        owner_engine, slug=slug, name="One", admin_email="a@one.test", admin_password=PASSWORD
    )
    with pytest.raises(ValueError, match="already exists"):
        provision_tenant(
            owner_engine, slug=slug, name="Two", admin_email="a@two.test", admin_password=PASSWORD
        )


def test_cli_provisions_with_owner_url_and_env_password(
    database_urls: DatabaseUrls,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MIGRATIONS_DATABASE_URL", database_urls.owner)
    monkeypatch.setenv("VEYL_ADMIN_PASSWORD", PASSWORD)
    code = cli.main(
        [
            "provision-tenant",
            "--slug",
            _slug(),
            "--name",
            "CLI Co",
            "--admin-email",
            "root@cli.test",
        ]
    )
    assert code == 0
    assert "tenant_id=" in capsys.readouterr().out


def test_cli_refuses_without_password_env(
    database_urls: DatabaseUrls,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MIGRATIONS_DATABASE_URL", database_urls.owner)
    monkeypatch.delenv("VEYL_ADMIN_PASSWORD", raising=False)
    code = cli.main(
        ["provision-tenant", "--slug", _slug(), "--name", "X", "--admin-email", "a@b.test"]
    )
    assert code == 2
    assert "VEYL_ADMIN_PASSWORD" in capsys.readouterr().err
