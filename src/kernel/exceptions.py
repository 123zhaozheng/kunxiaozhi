"""
异常定义

定义系统中使用的所有自定义异常。
"""


class AgentError(Exception):
    """Agent 相关错误基类"""

    pass


class ConfigurationError(Exception):
    """配置错误"""

    pass


class ValidationError(Exception):
    """验证错误"""

    pass


class NotFoundError(Exception):
    """资源未找到错误"""

    pass


class AuthenticationError(Exception):
    """认证错误"""

    pass


class AuthorizationError(Exception):
    """授权错误"""

    pass


class StorageError(Exception):
    """存储错误"""

    pass


class LLMError(Exception):
    """LLM 调用错误"""

    pass


class ToolError(Exception):
    """工具执行错误"""

    pass


class SkillError(Exception):
    """技能相关错误"""

    pass


class SessionError(Exception):
    """会话相关错误"""

    pass


class SandboxCapacityUnavailable(Exception):  # noqa: N818
    """No healthy OpenSandbox node can accept a managed sandbox."""

    code = "sandbox_capacity_unavailable"
    message = "当前用户太多，沙盒资源有限～请先切换到 Fast 模式继续聊，或稍后再试。"


class EmailNotVerifiedError(Exception):
    """邮箱未验证错误"""

    def __init__(self, message: str, email: str):
        super().__init__(message)
        self.email = email


class AccountNotActiveError(Exception):
    """账户未激活错误"""

    def __init__(self, message: str, email: str):
        super().__init__(message)
        self.email = email
