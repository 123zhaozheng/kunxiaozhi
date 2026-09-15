"""
File upload API routes

Provides endpoints for file uploads to S3-compatible storage.
"""

import hashlib
import uuid
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import Any, Literal, Protocol
from urllib.parse import unquote, urlsplit

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from src.api.deps import get_current_user_required, require_permissions
from src.api.routes.file_type import (
    FILE_EXTENSIONS,
    FileCategory,
    get_file_category,
    get_permission_for_category,
)
from src.infra.async_utils import run_blocking_io
from src.infra.async_utils.background_tasks import BestEffortTaskLimiter
from src.infra.auth.rbac import check_permission
from src.infra.logging import get_logger
from src.infra.storage.s3 import (
    S3Config,
    S3Provider,
)
from src.infra.storage.s3.base import BinaryReadFile
from src.infra.storage.user_storage import (
    StorageDomainError,
    StorageManagedBySourceError,
    StorageQuotaExceededError,
    UserStorageQuotaService,
)
from src.infra.upload.file_record import FileRecordStorage
from src.kernel.config import settings
from src.kernel.schemas.storage import FileLifecycleStatus, StorageSource
from src.kernel.schemas.user import TokenPayload

logger = get_logger(__name__)

_file_record_storage = FileRecordStorage()
_upload_delete_tasks = BestEffortTaskLimiter("upload delete", max_tasks=8)

UPLOAD_READ_CHUNK_SIZE = 1024 * 1024
UPLOAD_SPOOL_MEMORY_LIMIT = 2 * 1024 * 1024
SIGNED_URL_KEYS_MAX = 100
LEGACY_PUBLIC_OBJECT_PREFIXES = (
    "generated-images/",
    "revealed_files/",
    "tool_binaries/",
    "revealed_projects/",
)
LEGACY_SYSTEM_SOURCES = frozenset(
    {
        "generated",
        "generated_image",
        "reveal",
        "revealed",
        "revealed_file",
        "revealed_project",
        "tool",
        "tool_binary",
    }
)

# Extensions that can become active content or native code when opened by a
# client or storage consumer.  This is intentionally a denylist: legacy and
# unknown compatibility extensions continue through the existing policy, while
# these suffixes are rejected before any request body or storage is touched.
DANGEROUS_UPLOAD_EXTENSIONS = frozenset(
    {
        "apk",
        "appimage",
        "asp",
        "aspx",
        "bat",
        "bin",
        "class",
        "cmd",
        "com",
        "cpl",
        "desktop",
        "docm",
        "dll",
        "dylib",
        "ear",
        "exe",
        "hta",
        "htm",
        "html",
        "jar",
        "js",
        "jse",
        "jsp",
        "lnk",
        "mjs",
        "msi",
        "msp",
        "php",
        "ps1",
        "scr",
        "sh",
        "so",
        "svg",
        "vb",
        "vbe",
        "vbs",
        "pptm",
        "url",
        "war",
        "wsf",
        "xlsm",
        "xhtml",
    }
)


def _final_extension(filename: str | None) -> str:
    """Return the case-folded final suffix from a client-supplied name."""
    basename = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in basename or basename.endswith("."):
        return ""
    return basename.rsplit(".", 1)[-1].casefold()


def _reject_dangerous_upload_filename(filename: str | None) -> None:
    extension = _final_extension(filename)
    if extension in DANGEROUS_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Dangerous file extension '.{extension}' is not allowed",
        )


async def drain_upload_delete_tasks() -> None:
    await _upload_delete_tasks.drain()


