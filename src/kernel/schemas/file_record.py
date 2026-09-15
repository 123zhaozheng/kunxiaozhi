"""Legacy file record schema kept as a compatibility/migration input."""

from datetime import datetime

from pydantic import BaseModel, Field

from src.infra.utils.datetime import utc_now


class FileRecordSchema(BaseModel):
    """Represents a historical physical record.

    New managed uploads use the storage-domain schemas.  The optional fields are
    additive so old records can still be decoded while migration is in progress.
    """

    id: str = Field(alias="_id")
    hash: str  # SHA-256 hex digest
    key: str  # Storage object key, e.g. "user_id/abc123hash"
    name: str  # Original filename
    mime_type: str
    size: int
    category: str  # "image", "video", "audio", "document"
    uploaded_by: str  # User ID of first uploader (legacy semantics)
    user_id: str | None = None
    source: str = "legacy"
    file_id: str | None = None
    status: str = "active"
    deleted_at: datetime | None = None
    deleted_reason: str | None = None
    reference_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)

    model_config = {"populate_by_name": True}
