from datetime import timedelta

import pytest

from app.auth import service
from app.auth.ratelimit import LoginLimiters
from app.db.engine import create_db_engine
from app.errors import RateLimited


def test_login_sheds_load_when_password_hashing_is_saturated() -> None:
    # argon2 costs ~64 MiB and real CPU per attempt; a flood of logins with rotating
    # identities must not be able to occupy every worker thread.
    limiters = LoginLimiters.create(
        max_failures=5, window_seconds=60, max_concurrent_hashes=1, hash_wait_seconds=0.05
    )
    assert limiters.hashing.acquire(timeout=0)  # occupy the only slot
    engine = create_db_engine("postgresql+psycopg://x:x@127.0.0.1:1/x")  # must not be reached
    try:
        with pytest.raises(RateLimited):
            service.login(
                engine,
                limiters,
                tenant_slug="acme",
                email="a@b.test",
                password="whatever-password",
                client_ip="203.0.113.9",
                session_ttl=timedelta(minutes=5),
            )
    finally:
        limiters.hashing.release()
        engine.dispose()
