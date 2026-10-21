from app.db.diagnose import describe_db_error


class FakeDriverError(Exception):
    pass


class Wrapped(Exception):
    def __init__(self, orig: Exception) -> None:
        super().__init__("wrapped")
        self.orig = orig


def test_uses_the_driver_error_and_removes_secrets() -> None:
    error = Wrapped(
        FakeDriverError(
            'connection to "ep-x.neon.tech" failed: FATAL:  password authentication failed '
            "for user veyl_app (tried hunter2-secret)\n  extra line"
        )
    )
    text = describe_db_error(error, ["hunter2-secret", None])
    assert text.startswith("FakeDriverError: connection to")
    assert "password authentication failed for user veyl_app" in text
    assert "hunter2-secret" not in text and "\n" not in text


def test_removes_credentials_in_urls_and_password_settings() -> None:
    error = FakeDriverError("bad dsn postgresql://u:pw-in-url@h/d password=pw-setting x")
    text = describe_db_error(error)
    assert "pw-in-url" not in text and "pw-setting" not in text
    assert "postgresql://u:***@h/d" in text
