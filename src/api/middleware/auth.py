"""
认证中间件
"""

import re

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class AuthMiddleware(BaseHTTPMiddleware):
    """
    认证中间件

    验证请求中的 JWT token。
    Note: Most routes use route-level Depends(get_current_user_required) for auth.
    This middleware provides an additional layer for paths that may not have
    route-level guards.
    """

    # 不需要认证的路径（精确匹配）
    PUBLIC_PATHS = {
        "/",
        "/health",
        "/ready",
        "/api/auth/login",
        "/api/auth/login/oa-sso",
        "/api/auth/register",
        "/docs",
        "/openapi.json",
        "/api/auth/permissions",
        "/manifest.json",
        "/sw.js",
        "/offline.html",
        "/api/version",
        "/robots.txt",
        "/sitemap.xml",
        "/index.html",
    }

    # 不需要认证的路径前缀
    PUBLIC_PREFIXES = (
        "/api/auth/oauth/",
        "/api/auth/refresh",
        "/api/auth/forgot-password",
        "/api/auth/reset-password",
        "/api/auth/verify-email",
        "/api/auth/resend-verification",
        "/api/upload/file/",
        # 沙箱/工具链无凭据拉取逻辑文件内容；file_id 为 128 位随机 hex，
        # 与 /api/upload/file/{key} 的能力令牌模型一致。
        "/api/storage/files/",
        "/assets/",
        "/icons/",
        "/images/",
        "/emoji-assets/",
        "/shared/",
        "/api/share/public/",
        "/api/agents",
        "/auth/",
        "/favicon",
        "/static/",
    )

    # Exact-match exemptions for capability-token style reads.  A broad
    # "/api/storage/files/" prefix would also exempt files/status,
    # files/{id} DELETE and files/batch-delete, so the pattern is pinned to the
    # content endpoint of a 128-bit hex file_id.
    PUBLIC_PATH_PATTERNS = (
        re.compile(r"^/api/storage/files/[0-9a-f]{32}/content(?:/.*)?$"),
    )

    @staticmethod
    def _is_browser_page_request(request: Request) -> bool:
        """
        Allow unauthenticated browser navigations for SPA routes.

        API/XHR requests usually send ``Accept: application/json`` or ``*/*``,
        while full page navigations include ``text/html``. This keeps backend
        APIs protected and lets the frontend router handle routes like
        ``/models`` after the request reaches the SPA fallback.
        """
        if request.method not in {"GET", "HEAD"}:
            return False

        accept = request.headers.get("accept", "")
        return "text/html" in accept.lower()

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # CORS preflight — always pass
        if request.method == "OPTIONS":
            return await call_next(request)

        # Exact match on public paths
        if path in self.PUBLIC_PATHS:
            return await call_next(request)

        # Prefix match for known public prefixes
        for prefix in self.PUBLIC_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Exact-pattern public endpoints (unauthenticated capability reads).
        for pattern in self.PUBLIC_PATH_PATTERNS:
            if pattern.match(path):
                return await call_next(request)

        # Let browser page navigations reach the SPA fallback / redirect route.
        if self._is_browser_page_request(request):
            return await call_next(request)

        # All other paths require an Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated"},
            )

        return await call_next(request)
