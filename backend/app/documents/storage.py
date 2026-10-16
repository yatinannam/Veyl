import os
import re
from pathlib import Path
from uuid import UUID

_KEY = re.compile(
    r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}/[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$"
)


class LocalStorage:
    """Original uploads on a local volume. Keys are always `tenant_id/document_id`, generated
    by the server; user-supplied filenames never reach the filesystem."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @staticmethod
    def key_for(tenant_id: UUID, document_id: UUID) -> str:
        return f"{tenant_id}/{document_id}"

    def _path(self, key: str) -> Path:
        if not _KEY.fullmatch(key):
            raise ValueError("invalid storage key")
        return self.root / key

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
