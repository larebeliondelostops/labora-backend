import hashlib
import hmac
import time
from pathlib import Path

from app.core.config import settings


class DocumentStorageService:
    bucket_name = "documents"

    def __init__(self, root_path: str | None = None) -> None:
        self.root_path = _storage_root(root_path or settings.local_storage_path)

    def build_storage_key(self, *, case_id: str, document_id: str, filename: str) -> str:
        return f"{case_id}/{document_id}/{filename}"

    def save(self, *, storage_key: str, content: bytes) -> None:
        path = self.path_for_key(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def exists(self, storage_key: str) -> bool:
        return self.path_for_key(storage_key).exists()

    def read(self, storage_key: str) -> bytes:
        return self.path_for_key(storage_key).read_bytes()

    def path_for_key(self, storage_key: str) -> Path:
        clean_parts = [part for part in storage_key.replace("\\", "/").split("/") if part]
        return self.root_path.joinpath(self.bucket_name, *clean_parts)

    def signed_view_url(self, *, document_id: str, expires_in_seconds: int = 300) -> str:
        expires = int(time.time()) + expires_in_seconds
        signature = self._signature(document_id=document_id, expires=expires)
        return (
            f"{settings.api_v1_prefix}/documents/{document_id}/file"
            f"?expires={expires}&token={signature}"
        )

    def validate_token(self, *, document_id: str, expires: int, token: str) -> bool:
        if expires < int(time.time()):
            return False
        expected = self._signature(document_id=document_id, expires=expires)
        return hmac.compare_digest(expected, token)

    def _signature(self, *, document_id: str, expires: int) -> str:
        secret = settings.jwt_access_secret.encode("utf-8")
        payload = f"{document_id}:{expires}".encode("utf-8")
        return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _storage_root(raw_path: str) -> Path:
    path = Path(raw_path)
    cwd = Path.cwd()
    if raw_path == "/app/storage":
        try:
            app_root = Path("/app").resolve()
            cwd_resolved = cwd.resolve()
            if cwd_resolved != app_root and not cwd_resolved.is_relative_to(app_root):
                return cwd / "storage"
        except OSError:
            return cwd / "storage"
    try:
        if path.is_absolute() and not path.resolve().is_relative_to(cwd.resolve()):
            if raw_path == "/app/storage":
                return cwd / "storage"
    except OSError:
        if raw_path == "/app/storage":
            return cwd / "storage"
    return path
