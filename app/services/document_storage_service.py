import hashlib
import hmac
import ipaddress
import time
from collections.abc import Iterator
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from app.core.config import settings


class StorageProviderError(Exception):
    pass


class DocumentStorageService:
    local_bucket_name = "documents"

    def __init__(
        self,
        root_path: str | None = None,
        *,
        backend: str | None = None,
        minio_client=None,
        minio_public_client=None,
    ) -> None:
        self.backend = (backend or settings.storage_backend).strip().lower()
        if self.backend not in {"local", "minio"}:
            raise StorageProviderError("Backend de almacenamiento no soportado.")
        self.bucket_name = (
            settings.minio_bucket if self.backend == "minio" else self.local_bucket_name
        )
        self.root_path = _storage_root(root_path or settings.local_storage_path)
        self._minio_client = minio_client
        self._minio_public_client = minio_public_client

    @property
    def is_local(self) -> bool:
        return self.backend == "local"

    @property
    def is_minio(self) -> bool:
        return self.backend == "minio"

    def build_storage_key(self, *, case_id: str, document_id: str, filename: str) -> str:
        return f"{case_id}/{document_id}/{filename}"

    def save(
        self,
        *,
        storage_key: str,
        content: bytes,
        content_type: str | None = None,
    ) -> None:
        if self.is_minio:
            try:
                self._internal_client().put_object(
                    self.bucket_name,
                    storage_key,
                    BytesIO(content),
                    length=len(content),
                    content_type=content_type or "application/octet-stream",
                )
            except Exception as exc:
                raise StorageProviderError("No fue posible guardar el archivo en MinIO.") from exc
            return

        path = self.path_for_key(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def exists(self, storage_key: str) -> bool:
        if self.is_minio:
            try:
                self.stat_object(storage_key)
            except FileNotFoundError:
                return False
            return True
        return self.path_for_key(storage_key).exists()

    def stat_object(self, storage_key: str):
        if self.is_minio:
            try:
                return self._internal_client().stat_object(self.bucket_name, storage_key)
            except Exception as exc:
                if _is_minio_not_found(exc):
                    raise FileNotFoundError(storage_key) from exc
                raise StorageProviderError("No fue posible verificar el archivo en MinIO.") from exc
        path = self.path_for_key(storage_key)
        if not path.exists():
            raise FileNotFoundError(storage_key)
        return path.stat()

    def read(self, storage_key: str) -> bytes:
        if self.is_minio:
            response = None
            try:
                response = self._internal_client().get_object(self.bucket_name, storage_key)
                return response.read()
            except Exception as exc:
                if _is_minio_not_found(exc):
                    raise FileNotFoundError(storage_key) from exc
                raise StorageProviderError("No fue posible leer el archivo desde MinIO.") from exc
            finally:
                _close_minio_response(response)
        return self.path_for_key(storage_key).read_bytes()

    def stream(self, storage_key: str, *, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        if self.is_minio:
            try:
                response = self._internal_client().get_object(self.bucket_name, storage_key)
            except Exception as exc:
                if _is_minio_not_found(exc):
                    raise FileNotFoundError(storage_key) from exc
                raise StorageProviderError("No fue posible leer el archivo desde MinIO.") from exc

            def minio_iterator() -> Iterator[bytes]:
                try:
                    for chunk in response.stream(chunk_size):
                        if chunk:
                            yield chunk
                finally:
                    _close_minio_response(response)

            return minio_iterator()

        path = self.path_for_key(storage_key)
        if not path.exists():
            raise FileNotFoundError(storage_key)

        def local_iterator() -> Iterator[bytes]:
            with path.open("rb") as file:
                while chunk := file.read(chunk_size):
                    yield chunk

        return local_iterator()

    def path_for_key(self, storage_key: str) -> Path:
        clean_parts = [part for part in storage_key.replace("\\", "/").split("/") if part]
        return self.root_path.joinpath(self.bucket_name, *clean_parts)

    def signed_upload_url(
        self,
        *,
        document_id: str,
        storage_key: str,
        expires_in_seconds: int | None = None,
    ) -> str:
        ttl_seconds = expires_in_seconds or settings.minio_presigned_upload_ttl_seconds
        if self.is_minio:
            try:
                return self._public_client().presigned_put_object(
                    self.bucket_name,
                    storage_key,
                    expires=timedelta(seconds=ttl_seconds),
                )
            except Exception as exc:
                raise StorageProviderError("No fue posible generar la URL de subida.") from exc

        expires = int(time.time()) + ttl_seconds
        signature = self._signature(
            document_id=document_id,
            expires=expires,
            action="upload",
        )
        return (
            f"{_backend_url()}{settings.api_v1_prefix}/documents/{document_id}/upload"
            f"?expires={expires}&token={signature}"
        )

    def signed_view_url(
        self,
        *,
        document_id: str,
        storage_key: str | None = None,
        expires_in_seconds: int = 300,
    ) -> str:
        if self.is_minio:
            if not storage_key:
                raise StorageProviderError("Storage key requerido para visualizar desde MinIO.")
            try:
                return self._public_client().presigned_get_object(
                    self.bucket_name,
                    storage_key,
                    expires=timedelta(seconds=expires_in_seconds),
                )
            except Exception as exc:
                raise StorageProviderError("No fue posible generar la URL de visualizacion.") from exc

        expires = int(time.time()) + expires_in_seconds
        signature = self._signature(
            document_id=document_id,
            expires=expires,
            action="view",
        )
        return (
            f"{_backend_url()}{settings.api_v1_prefix}/documents/{document_id}/file"
            f"?expires={expires}&token={signature}"
        )

    def validate_token(
        self,
        *,
        document_id: str,
        expires: int,
        token: str,
        action: str = "view",
    ) -> bool:
        if expires < int(time.time()):
            return False
        expected = self._signature(
            document_id=document_id,
            expires=expires,
            action=action,
        )
        return hmac.compare_digest(expected, token)

    def _signature(self, *, document_id: str, expires: int, action: str) -> str:
        secret = settings.jwt_access_secret.encode("utf-8")
        payload = f"{action}:{document_id}:{expires}".encode("utf-8")
        return hmac.new(secret, payload, hashlib.sha256).hexdigest()

    def _internal_client(self):
        if self._minio_client is None:
            self._minio_client = self._build_minio_client(settings.minio_endpoint)
        return self._minio_client

    def _public_client(self):
        if self._minio_public_client is None:
            _validate_public_minio_endpoint()
            self._minio_public_client = self._build_minio_client(
                settings.minio_public_endpoint,
            )
        return self._minio_public_client

    def _build_minio_client(self, raw_endpoint: str):
        Minio = _load_minio_client_class()
        endpoint, secure = _normalize_minio_endpoint(
            raw_endpoint,
            default_secure=settings.minio_secure,
        )
        return Minio(
            endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=secure,
            region=settings.minio_region,
        )


def _load_minio_client_class():
    try:
        from minio import Minio
    except ImportError as exc:
        raise StorageProviderError("El paquete minio no esta instalado.") from exc
    return Minio


def _normalize_minio_endpoint(raw_endpoint: str, *, default_secure: bool) -> tuple[str, bool]:
    endpoint = raw_endpoint.strip()
    if "://" not in endpoint:
        return endpoint, default_secure

    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise StorageProviderError("Endpoint de MinIO invalido.")
    return parsed.netloc, parsed.scheme == "https"


def _is_minio_not_found(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    response = getattr(exc, "response", None)
    status = getattr(response, "status", None)
    return code in {"NoSuchBucket", "NoSuchKey", "NoSuchObject", "NotFound"} or status == 404


def _close_minio_response(response) -> None:
    if response is None:
        return
    close = getattr(response, "close", None)
    if callable(close):
        close()
    release_conn = getattr(response, "release_conn", None)
    if callable(release_conn):
        release_conn()


def _backend_url() -> str:
    public_url = settings.backend_public_url
    if _is_production_env() and _is_local_or_internal_url(public_url):
        raise StorageProviderError(
            "BACKEND_PUBLIC_URL/API_PUBLIC_BASE_URL debe ser una URL publica en produccion."
        )
    return public_url


def _validate_public_minio_endpoint() -> None:
    if not _is_production_env():
        return
    public_endpoint = settings.minio_public_endpoint
    if _is_local_or_internal_url(_endpoint_as_url(public_endpoint, secure=settings.minio_secure)):
        raise StorageProviderError(
            "MINIO_PUBLIC_ENDPOINT debe ser una URL publica en produccion."
        )


def _endpoint_as_url(raw_endpoint: str, *, secure: bool) -> str:
    endpoint = raw_endpoint.strip()
    if "://" in endpoint:
        return endpoint
    scheme = "https" if secure else "http"
    return f"{scheme}://{endpoint}"


def _is_production_env() -> bool:
    return settings.app_env.strip().lower() in {"production", "prod"}


def _is_local_or_internal_url(raw_url: str) -> bool:
    parsed = urlsplit(raw_url)
    hostname = (parsed.hostname or "").strip().lower()
    if parsed.scheme not in {"http", "https"} or not hostname:
        return True
    if hostname in {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "host.docker.internal",
        "minio",
        "labora-minio",
        "labora-backend",
    }:
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        return address.is_loopback or address.is_private or address.is_link_local
    if hostname.endswith(".local") or hostname.endswith(".internal"):
        return True
    return False


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
