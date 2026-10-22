"""Personal knowledge base API schemas and state constants."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

# ── document states (design §3) ──────────────────────────────
DOC_UPLOADED = "uploaded"
DOC_PARSING = "parsing"
DOC_PARSED = "parsed"
DOC_INDEXING = "indexing"
DOC_INDEXED = "indexed"
DOC_ERROR_PARSING = "error_parsing"
DOC_ERROR_INDEXING = "error_indexing"

DOC_PROCESSING_STATUSES = (DOC_UPLOADED, DOC_PARSING, DOC_PARSED, DOC_INDEXING)
DOC_FAILED_STATUSES = (DOC_ERROR_PARSING, DOC_ERROR_INDEXING)
DOC_ALL_STATUSES = (*DOC_PROCESSING_STATUSES, DOC_INDEXED, *DOC_FAILED_STATUSES)

# ── task states (design §2 kb_tasks) ─────────────────────────
TASK_QUEUED = "queued"
TASK_RUNNING = "running"
TASK_SUCCEEDED = "succeeded"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"

TASK_ACTIVE_STATUSES = (TASK_QUEUED, TASK_RUNNING)
TASK_TERMINAL_STATUSES = (TASK_SUCCEEDED, TASK_FAILED, TASK_CANCELLED)
TASK_TYPE_INGEST = "ingest"

# ── kb states / roles ────────────────────────────────────────
KB_STATUS_ACTIVE = "active"
KB_STATUS_DELETING = "deleting"
KB_ROLE_OWNER = "owner"
KB_ROLE_EDITOR = "editor"
KB_ROLE_VIEWER = "viewer"
KB_ROLE_SUBSCRIBER = "subscriber"
KB_ROLE_VISITOR = "visitor"
KB_SHARE_ROLES = (KB_ROLE_EDITOR, KB_ROLE_VIEWER)
PUBLIC_NONE = "none"
PUBLIC_PENDING = "pending"
PUBLIC_APPROVED = "approved"
PUBLIC_REJECTED = "rejected"
PUBLIC_DELISTED = "delisted"
PUBLIC_STATUSES = (PUBLIC_NONE, PUBLIC_PENDING, PUBLIC_APPROVED, PUBLIC_REJECTED, PUBLIC_DELISTED)
KB_SCOPES = ("mine", "shared", "subscribed", "writable", "readable")
QUOTA_COUNT = "kb_quota_count"
QUOTA_DOCS = "kb_quota_docs"
QUOTA_STORAGE = "kb_quota_storage"
FILE_TOO_LARGE = "kb_file_too_large"

DEFAULT_KB_ICON = "library-big"

_MONGO_ID = AliasChoices("id", "_id")


class _KBModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class KBEmbeddingInfo(_KBModel):
    model_name: str = Field(..., description="Embedding model the index was built with")
    dim: int = Field(..., description="Vector dimension")


class KBStats(_KBModel):
    doc_count: int = 0
    indexed_count: int = 0
    processing_count: int = 0
    failed_count: int = 0
    chunk_count: int = 0
    size_bytes: int = 0


class KBTaskProgress(_KBModel):
    total: int = 0
    done: int = 0
    failed: int = 0
    current_doc_id: Optional[str] = None


class KBTask(_KBModel):
    id: str = Field(..., validation_alias=_MONGO_ID)
    kb_id: str
    type: str = TASK_TYPE_INGEST
    status: str
    progress: KBTaskProgress = Field(default_factory=KBTaskProgress)
    attempts: int = 0
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class KBDocumentError(_KBModel):
    stage: str
    message: str
    at: Optional[datetime] = None


class KBDocument(_KBModel):
    id: str = Field(..., validation_alias=_MONGO_ID)
    kb_id: str
    filename: str
    ext: str = ""
    mime: str = ""
    size: int = 0
    status: str
    error: Optional[KBDocumentError] = None
    parse_method: Optional[str] = None
    chunk_count: int = 0
    token_count: int = 0
    line_count: int = 0
    uploader_username: str = ""
    source: str = "upload"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    indexed_at: Optional[datetime] = None


class KBReview(_KBModel):
    action: str = ""
    reviewer_id: str = ""
    reviewer_username: str = ""
    reason: str = ""
    at: Optional[datetime] = None


class KnowledgeBase(_KBModel):
    id: str = Field(..., validation_alias=_MONGO_ID)
    name: str
    description: str = ""
    icon: str = DEFAULT_KB_ICON
    owner_id: str
    owner_username: str = ""
    my_role: str = KB_ROLE_OWNER
    chunk_preset: str = "general"
    chunk_token_num: int = 512
    overlap_percent: int = 0
    visibility: str = "private"
    public_status: str = PUBLIC_NONE
    published_at: Optional[datetime] = None
    review: Optional["KBReview"] = None
    subscriber_count: int = 0
    can_write: bool = False
    can_manage: bool = False
    can_chat: bool = True
    chat_enabled: Optional[bool] = None
    graph_enabled: bool = False
    embedding: Optional[KBEmbeddingInfo] = None
    index_outdated: bool = False
    stats: KBStats = Field(default_factory=KBStats)
    active_task: Optional[KBTask] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class KBChatEnabledUpdate(_KBModel):
    enabled: bool


class KBCreate(_KBModel):
    """Create payload; range checks happen in the service so they answer 400."""

    name: str
    description: str = ""
    icon: Optional[str] = None
    chunk_preset: Optional[str] = None
    chunk_token_num: Optional[int] = None
    overlap_percent: Optional[int] = None


class KBUpdate(_KBModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    chunk_preset: Optional[str] = None
    chunk_token_num: Optional[int] = None
    overlap_percent: Optional[int] = None


class KBPreset(_KBModel):
    key: str
    chunk_token_num: int
    overlap_percent: int


class KBConfig(_KBModel):
    enabled: bool
    ready: bool
    not_ready_reason: Optional[str] = None
    presets: list[KBPreset] = Field(default_factory=list)
    allowed_extensions: list[str] = Field(default_factory=list)
    mineru_enabled: bool = False
    max_file_mb: int = 100
    rerank_available: bool = False
    allow_public: bool = True
    public_requires_review: bool = False


class KBListResponse(_KBModel):
    items: list[KnowledgeBase] = Field(default_factory=list)


class KBDocumentListResponse(_KBModel):
    items: list[KBDocument] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50


class KBUploadResponse(_KBModel):
    document: KBDocument
    duplicate: bool = False


class KBDocIdsRequest(_KBModel):
    doc_ids: list[str] = Field(default_factory=list)


class KBTaskEnqueueResponse(_KBModel):
    task_id: Optional[str] = None
    count: int = 0


class KBDeleteDocumentsResponse(_KBModel):
    deleted: int = 0


class KBContentLine(_KBModel):
    n: int
    text: str


class KBContentResponse(_KBModel):
    doc_id: str
    filename: str
    total_lines: int
    offset: int
    lines: list[KBContentLine] = Field(default_factory=list)
    has_more: bool = False


class KBChunk(_KBModel):
    chunk_id: str = Field(..., validation_alias=AliasChoices("chunk_id", "_id"))
    index: int
    text: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    token_count: int = 0


class KBChunkListResponse(_KBModel):
    items: list[KBChunk] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class KBSearchRequest(_KBModel):
    query: str
    top_k: int = 10
    mode: str = "hybrid"
    use_rerank: Optional[bool] = None
    score_threshold: float = 0.0
    vector_weight: float = 0.7


class KBSearchHit(_KBModel):
    chunk_id: str
    kb_id: str
    kb_name: str = ""
    doc_id: str
    filename: str
    index: int
    text: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    score: float
    rerank_score: Optional[float] = None


class KBSearchResponse(_KBModel):
    results: list[KBSearchHit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    took_ms: int = 0


class KBTaskListResponse(_KBModel):
    items: list[KBTask] = Field(default_factory=list)


# ── PR2: sharing, public KBs, quotas, notifications, import ──


class KBMember(_KBModel):
    user_id: str
    username: str = ""
    role: str
    granted_by: str = ""
    created_at: Optional[datetime] = None


class KBMemberListResponse(_KBModel):
    items: list[KBMember] = Field(default_factory=list)


class KBMemberAdd(_KBModel):
    usernames: list[str] = Field(..., min_length=1, max_length=50)
    role: str = KB_ROLE_VIEWER


class KBMemberAddError(_KBModel):
    username: str
    reason: str  # not_found | inactive | self


class KBMemberAddResponse(_KBModel):
    added: list[KBMember] = Field(default_factory=list)
    updated: list[KBMember] = Field(default_factory=list)
    errors: list[KBMemberAddError] = Field(default_factory=list)


class KBMemberUpdate(_KBModel):
    role: str


class KBUserResolve(_KBModel):
    username: str
    exists: bool
    active: bool


class KBUserResolveRequest(_KBModel):
    usernames: list[str] = Field(..., min_length=1, max_length=50)


class KBUserResolveResponse(_KBModel):
    items: list[KBUserResolve] = Field(default_factory=list)


class KBQuotaLimits(_KBModel):
    kb_max_count: int
    kb_max_docs_per_kb: int
    kb_storage_quota_mb: int
    kb_max_file_size_mb: int


class KBQuotaUsage(_KBModel):
    kb_count: int = 0
    doc_count: int = 0
    max_docs_in_kb: int = 0
    storage_bytes: int = 0


class KBQuotaResponse(_KBModel):
    limits: KBQuotaLimits
    usage: KBQuotaUsage


class KBPlazaResponse(_KBModel):
    items: list[KnowledgeBase] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class KBNotification(_KBModel):
    id: str = Field(..., validation_alias=_MONGO_ID)
    type: str
    kb_id: str = ""
    kb_name: str = ""
    actor_username: str = ""
    message: str = ""
    read: bool = False
    created_at: Optional[datetime] = None


class KBNotificationListResponse(_KBModel):
    items: list[KBNotification] = Field(default_factory=list)
    total: int = 0
    unread: int = 0
    page: int = 1
    page_size: int = 20


class KBUnreadCount(_KBModel):
    count: int = 0


class KBImportableFile(_KBModel):
    id: str
    file_name: str
    file_type: str = ""
    mime_type: str = ""
    file_size: int = 0
    created_at: Optional[str] = None


class KBImportableFileListResponse(_KBModel):
    items: list[KBImportableFile] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class KBImportRequest(_KBModel):
    file_ids: list[str] = Field(default_factory=list)


class KBImportResult(_KBModel):
    file_id: str
    status: str  # created | duplicate | skipped | failed
    code: Optional[str] = None
    message: Optional[str] = None
    document: Optional[KBDocument] = None


class KBImportResponse(_KBModel):
    results: list[KBImportResult] = Field(default_factory=list)
    created: int = 0
    duplicate: int = 0
    skipped: int = 0
    failed: int = 0


class KBReviewRequest(_KBModel):
    reason: str = ""


class KBAdminListResponse(_KBModel):
    items: list[KnowledgeBase] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class KBDraftConfirmRequest(_KBModel):
    kb_id: str
    title: Optional[str] = None


class KBDraft(_KBModel):
    draft_id: str
    status: str
    title: str = ""
    preview: str = ""
    char_count: int = 0
    suggested_kb_id: Optional[str] = None
    kb_id: Optional[str] = None
    document_id: Optional[str] = None
    expires_at: datetime


class KBDraftConfirmResponse(_KBModel):
    document: KBDocument
    kb_id: str
