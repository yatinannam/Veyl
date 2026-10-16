import uuid
from pathlib import Path

import pytest

from app.documents.storage import LocalStorage


def test_round_trip_under_tenant_and_document_key(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    tenant_id, document_id = uuid.uuid4(), uuid.uuid4()
    key = storage.key_for(tenant_id, document_id)
    storage.save(key, b"hello")
    assert (tmp_path / str(tenant_id) / str(document_id)).read_bytes() == b"hello"
    assert storage.read(key) == b"hello"
    storage.delete(key)
    assert not (tmp_path / str(tenant_id) / str(document_id)).exists()


@pytest.mark.parametrize("key", ["../etc/passwd", "a/b", "/abs/path", f"{uuid.uuid4()}/../x"])
def test_keys_that_are_not_server_generated_are_refused(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).read(key)
