"""Domain errors for the personal knowledge base; routes map them to HTTP codes."""

from __future__ import annotations


class KBError(Exception):
    default_message = "知识库操作失败"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)


class KBNotFoundError(KBError):
    """Missing or inaccessible knowledge base (deliberately indistinguishable)."""

    default_message = "知识库不存在"


class KBDocumentNotFoundError(KBError):
    default_message = "文档不存在"


class KBTaskNotFoundError(KBError):
    default_message = "任务不存在"


class KBDisabledError(KBError):
    default_message = "知识库功能未开启"


class KBValidationError(KBError, ValueError):
    default_message = "参数不合法"


class KBForbiddenError(KBError):
    default_message = "无权执行此操作"


class KBBusyError(KBError):
    default_message = "知识库正在处理文档，请稍后再试"


class KBQuotaError(KBError):
    """A quota or size limit was hit; ``code`` is one of the ``kb_quota_*``/``kb_file_too_large``."""

    default_message = "已超出知识库额度"

    def __init__(self, code: str, message: str, limit: int) -> None:
        super().__init__(message)
        self.code = code
        self.limit = limit


class KBConflictError(KBError):
    default_message = "操作冲突"


class KBDraftExpiredError(KBError):
    default_message = "草稿已过期，请让助手重新生成"