def _parse_bool(value: Any) -> bool:
    """Parse boolean value from various types."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes", "on")
    return bool(value)


router = APIRouter()


async def _get_live_record_by_hash(
    file_hash: str,
    storage=None,
    *,
    user_id: str | None = None,
    source: str | None = None,
) -> dict | None:
    """Return a dedupe record only if both metadata and the backing file still exist."""
    try:
        record = await _file_record_storage.find_by_hash(
            file_hash,
            user_id=user_id,
            source=source,
        )
    except TypeError:
        # Small compatibility doubles and third-party adapters may still expose
        # the pre-owner-aware method signature.
        record = await _file_record_storage.find_by_hash(file_hash)
    if user_id is not None and record is not None:
        owner = record.get("user_id") or record.get("uploaded_by")
        if owner != user_id:
            return None
    if record is None:
        return None

    storage = storage or await get_or_init_storage()
    if await storage.file_exists(record["key"]):
        return record

    logger.warning(
        "Found stale file record for hash %s pointing to missing key %s",
        file_hash,
        record["key"],
    )
    try:
        await _file_record_storage.delete_by_hash(file_hash, user_id=user_id)
    except TypeError:
        await _file_record_storage.delete_by_hash(file_hash)
    return None


def _get_base_url(request: Request) -> str:
    """获取 base_url，优先 APP_BASE_URL 环境变量，fallback 到 request.base_url。

    APP_BASE_URL 必须是合法的 http(s) URL 才会被采用；默认占位符（示例
    注释）等非 URL 值会被忽略，避免拼出 vision 模型无法 fetch 的废 URL。
    """
    app_base_url = (getattr(settings, "APP_BASE_URL", "") or "").strip()
    if app_base_url.startswith("http://") or app_base_url.startswith("https://"):
        return app_base_url.rstrip("/")
    base_url = str(request.base_url).rstrip("/")
    if base_url == "http://None":
        return ""
    return base_url


def _build_upload_response(
    request: Request,
    *,
    key: str,
    name: str,
    file_type: str,
    mime_type: str,
    size: int,
    exists: bool = False,
    file_id: str | None = None,
    source: str | None = None,
    status: str | None = None,
    storage_usage: Any | None = None,
    logical_url: str | None = None,
) -> dict:
    """Build a normalized upload response payload."""
    base_url = _get_base_url(request)
    proxy_url = logical_url or f"{base_url}/api/upload/file/{key}"
    payload = {
        "key": key,
        "url": proxy_url,
        "name": name,
        "type": file_type,
        "mime_type": mime_type,
        "size": size,
    }
    if exists:
        payload["exists"] = True
    if file_id:
        payload["file_id"] = file_id
    if source:
        payload["source"] = source
    if status:
        payload["status"] = status
    if storage_usage is not None:
        payload["storage_usage"] = (
            storage_usage.model_dump(mode="json")
            if hasattr(storage_usage, "model_dump")
            else storage_usage
        )
    return payload


def _avatar_object_key_from_url(avatar_url: str | None, user_id: str) -> str | None:
    if not avatar_url:
        return None

    parsed = urlsplit(avatar_url)
    path = unquote(parsed.path or avatar_url)
    proxy_prefix = "/api/upload/file/"
    if proxy_prefix in path:
        key = path.split(proxy_prefix, 1)[1]
    else:
        key = path.lstrip("/")

    owned_prefix = f"avatars/{user_id}/"
    if key.startswith(owned_prefix):
        return key
    return None


def _logical_file_id_from_url(file_url: str | None) -> str | None:
    if not file_url or "/api/storage/files/" not in file_url:
        return None
    value = file_url.split("/api/storage/files/", 1)[1].split("/", 1)[0]
    return value or None


async def _legacy_avatar_is_referenced(key: str) -> bool:
    """Keep an old avatar readable only while its profile still points to it."""
    parts = key.split("/")
    if len(parts) < 3 or parts[0] != "avatars" or not parts[1]:
        return False
    try:
        from src.infra.user.storage import UserStorage

        user = await UserStorage().get_by_id(parts[1])
    except Exception as exc:
        logger.debug("Legacy avatar ownership lookup failed for %s: %s", key, exc)
        return False
    avatar_url = getattr(user, "avatar_url", None) if user else None
    return _avatar_object_key_from_url(avatar_url, parts[1]) == key


async def _delete_avatar_object_if_owned(
    storage: Any,
    user_id: str,
    avatar_url: str | None,
    *,
    keep_key: str | None = None,
) -> None:
    key = _avatar_object_key_from_url(avatar_url, user_id)
    if key is None or key == keep_key:
        return

    try:
        await storage.delete_file(key)
    except Exception as e:
        logger.warning("Failed to delete previous avatar object %s: %s", key, e, exc_info=True)


def _path_exists(file_path) -> bool:
    return file_path.exists()


async def _get_file_response_metadata(key: str) -> tuple[str | None, str]:
    record = await _file_record_storage.find_by_key(key)
    if record is None:
        try:
            try:
                record = await UserStorageQuotaService().get_content_file(key, allow_storage_key=True)
            except TypeError:
                record = await UserStorageQuotaService().get_content_file(key)
        except Exception:
            # Metadata lookup must not make legacy system-artifact reads fail if
            # the additive Mongo collections are unavailable.
            record = None
    filename_for_disposition = record["name"] if record else None
    if filename_for_disposition:
        filename_for_disposition = (
            str(filename_for_disposition)
            .replace("\r", "")
            .replace("\n", "")
            .replace("/", "_")
            .replace("\\", "_")
            .replace('"', "'")[:255]
        )
    content_type = record["mime_type"] if record and record.get("mime_type") else None

    if not content_type:
        import mimetypes

        content_type, _ = mimetypes.guess_type(key)
        if not content_type:
            content_type = "application/octet-stream"

    return filename_for_disposition, content_type


async def _read_upload_file_limited(
    file: Any,
    *,
    max_size_bytes: int,
    max_size_mb: int,
    purpose: str = "File",
    chunk_size: int = UPLOAD_READ_CHUNK_SIZE,
) -> bytes:
    """Read an UploadFile in chunks and stop as soon as the configured limit is exceeded."""
    data = bytearray()
    total_size = 0

    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break

        total_size += len(chunk)
        if total_size > max_size_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"{purpose} size exceeds maximum of {max_size_mb}MB",
            )
        data.extend(chunk)

    return bytes(data)


class UploadSpool(BinaryReadFile, Protocol):
    def close(self) -> None: ...


@dataclass
class SpooledUpload:
    file: UploadSpool
    sha256_hex: str
    size: int

    def close(self) -> None:
        self.file.close()


async def _spool_upload_file_limited(
    file: Any,
    *,
    max_size_bytes: int,
    max_size_mb: int,
    purpose: str = "File",
    chunk_size: int = UPLOAD_READ_CHUNK_SIZE,
) -> SpooledUpload:
    """Stream an UploadFile into a bounded spool while hashing and enforcing size limits."""
    digest = hashlib.sha256()
    total_size = 0
    spooled = SpooledTemporaryFile(max_size=UPLOAD_SPOOL_MEMORY_LIMIT, mode="w+b")

    try:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break

            total_size += len(chunk)
            if total_size > max_size_bytes:
                raise HTTPException(
                    status_code=400,
                    detail=f"{purpose} size exceeds maximum of {max_size_mb}MB",
                )
            digest.update(chunk)
            await run_blocking_io(spooled.write, chunk)

        await run_blocking_io(spooled.seek, 0)
        return SpooledUpload(file=spooled, sha256_hex=digest.hexdigest(), size=total_size)
    except Exception:
        spooled.close()
        raise


def get_s3_enabled() -> bool:
    """Get S3 enabled status from cached settings"""
    return _parse_bool(settings.S3_ENABLED)


async def get_s3_config_from_settings() -> S3Config:
    """Get S3 configuration from cached settings"""
    if not get_s3_enabled():
        return settings.get_s3_config()

    provider_map = {
        "aws": S3Provider.AWS,
        "aliyun": S3Provider.ALIYUN,
        "tencent": S3Provider.TENCENT,
        "minio": S3Provider.MINIO,
        "custom": S3Provider.CUSTOM,
        "local": S3Provider.LOCAL,
    }

    storage_path = getattr(settings, "LOCAL_STORAGE_PATH", "./uploads") or "./uploads"

    return S3Config(
        provider=provider_map.get(str(settings.S3_PROVIDER).lower(), S3Provider.AWS),
        endpoint_url=settings.S3_ENDPOINT_URL if settings.S3_ENDPOINT_URL else None,
        access_key=str(settings.S3_ACCESS_KEY) if settings.S3_ACCESS_KEY else "",
        secret_key=str(settings.S3_SECRET_KEY) if settings.S3_SECRET_KEY else "",
        region=str(settings.S3_REGION) if settings.S3_REGION else "us-east-1",
        bucket_name=str(settings.S3_BUCKET_NAME) if settings.S3_BUCKET_NAME else "",
        custom_domain=settings.S3_CUSTOM_DOMAIN if settings.S3_CUSTOM_DOMAIN else None,
        path_style=_parse_bool(settings.S3_PATH_STYLE),
        public_bucket=_parse_bool(settings.S3_PUBLIC_BUCKET),
        max_file_size=(int(settings.S3_MAX_FILE_SIZE) if settings.S3_MAX_FILE_SIZE else 10485760),
        internal_max_upload_size=(
            int(settings.S3_INTERNAL_UPLOAD_MAX_SIZE)
            if settings.S3_INTERNAL_UPLOAD_MAX_SIZE
            else 50 * 1024 * 1024
        ),
        presigned_url_expires=(
            int(settings.S3_PRESIGNED_URL_EXPIRES)
            if settings.S3_PRESIGNED_URL_EXPIRES
            else 7 * 24 * 3600
        ),
        storage_path=storage_path,
    )


async def get_or_init_storage():
    """Initialize and get storage service (re-exported from infra layer)"""
    from src.infra.storage.s3.service import get_or_init_storage as _get_or_init

    return await _get_or_init()


async def resolve_upload_limits(user_roles: list[str]) -> dict:
    """Resolve effective upload limits for a user based on their roles.

    Most permissive value across roles wins. Falls back to global settings.
    """
    from src.infra.role.storage import RoleStorage

    defaults = {
        "image": settings.FILE_UPLOAD_MAX_SIZE_IMAGE,
        "video": settings.FILE_UPLOAD_MAX_SIZE_VIDEO,
        "audio": settings.FILE_UPLOAD_MAX_SIZE_AUDIO,
        "document": settings.FILE_UPLOAD_MAX_SIZE_DOCUMENT,
        "maxFiles": settings.FILE_UPLOAD_MAX_FILES,
    }

    field_map = {
        "image": "max_file_size_image",
        "video": "max_file_size_video",
        "audio": "max_file_size_audio",
        "document": "max_file_size_document",
        "maxFiles": "max_files",
    }

    resolved = dict(defaults)
    role_overrides: dict[str, int] = {}

    try:
        role_storage = RoleStorage()
        for role_name in user_roles:
            role = await role_storage.get_by_name(role_name)
            if role and role.limits:
                for key, field_name in field_map.items():
                    value = getattr(role.limits, field_name, None)
                    if value is not None:
                        role_overrides[key] = max(role_overrides.get(key, value), value)

        # Only apply role overrides for fields where at least one role set a value
        resolved.update(role_overrides)
    except Exception as e:
        logger.warning(f"Failed to resolve role upload limits, using defaults: {e}")

    return resolved


def _storage_error_http_exception(exc: StorageDomainError) -> HTTPException:
    """Map domain errors without exposing ownership or physical-key details."""
    usage = exc.usage if isinstance(exc, StorageQuotaExceededError) else None
    detail: dict[str, Any] = {"code": exc.code, "message": str(exc)}
    if usage is not None:
        detail["usage"] = usage.model_dump(mode="json")
        detail["required_bytes"] = exc.required_bytes
    return HTTPException(status_code=exc.status_code, detail=detail)


def _request_header(request: Request, name: str) -> str | None:
    headers = getattr(request, "headers", {})
    try:
        value = headers.get(name)
    except AttributeError:
        value = None
    return str(value).strip() if value else None


def _safe_compatibility_key(key: str) -> bool:
    """Reject traversal/encoded separators before any legacy provider call."""
    raw = str(key or "")
    lowered = raw.casefold()
    if not raw or len(raw.encode("utf-8")) > 1024 or "\x00" in raw or "%2f" in lowered or "%5c" in lowered:
        return False
    decoded = unquote(raw)
    if "\x00" in decoded or "\\" in decoded or decoded.startswith("/"):
        return False
    return not any(part in {"", ".", ".."} for part in decoded.split("/"))


def _is_legacy_personal_record(record: dict[str, Any] | None) -> bool:
    """Keep system-artifact records on their owning cleanup/read paths."""

    if record is None:
        return False
    source = str(record.get("source") or "legacy").casefold()
    return source not in LEGACY_SYSTEM_SOURCES


class FileCheckRequest(BaseModel):
    hash: str = Field(
        ...,
        min_length=64,
        max_length=64,
        pattern="^[0-9a-fA-F]{64}$",
        description="SHA-256 hex digest",
    )
    size: int = Field(..., gt=0, description="File size in bytes")
    name: str = Field(..., min_length=1, max_length=1024, description="Original filename")
    mime_type: str = Field(..., min_length=1, max_length=255, description="MIME type")


@router.post("/check")
async def check_file_exists(
    request: Request,
    body: FileCheckRequest,
    current_user: TokenPayload = Depends(get_current_user_required),
) -> dict:
    domain = UserStorageQuotaService()
    modern = await domain.storage.find_active_by_hash(current_user.sub, StorageSource.CHAT, body.hash)
    if modern:
        blob = await domain.storage.get_blob(str(modern.get("blob_id")))
        if blob and blob.get("status") == "active":
            usage = await domain.get_usage(current_user.sub, roles=current_user.roles)
            file_id = str(modern.get("_id") or modern.get("file_id"))
            base_url = _get_base_url(request)
            return _build_upload_response(
                request,
                key=str(blob.get("storage_key") or modern.get("storage_key") or ""),
                name=str(modern.get("name") or body.name),
                file_type=str(modern.get("category") or "unknown"),
                mime_type=str(modern.get("mime_type") or body.mime_type),
                size=int(modern.get("size", body.size)),
                exists=True,
                file_id=file_id,
                source=str(modern.get("source") or StorageSource.CHAT.value),
                status=str(modern.get("status") or FileLifecycleStatus.ACTIVE.value),
                storage_usage=usage,
                logical_url=f"{base_url}/api/storage/files/{file_id}/content",
            )

    # Legacy compatibility is still owner-scoped.  A global hash lookup would
    # reveal another user's key and is no longer permitted.
    storage = await get_or_init_storage()
    record = await _get_live_record_by_hash(
        body.hash,
        storage,
        user_id=current_user.sub,
        source=StorageSource.CHAT.value,
    )
    if record is None:
        return {"exists": False}
    base_url = _get_base_url(request)
    return {
        "exists": True,
        "key": record["key"],
        "url": f"{base_url}/api/upload/file/{record['key']}",
        "name": record["name"],
        "type": record["category"],
        "mime_type": record["mime_type"],
        "size": record["size"],
    }


@router.post("/file")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    current_user: TokenPayload = Depends(get_current_user_required),
) -> dict:
    """
    Upload a file to S3

    Requires: file:upload:{type} permission based on file type
    Files are stored in folders organized by user_id.

    Args:
        request: FastAPI request object (for base_url)
        file: File to upload
        current_user: Current authenticated user

    Returns:
        Upload result with URL and metadata
    """
    # Reject active/native final suffixes before initializing storage or reading
    # the multipart body.  MIME and earlier compound suffixes are irrelevant.
    _reject_dangerous_upload_filename(file.filename)
    storage = await get_or_init_storage()

    # Determine file category from filename and content_type (no need to read content)
    category = get_file_category(file.filename or "", file.content_type)
    permission = get_permission_for_category(category)

    # Check permission
    has_specific = False
    has_general = False

    if permission:
        has_specific = check_permission(current_user.permissions, permission)
    has_general = check_permission(current_user.permissions, "file:upload")

    if not (has_specific or has_general):
        category_label = category.value if category != FileCategory.UNKNOWN else "未知"
        raise HTTPException(
            status_code=403,
            detail=f"No permission to upload {category_label} files",
        )

    # Resolve per-role upload limits
    upload_limits = await resolve_upload_limits(current_user.roles)
    size_limits = {
        FileCategory.IMAGE: upload_limits["image"],
        FileCategory.VIDEO: upload_limits["video"],
        FileCategory.AUDIO: upload_limits["audio"],
        FileCategory.DOCUMENT: upload_limits["document"],
        FileCategory.UNKNOWN: 10,
    }
    max_size_mb = size_limits.get(category, 10)
    max_size_bytes = max_size_mb * 1024 * 1024

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_size_bytes:
                raise HTTPException(
                    status_code=400,
                    detail=f"File size exceeds maximum of {max_size_mb}MB",
                )
        except ValueError:
            pass

    # Validate file extension
    ext = (file.filename or "").lower().split(".")[-1]
    allowed_exts = FILE_EXTENSIONS.get(category, set())
    if category != FileCategory.UNKNOWN and ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"File extension '.{ext}' is not allowed for {category.value} files",
        )

    spooled_upload: SpooledUpload | None = None
    storage_key = ""
    file_hash = ""
    prepared = None
    object_written = False
    domain: UserStorageQuotaService | None = None
    try:
        spooled_upload = await _spool_upload_file_limited(
            file,
            max_size_bytes=max_size_bytes,
            max_size_mb=max_size_mb,
        )
        file_hash = spooled_upload.sha256_hex

        domain = UserStorageQuotaService()
        # Every new personal object is immutable and user/source scoped.  The
        # logical file row is staged and quota is reserved before this write.
        basename = str(file.filename or "unknown").replace("\\", "/").rsplit("/", 1)[-1]
        ext = basename.rsplit(".", 1)[-1].casefold() if "." in basename and not basename.endswith(".") else ""
        short_id = uuid.uuid4().hex
        storage_key = (
            f"managed/chat/{current_user.sub}/{short_id}.{ext}"
            if ext
            else f"managed/chat/{current_user.sub}/{short_id}"
        )
        idempotency_key = _request_header(request, "x-idempotency-key") or f"upload:{file_hash}"
        try:
            prepared = await domain.prepare_create(
                current_user.sub,
                source=StorageSource.CHAT,
                name=basename or "unknown",
                mime_type=file.content_type or "application/octet-stream",
                category=category.value,
                size=spooled_upload.size,
                content_hash=file_hash,
                storage_key=storage_key,
                idempotency_key=idempotency_key,
                roles=current_user.roles,
            )
        except StorageDomainError as exc:
            raise _storage_error_http_exception(exc) from exc

        if prepared.reused:
            existing = await domain.storage.get_file(prepared.file_id)
            usage = await domain.get_usage(current_user.sub, roles=current_user.roles)
            if existing:
                return _build_upload_response(
                    request,
                    key=str(existing.get("storage_key") or prepared.storage_key),
                    name=str(existing.get("name") or basename),
                    file_type=str(existing.get("category") or category.value),
                    mime_type=str(existing.get("mime_type") or file.content_type or "application/octet-stream"),
                    size=int(existing.get("size", prepared.size)),
                    exists=True,
                    file_id=prepared.file_id,
                    source=str(existing.get("source") or StorageSource.CHAT.value),
                    status=str(existing.get("status") or FileLifecycleStatus.ACTIVE.value),
                    storage_usage=usage,
                    logical_url=f"{_get_base_url(request)}/api/storage/files/{prepared.file_id}/content",
                )
        upload_result = await storage.upload_stream_to_key(
            file=spooled_upload.file,
            key=prepared.storage_key,
            content_type=file.content_type,
            metadata={"uploaded_by": current_user.sub, "content_hash": file_hash},
            skip_size_limit=True,
        )
        if upload_result.key != prepared.storage_key:
            raise RuntimeError("storage backend changed the immutable managed key")
        storage_key = prepared.storage_key
        object_written = True
        user_file, usage = await domain.complete_create(prepared, roles=current_user.roles)

        return _build_upload_response(
            request,
            key=storage_key,
            name=basename or "unknown",
            file_type=category.value,
            mime_type=file.content_type or "application/octet-stream",
            size=spooled_upload.size,
            file_id=user_file.file_id,
            source=user_file.source.value,
            status=user_file.status.value,
            storage_usage=usage,
            logical_url=f"{_get_base_url(request)}/api/storage/files/{user_file.file_id}/content",
        )
    except HTTPException:
        if prepared is not None and not object_written:
            try:
                await (domain or UserStorageQuotaService()).compensate_create(current_user.sub, prepared.operation_id)
            except Exception as compensation_error:
                logger.error("Upload reservation compensation failed: %s", compensation_error, exc_info=True)
        raise
    except StorageDomainError as exc:
        if prepared is not None and not object_written:
            try:
                await (domain or UserStorageQuotaService()).compensate_create(current_user.sub, prepared.operation_id)
            except Exception as compensation_error:
                logger.error("Upload reservation compensation failed: %s", compensation_error, exc_info=True)
        raise _storage_error_http_exception(exc) from exc
    except Exception as e:
        if prepared is not None and not object_written:
            try:
                await (domain or UserStorageQuotaService()).compensate_create(current_user.sub, prepared.operation_id)
            except Exception as compensation_error:
                logger.error("Upload reservation compensation failed: %s", compensation_error, exc_info=True)
        if object_written:
            logger.error("Upload object was written but lifecycle finalization failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
    finally:
        if spooled_upload is not None:
            spooled_upload.close()


def _get_image_content_type(data: bytes) -> str:
    """Detect image content type from binary data using magic bytes"""
    # Check magic bytes to detect image type
    # Safety check: ensure data is long enough for magic byte detection
    if len(data) < 2:
        return "image/png"  # Default for empty/very small data

    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    elif data[:2] == b"\xff\xd8":
        return "image/jpeg"
    elif len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    elif data[:2] in (b"BM", b"BA"):
        return "image/bmp"
    else:
        return "image/png"  # Default to PNG


@router.post("/avatar", dependencies=[Depends(require_permissions("avatar:upload"))])
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: TokenPayload = Depends(get_current_user_required),
) -> dict:
    """
    Upload user avatar

    Avatar is stored in object storage and referenced by URL.

    Requires: file:upload permission

    Args:
        file: Avatar image file
        current_user: Current authenticated user

    Returns:
        Avatar data URI
    """
    # Validate file type
    allowed_image_extensions = ["jpg", "jpeg", "png", "gif", "webp"]
    ext = (
        (file.filename or "avatar.png").lower().split(".")[-1]
        if "." in (file.filename or "")
        else ""
    )
    if ext not in allowed_image_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File type '.{ext}' is not allowed. Allowed types: {', '.join(allowed_image_extensions)}",
        )

    # Validate file size (max 2MB for avatar)
    max_size = 2 * 1024 * 1024  # 2MB
    spooled_upload = await _spool_upload_file_limited(
        file,
        max_size_bytes=max_size,
        max_size_mb=2,
        purpose="Avatar file",
    )

    try:
        header = await run_blocking_io(spooled_upload.file.read, 12)
        content_type = _get_image_content_type(header)
        await run_blocking_io(spooled_upload.file.seek, 0)
    except Exception:
        spooled_upload.close()
        raise


    domain: UserStorageQuotaService | None = None
    modern_avatar = False
    prepared = None
    object_written = False
    try:
        from src.infra.user.storage import UserStorage
        from src.kernel.schemas.user import UserUpdate

        storage = await get_or_init_storage()
        domain = UserStorageQuotaService()
        modern_avatar = hasattr(storage, "upload_stream_to_key")
        if modern_avatar:
            avatar_name = (file.filename or "avatar.png").replace("\\", "/").rsplit("/", 1)[-1]
            avatar_key = f"managed/profile_avatar/{current_user.sub}/{uuid.uuid4().hex}.png"
            try:
                prepared = await domain.prepare_create(
                    current_user.sub,
                    source=StorageSource.PROFILE_AVATAR,
                    source_ref="profile.avatar",
                    name=avatar_name,
                    mime_type=content_type,
                    category="image",
                    size=spooled_upload.size,
                    content_hash=hashlib.sha256(
                        await run_blocking_io(spooled_upload.file.read)
                    ).hexdigest(),
                    storage_key=avatar_key,
                    idempotency_key=f"avatar:{current_user.sub}:{hashlib.sha256(avatar_key.encode()).hexdigest()}",
                    roles=current_user.roles,
                )
                await run_blocking_io(spooled_upload.file.seek, 0)
                if not prepared.reused:
                    await storage.upload_stream_to_key(
                        file=spooled_upload.file,
                        key=prepared.storage_key,
                        content_type=content_type,
                        metadata={"uploaded_by": current_user.sub, "source": StorageSource.PROFILE_AVATAR.value},
                        skip_size_limit=True,
                    )
                    object_written = True
                    _user_file, usage = await domain.complete_create(prepared, roles=current_user.roles)
                else:
                    usage = await domain.get_usage(current_user.sub, roles=current_user.roles)
                avatar_url = f"/api/storage/files/{prepared.file_id}/content"
            except StorageDomainError as exc:
                raise _storage_error_http_exception(exc) from exc
        else:
            # Compatibility doubles and older storage adapters still expose the
            # historical avatar helper.  They do not participate in modern quota
            # accounting, but real adapters always take the branch above.
            upload_result = await storage.upload_file(
                file=spooled_upload.file,
                folder=f"avatars/{current_user.sub}",
                filename=file.filename or "avatar.png",
                content_type=content_type,
                skip_size_limit=True,
            )
            avatar_url = upload_result.url or f"/api/upload/file/{upload_result.key}"

        logger.info(f"Uploading avatar for user: {current_user.sub}, filename: {file.filename}")
        user_storage = UserStorage()
        previous_user = await user_storage.get_by_id(current_user.sub)
        previous_avatar_url = getattr(previous_user, "avatar_url", None)
        await user_storage.update(
            current_user.sub,
            UserUpdate(avatar_url=avatar_url),
        )
        if modern_avatar and prepared is not None and prepared.replaced_file_id:
            usage = await domain.finalize_replace(prepared)
        await _delete_avatar_object_if_owned(
            storage,
            current_user.sub,
            previous_avatar_url,
            keep_key=(prepared.storage_key if modern_avatar and prepared is not None else upload_result.key),
        )
        logger.info(f"Avatar uploaded successfully for user: {current_user.sub}")

        response = {
            "url": avatar_url,
            "size": spooled_upload.size,
            "content_type": content_type,
        }
        if modern_avatar and prepared is not None:
            response.update(
                {
                    "file_id": prepared.file_id,
                    "source": StorageSource.PROFILE_AVATAR.value,
                    "status": FileLifecycleStatus.ACTIVE.value,
                    "storage_usage": usage.model_dump(mode="json"),
                }
            )
        return response
    except HTTPException:
        raise
    except Exception as e:
        if modern_avatar and domain is not None and prepared is not None and not object_written:
            try:
                await domain.compensate_create(current_user.sub, prepared.operation_id)
            except Exception as compensation_error:
                logger.error("Avatar reservation compensation failed: %s", compensation_error, exc_info=True)
        logger.exception("Avatar upload failed")
        raise HTTPException(status_code=500, detail=f"Avatar upload failed: {str(e)}")
    finally:
        spooled_upload.close()


@router.post("/asset/{asset_source}/{owner_ref}")
async def upload_managed_asset(
    asset_source: Literal["persona", "team"],
    owner_ref: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: TokenPayload = Depends(get_current_user_required),
) -> dict:
    """Upload a protected Persona/Team avatar through its owning scope.

    The owner reference is supplied by the owning editor (a persisted entity ID
    or a draft token); arbitrary folder names never select a storage source.
    """

    permission = "persona_preset:write" if asset_source == "persona" else "team:write"
    if not check_permission(current_user.permissions, permission):
        raise HTTPException(status_code=403, detail=f"No permission to upload {asset_source} avatar")
    clean_owner_ref = unquote(str(owner_ref or "")).strip()
    if (
        not clean_owner_ref
        or len(clean_owner_ref.encode("utf-8")) > 128
        or not all(character.isalnum() or character in {"-", "_"} for character in clean_owner_ref)
    ):
        raise HTTPException(status_code=400, detail="Invalid avatar owner reference")
    _reject_dangerous_upload_filename(file.filename)
    basename = str(file.filename or "avatar.png").replace("\\", "/").rsplit("/", 1)[-1]
    extension = basename.rsplit(".", 1)[-1].casefold() if "." in basename else ""
    if extension not in {"jpg", "jpeg", "png", "gif", "webp"}:
        raise HTTPException(status_code=400, detail="Avatar must be an image")
    spooled = await _spool_upload_file_limited(
        file,
        max_size_bytes=2 * 1024 * 1024,
        max_size_mb=2,
        purpose="Avatar file",
    )
    try:
        header = await run_blocking_io(spooled.file.read, 12)
        content_type = _get_image_content_type(header)
        await run_blocking_io(spooled.file.seek, 0)
        source = (
            StorageSource.PERSONA_AVATAR
            if asset_source == "persona"
            else StorageSource.TEAM_AVATAR
        )
        domain = UserStorageQuotaService()
        content_hash = hashlib.sha256(await run_blocking_io(spooled.file.read)).hexdigest()
        await run_blocking_io(spooled.file.seek, 0)
        prepared = await domain.prepare_create(
            current_user.sub,
            source=source,
            source_ref=f"{source.value}:{clean_owner_ref}",
            name=basename,
            mime_type=content_type,
            category="image",
            size=spooled.size,
            content_hash=content_hash,
            storage_key=f"managed/{source.value}/{current_user.sub}/{uuid.uuid4().hex}.{extension}",
            idempotency_key=_request_header(request, "x-idempotency-key")
            or f"{source.value}:{current_user.sub}:{clean_owner_ref}:{content_hash}",
            roles=current_user.roles,
        )
        if prepared.reused:
            owner = await domain.storage.get_file(prepared.file_id)
            usage = await domain.get_usage(current_user.sub, roles=current_user.roles)
            return _build_upload_response(
                request,
                key=str(owner.get("storage_key") if owner else prepared.storage_key),
                name=basename,
                file_type="image",
                mime_type=content_type,
                size=spooled.size,
                file_id=prepared.file_id,
                source=source.value,
                status=FileLifecycleStatus.ACTIVE.value,
                storage_usage=usage,
                logical_url=f"{_get_base_url(request)}/api/storage/files/{prepared.file_id}/content",
            )
        storage = await get_or_init_storage()
        await storage.upload_stream_to_key(
            file=spooled.file,
            key=prepared.storage_key,
            content_type=content_type,
            metadata={"uploaded_by": current_user.sub, "source": source.value},
            skip_size_limit=True,
        )
        _owner, usage = await domain.complete_create(prepared, roles=current_user.roles)
        return _build_upload_response(
            request,
            key=prepared.storage_key,
            name=basename,
            file_type="image",
            mime_type=content_type,
            size=spooled.size,
            file_id=prepared.file_id,
            source=source.value,
            status=FileLifecycleStatus.PENDING.value if prepared.replaced_file_id else FileLifecycleStatus.ACTIVE.value,
            storage_usage=usage,
            logical_url=f"{_get_base_url(request)}/api/storage/files/{prepared.file_id}/content",
        )
    except StorageDomainError as exc:
        try:
            if "prepared" in locals() and prepared is not None:
                await UserStorageQuotaService().compensate_create(current_user.sub, prepared.operation_id)
        except Exception as compensation_error:
            logger.error("Managed avatar reservation compensation failed: %s", compensation_error)
        raise _storage_error_http_exception(exc) from exc
    except Exception as exc:
        try:
            if "prepared" in locals() and prepared is not None:
                await UserStorageQuotaService().compensate_create(current_user.sub, prepared.operation_id)
        except Exception as compensation_error:
            logger.error("Managed avatar reservation compensation failed: %s", compensation_error)
        raise HTTPException(status_code=500, detail="Avatar upload failed") from exc
    finally:
        spooled.close()



@router.delete("/avatar", dependencies=[Depends(require_permissions("avatar:upload"))])
async def delete_avatar(
    current_user: TokenPayload = Depends(get_current_user_required),
) -> dict:
    """
    Delete user avatar

    Removes the avatar_url from the user's profile.
    Requires: avatar:upload permission

    Args:
        current_user: Current authenticated user

    Returns:
        Deletion status
    """
    try:
        from src.infra.user.storage import UserStorage
        from src.kernel.schemas.user import UserUpdate

        logger.info(f"Deleting avatar for user: {current_user.sub}")
        user_storage = UserStorage()
        previous_user = await user_storage.get_by_id(current_user.sub)
        previous_avatar_url = getattr(previous_user, "avatar_url", None)
        await user_storage.update(
            current_user.sub,
            UserUpdate(avatar_url=None),
        )
        modern_file_id = _logical_file_id_from_url(previous_avatar_url)
        modern_result = None
        if modern_file_id:
            modern_result = await UserStorageQuotaService().delete_protected_file(
                current_user.sub,
                modern_file_id,
                reason="profile_avatar_removed",
            )
        object_storage = await get_or_init_storage()
        await _delete_avatar_object_if_owned(
            object_storage,
            current_user.sub,
            previous_avatar_url,
        )
        logger.info(f"Avatar deleted successfully for user: {current_user.sub}")

        response = {"deleted": True}
        if modern_result is not None:
            response.update(modern_result)
        return response
    except Exception as e:
        if isinstance(e, StorageDomainError):
            raise _storage_error_http_exception(e) from e
        logger.exception("Avatar deletion failed")
        raise HTTPException(status_code=500, detail=f"Avatar deletion failed: {str(e)}")


@router.delete("/{key:path}", dependencies=[Depends(require_permissions("file:upload"))])
async def delete_file(
    key: str,
    current_user: TokenPayload = Depends(get_current_user_required),
) -> dict:
    """
    Delete a file from S3

    Requires: file:upload permission

    Args:
        key: File key to delete
        current_user: Current authenticated user

    Returns:
        Deletion status
    """
    user_id = getattr(current_user, "sub", None)
    if user_id:
        # New callers may pass a logical file id or a physical key, but both
        # are resolved through the owner-scoped domain row first.
        domain = UserStorageQuotaService()
        owned = await domain.storage.get_owned_file(
            user_id,
            key,
            include_deleted=True,
            allow_storage_key=True,
        )
        if owned:
            result = await domain.delete_file(user_id, str(owned.get("_id") or key))
            if result.get("logical_status") == "managed_by_source":
                raise _storage_error_http_exception(StorageManagedBySourceError("文件必须通过所属功能管理"))
            return {"deleted": result.get("logical_status") in {"deleted", "already_deleted"}, "key": key, **result}

        # Legacy records are only compatible when they carry an explicit owner.
        try:
            record = await _file_record_storage.find_by_key(key, user_id=user_id, include_deleted=True)
        except TypeError:
            record = await _file_record_storage.find_by_key(key)
        if record is not None and (record.get("user_id") or record.get("uploaded_by")) != user_id:
            record = None
        if record is not None and not _is_legacy_personal_record(record):
            record = None
        if record is None:
            raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不存在"})
        if record.get("status") in {"deleted", "delete_pending"}:
            return {"deleted": False, "key": key, "status": "already_deleted"}
        # Legacy records do not prove the complete owner set of a physical key;
        # tombstone only and leave physical cleanup to migration/purge after
        # ownership reconstruction.
        await _file_record_storage.delete_by_key(key, user_id=user_id, logical=True)
        return {"deleted": True, "key": key, "status": "deleted", "physical_status": "quarantined"}

    # A missing identity is only retained for old internal callers/tests; the
    # authenticated public route above never permits an untracked raw delete.
    storage = await get_or_init_storage()
    record = await _file_record_storage.find_by_key(key)
    if record is not None:
        # This compatibility-only no-identity branch cannot prove ownership.
        # Legacy reference_count is deliberately not a purge authority.
        return {"deleted": False, "key": key, "status": "preserved"}

    async def background_delete():
        try:
            await storage.delete_file(key)
            await _file_record_storage.delete_by_key(key)
            logger.info("Background delete completed for key: %s", key)
        except Exception as e:
            logger.error("Background delete failed for key %s: %s", key, e)

    _upload_delete_tasks.create_task(background_delete())
    return {"deleted": True, "key": key, "status": "deleting"}


@router.get("/config")
async def get_storage_config(
    current_user: TokenPayload = Depends(get_current_user_required),
) -> dict:
    """
    Get storage configuration status and file upload limits

    Returns effective upload limits for the current user based on their roles.
    Falls back to global settings if no role-specific limits are configured.

    Returns:
        Storage configuration and upload limits
    """
    s3_enabled = get_s3_enabled()

    # Resolve per-role upload limits for current user
    upload_limits = await resolve_upload_limits(current_user.roles)

    return {
        "enabled": True,  # Always enabled (local storage as fallback)
        "provider": settings.S3_PROVIDER if s3_enabled else "local",
        "uploadLimits": {
            "image": upload_limits["image"],
            "video": upload_limits["video"],
            "audio": upload_limits["audio"],
            "document": upload_limits["document"],
            "maxFiles": upload_limits["maxFiles"],
        },
    }


# ============================================================================
# Signed URL API (for private buckets)
# ============================================================================


class SignedUrlRequest(BaseModel):
    """Request model for getting signed URLs"""

    keys: list[str] = Field(
        ...,
        min_length=1,
        max_length=SIGNED_URL_KEYS_MAX,
        description="List of S3 object keys to get signed URLs for",
    )
    expires: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="URL expiration time in seconds (default 1 hour, max 24 hours)",
    )

    @field_validator("keys")
    @classmethod
    def validate_keys(cls, values: list[str]) -> list[str]:
        for key in values:
            if not key or len(key.encode("utf-8")) > 1024:
                raise ValueError("keys must be non-empty and at most 1024 UTF-8 bytes")
        return values


class SignedUrlItem(BaseModel):
    """Single signed URL result"""

    key: str
    url: str | None = None
    error: str | None = None


class SignedUrlResponse(BaseModel):
    """Response model for signed URLs"""

    urls: list[SignedUrlItem]
    expires_in: int


@router.post(
    "/signed-urls",
    response_model=SignedUrlResponse,
    dependencies=[Depends(require_permissions("file:upload"))],
)
async def get_signed_urls(
    body: SignedUrlRequest,
    req: Request,
    current_user: TokenPayload = Depends(get_current_user_required),
) -> SignedUrlResponse:
    """
    Get presigned URLs for private S3 objects

    This endpoint generates temporary signed URLs that can be used to access
    private files stored in S3. The URLs expire after the specified time.

    Args:
        request: Contains list of S3 keys and optional expiration time
        current_user: Current authenticated user

    Returns:
        List of signed URLs for each requested key
    """
    base_url = _get_base_url(req)
    urls = []
    domain = UserStorageQuotaService()
    storage = None
    for key in body.keys:
        if not _safe_compatibility_key(key):
            urls.append(SignedUrlItem(key=key, error="File not found"))
            continue
        managed = await domain.storage.get_owned_file(
            current_user.sub,
            key,
            include_deleted=True,
            allow_storage_key=True,
        )
        if managed:
            if managed.get("status") in {
                FileLifecycleStatus.DELETE_PENDING.value,
                FileLifecycleStatus.DELETED.value,
            }:
                urls.append(SignedUrlItem(key=key, error="file_deleted"))
                continue
            blob = await domain.storage.get_blob(str(managed.get("blob_id")))
            if not blob or blob.get("status") != "active":
                urls.append(SignedUrlItem(key=key, error="File not found"))
                continue
            file_id = str(managed.get("_id") or managed.get("file_id"))
            # New managed files never expose a direct/presigned object URL.
            urls.append(SignedUrlItem(key=key, url=f"{base_url}/api/storage/files/{file_id}/content"))
            continue

        # Legacy compatibility signing is still owner-scoped and short-lived.
        try:
            legacy = await _file_record_storage.find_by_key(key, user_id=current_user.sub)
        except TypeError:
            legacy = await _file_record_storage.find_by_key(key)
        if legacy is not None and (legacy.get("user_id") or legacy.get("uploaded_by")) != current_user.sub:
            legacy = None
        if legacy is not None and not _is_legacy_personal_record(legacy):
            legacy = None
        if legacy is None or legacy.get("status") in {"deleted", "delete_pending"}:
            urls.append(SignedUrlItem(key=key, error="File not found"))
            continue
        if storage is None:
            storage = await get_or_init_storage()
        try:
            if storage.is_local:
                url = f"{base_url}/api/upload/file/{key}"
            elif storage._config.public_bucket:
                url = await storage.get_file_url(key)
            else:
                url = await storage.get_presigned_url(key, min(body.expires, 300))
            urls.append(SignedUrlItem(key=key, url=url))
        except Exception as e:
            logger.warning(f"Failed to generate signed URL for {key}: {e}")
            urls.append(SignedUrlItem(key=key, error=str(e)))

    # A zero expiry denotes logical managed URLs; legacy private signatures are
    # capped to five minutes by the branch above.
    return SignedUrlResponse(
        urls=urls,
        expires_in=0 if all(item.url and "/api/storage/files/" in item.url for item in urls) else min(body.expires, 300),
    )


@router.get(
    "/signed-url",
    response_model=SignedUrlItem,
    dependencies=[Depends(require_permissions("file:upload"))],
)
async def get_single_signed_url(
    key: str,
    request: Request,
    expires: int = 3600,
    current_user: TokenPayload = Depends(get_current_user_required),
) -> SignedUrlItem:
    """
    Get a single presigned URL for a private S3 object

    Convenience endpoint for getting a single signed URL.

    Args:
        key: S3 object key
        expires: URL expiration time in seconds (default 1 hour)
        current_user: Current authenticated user

    Returns:
        Signed URL for the requested key
    """
    # Validate expires range
    if expires < 60 or expires > 86400:
        raise HTTPException(
            status_code=400,
            detail="expires must be between 60 and 86400 seconds",
        )
    if not _safe_compatibility_key(key):
        return SignedUrlItem(key=key, error="File not found")

    base_url = _get_base_url(request)

    domain = UserStorageQuotaService()
    managed = await domain.storage.get_owned_file(
        current_user.sub,
        key,
        include_deleted=True,
        allow_storage_key=True,
    )
    if managed:
        if managed.get("status") in {
            FileLifecycleStatus.DELETE_PENDING.value,
            FileLifecycleStatus.DELETED.value,
        }:
            return SignedUrlItem(key=key, error="file_deleted")
        blob = await domain.storage.get_blob(str(managed.get("blob_id")))
        if not blob or blob.get("status") != "active":
            return SignedUrlItem(key=key, error="File not found")
        file_id = str(managed.get("_id") or managed.get("file_id"))
        return SignedUrlItem(key=key, url=f"{base_url}/api/storage/files/{file_id}/content")

    try:
        legacy = await _file_record_storage.find_by_key(key, user_id=current_user.sub)
    except TypeError:
        legacy = await _file_record_storage.find_by_key(key)
    if legacy is not None and (legacy.get("user_id") or legacy.get("uploaded_by")) != current_user.sub:
        legacy = None
    if legacy is not None and not _is_legacy_personal_record(legacy):
        legacy = None
    if legacy is None or legacy.get("status") in {"deleted", "delete_pending"}:
        return SignedUrlItem(key=key, error="File not found")
    storage = await get_or_init_storage()

    try:
        if storage.is_local:
            exists = await storage.file_exists(key)
            if not exists:
                return SignedUrlItem(key=key, error="File not found")
            return SignedUrlItem(key=key, url=f"{base_url}/api/upload/file/{key}")
        # If bucket is public, return direct URL
        if storage._config.public_bucket:
            url = await storage.get_file_url(key)
        else:
            url = await storage.get_presigned_url(key, min(expires, 300))
        return SignedUrlItem(key=key, url=url)
    except Exception as e:
        logger.warning(f"Failed to generate signed URL for {key}: {e}")
        return SignedUrlItem(key=key, error=str(e))


@router.get("/file/{key:path}")
async def get_file_proxy(
    key: str,
    request: Request,
    direct: bool = False,
    proxy: bool = False,
) -> Response:
    """Read a managed logical object or an explicitly allowlisted legacy object."""
    from fastapi.responses import JSONResponse

    if not _safe_compatibility_key(key):
        raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不存在"})
    base_url = _get_base_url(request)
    # Check historical metadata first; this preserves reads for existing
    # system/domain objects without allowing an unknown key to become readable.
    try:
        legacy_record = await _file_record_storage.find_by_key(key, include_deleted=True)
    except TypeError:
        legacy_record = await _file_record_storage.find_by_key(key)
    managed_record = None
    managed = False
    if legacy_record is not None:
        if not _is_legacy_personal_record(legacy_record):
            raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不存在"})
        if legacy_record.get("status") in {"deleted", "delete_pending"}:
            raise HTTPException(
                status_code=410,
                detail={"code": "file_deleted", "message": "文件已删除", "key": key},
                headers={"Cache-Control": "private, no-store"},
            )
    else:
        try:
            try:
                managed_record = await UserStorageQuotaService().get_content_file(key, allow_storage_key=True)
            except TypeError:
                managed_record = await UserStorageQuotaService().get_content_file(key)
        except Exception as exc:
            logger.warning("Managed file lookup failed for compatibility key %s: %s", key, exc)
            managed_record = None
        if managed_record is not None:
            # A physical managed key is never a second lifecycle endpoint.  In
            # particular, do not reveal another user's tombstone through a key
            # that may have leaked from an old event; only the opaque logical ID
            # route carries the documented 410 metadata.
            if "/" in key:
                raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不存在"})
            if managed_record.get("status") in {
                FileLifecycleStatus.DELETED.value,
                FileLifecycleStatus.DELETE_PENDING.value,
            }:
                raise HTTPException(
                    status_code=410,
                    detail={
                        "code": "file_deleted",
                        "message": "文件已删除",
                        "file_id": str(managed_record.get("_id") or managed_record.get("file_id")),
                    },
                    headers={"Cache-Control": "private, no-store"},
                )
            # New managed rows are addressable only through their opaque logical
            # file ID.  A physical key (or an ID supplied to this legacy route)
            # must not become a second content authorization path.
            raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不存在"})
        elif not any(key.startswith(prefix) for prefix in LEGACY_PUBLIC_OBJECT_PREFIXES) and not await _legacy_avatar_is_referenced(key):
            # Unknown/raw personal keys are non-enumerating and never touch the
            # backing storage provider.
            raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不存在"})

    storage = await get_or_init_storage()
    physical_key = key
    logical_file_id = None
    if managed and managed_record is not None:
        logical_file_id = str(managed_record.get("_id") or managed_record.get("file_id"))
        blob = await UserStorageQuotaService().storage.get_blob(str(managed_record.get("blob_id")))
        if not blob or blob.get("status") != "active":
            raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不存在"})
        physical_key = str(blob.get("storage_key") or managed_record.get("storage_key") or key)

    logical_url = f"{base_url}/api/storage/files/{logical_file_id}/content" if logical_file_id else f"{base_url}/api/upload/file/{key}"
    if direct and managed:
        return JSONResponse({"url": logical_url}, headers={"Cache-Control": "private, no-store"})

    # Managed content is always streamed through the app so lifecycle state is
    # checked on every request; only legacy compatibility objects may redirect.
    if managed:
        if not await storage.file_exists(physical_key):
            raise HTTPException(status_code=404, detail={"code": "file_not_found", "message": "文件不存在"})
        filename_for_disposition, content_type = await _get_file_response_metadata(key)
        headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
        if filename_for_disposition:
            headers["Content-Disposition"] = f'inline; filename="{filename_for_disposition}"'
        return StreamingResponse(
            storage.download_stream(physical_key),
            media_type=content_type,
            headers=headers,
        )

    proxy_url = f"{base_url}/api/upload/file/{key}"
    if storage.is_local:
        if direct:
            return JSONResponse({"url": proxy_url})
        try:
            file_path = storage.get_file_path(key)
            if not await run_blocking_io(_path_exists, file_path):
                raise HTTPException(status_code=404, detail="File not found")
            filename_for_disposition, content_type = await _get_file_response_metadata(key)
            return FileResponse(
                path=str(file_path),
                media_type=content_type,
                filename=filename_for_disposition,
                content_disposition_type="inline",
                headers={"Cache-Control": "public, max-age=86400"},
            )
        except HTTPException:
            raise
        except (OSError, ValueError) as exc:
            logger.warning("Failed to serve legacy local file %s: %s", key, exc)
            raise HTTPException(status_code=404, detail="File not found") from exc

    try:
        if not await storage.file_exists(key):
            raise HTTPException(status_code=404, detail="File not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Failed to check legacy file existence for %s: %s", key, e)

    if proxy:
        filename_for_disposition, content_type = await _get_file_response_metadata(key)
        headers = {"Cache-Control": "public, max-age=300"}
        if filename_for_disposition:
            headers["Content-Disposition"] = f'inline; filename="{filename_for_disposition}"'
        return StreamingResponse(storage.download_stream(key), media_type=content_type, headers=headers)

    try:
        url = (
            await storage.get_file_url(key)
            if storage._config.public_bucket
            else await storage.get_presigned_url(key, 300)
        )
    except Exception as e:
        logger.error("Failed to generate legacy file URL for %s: %s", key, e)
        raise HTTPException(status_code=500, detail="Failed to generate file URL") from e
    if direct:
        return JSONResponse({"url": url})
    return Response(status_code=302, headers={"Location": url, "Cache-Control": "public, max-age=300"})
