# src/api/routes/builtin_skill.py
"""内置 Skill 管理 API（admin only）。

提供 admin 通过 zip 上传或从商城加载创建内置 Skill、并管理角色绑定与启停的端点。
路由风格对齐 ``src/api/routes/marketplace.py`` 与 ``src/api/routes/skill.py``：

- 全部端点挂 ``require_permissions("manage_builtin_skills")``。
- zip 解析与大小校验复用 ``skill`` 模块的 ``_parse_zip_skill_preview`` /
  ``_parse_zip_skills`` / ``_get_skill_upload_max_size``（内部已 ``_sync_zip_upload_limits``）。
- 创建来源（zip / marketplace）的缓存失效在 storage 层完成；更新与删除在路由层显式调用
  ``invalidate_cache`` 失效全局 effective-skills 缓存。
"""

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from src.api.deps import require_permissions
from src.api.routes.skill import (
    _get_skill_upload_max_size,
    _parse_zip_skill_preview,
    _parse_zip_skills,
)
from src.api.routes.upload import _read_upload_file_limited
from src.infra.async_utils import run_blocking_io
from src.infra.skill.builtin import BuiltinSkillStorage
from src.infra.skill.builtin_copy import (
    delete_builtin_namespace_objects,
    delete_skill_name_from_all_users,
)
from src.infra.skill.marketplace import MarketplaceStorage
from src.infra.skill.types import (
    BuiltinSkill,
    BuiltinSkillResponse,
    BuiltinSkillUpdate,
    MarketplaceSkillResponse,
)
from src.kernel.schemas.user import TokenPayload

router = APIRouter()


def get_builtin_storage() -> BuiltinSkillStorage:
    return BuiltinSkillStorage()


def get_marketplace_storage() -> MarketplaceStorage:
    return MarketplaceStorage()


class FromMarketplaceRequest(BaseModel):
    """从商城加载为内置 Skill 的请求"""

    marketplace_name: str
    allowed_roles: list[str] = []


def _to_response(skill: BuiltinSkill) -> BuiltinSkillResponse:
    """将 builtin 元数据转为 admin 响应（单文件计数默认 0，列表接口才有完整 file_count）。"""
    return BuiltinSkillResponse.model_validate(skill.model_dump())


# ==========================================
# 列表
# ==========================================


@router.get("/", response_model=list[BuiltinSkillResponse])
async def list_builtin_skills(
    include_inactive: bool = Query(True),
    allowed_role: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user: TokenPayload = Depends(require_permissions("manage_builtin_skills")),
    builtin_storage: BuiltinSkillStorage = Depends(get_builtin_storage),
):
    """列出内置 Skill（admin 视图，含文件数量）"""
    return await builtin_storage.list_builtin_skills(
        include_inactive=include_inactive,
        allowed_role=allowed_role,
        source=source,
        skip=skip,
        limit=limit,
    )


@router.get("/marketplace", response_model=list[MarketplaceSkillResponse])
async def list_marketplace_sources_for_builtin(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user: TokenPayload = Depends(require_permissions("manage_builtin_skills")),
    marketplace: MarketplaceStorage = Depends(get_marketplace_storage),
):
    """List active Marketplace Skills available to Builtin administrators."""
    return await marketplace.list_marketplace_skills(
        active_only=True,
        viewer_id=user.sub,
        skip=skip,
        limit=limit,
    )


# ==========================================
# zip 来源
# ==========================================


@router.post("/zip/preview")
async def preview_zip_skills(
    file: UploadFile,
    user: TokenPayload = Depends(require_permissions("manage_builtin_skills")),
    builtin_storage: BuiltinSkillStorage = Depends(get_builtin_storage),
):
    """预览 ZIP 文件中的 skills（不落盘），并标记已存在的内置 Skill。"""
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="File must be a ZIP archive")

    try:
        max_size_bytes, max_size_mb = _get_skill_upload_max_size()
        content = await _read_upload_file_limited(
            file,
            max_size_bytes=max_size_bytes,
            max_size_mb=max_size_mb,
            purpose="ZIP file",
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read file content")

    try:
        skill_list = await run_blocking_io(_parse_zip_skill_preview, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 标记已存在的内置 Skill（per-skill 查询，避免加载全部元数据）
    for skill in skill_list:
        existing = await builtin_storage.get_builtin_skill(skill["name"])
        skill["already_exists"] = existing is not None

    return {
        "skill_count": len(skill_list),
        "skills": skill_list,
    }


@router.post("/zip", status_code=201)
async def upload_builtin_skill_from_zip(
    file: UploadFile,
    allowed_roles: list[str] = Form(default_factory=list),
    user: TokenPayload = Depends(require_permissions("manage_builtin_skills")),
    builtin_storage: BuiltinSkillStorage = Depends(get_builtin_storage),
):
    """从 ZIP 上传创建内置 Skill（支持一个 zip 内多个 skill）。"""
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="File must be a ZIP archive")

    try:
        max_size_bytes, max_size_mb = _get_skill_upload_max_size()
        content = await _read_upload_file_limited(
            file,
            max_size_bytes=max_size_bytes,
            max_size_mb=max_size_mb,
            purpose="ZIP file",
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to read file content")

    try:
        parsed = await run_blocking_io(_parse_zip_skills, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        created = await builtin_storage.import_parsed_skills(
            parsed, allowed_roles, user.sub
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if not created:
        raise HTTPException(status_code=400, detail="No valid skills found in ZIP")

    return {
        "message": f"Created {len(created)} builtin skill(s)",
        "created": created,
        "skill_count": len(created),
    }


# ==========================================
# 商城来源
# ==========================================


@router.post("/from-marketplace", response_model=BuiltinSkillResponse, status_code=201)
async def create_builtin_from_marketplace(
    data: FromMarketplaceRequest,
    user: TokenPayload = Depends(require_permissions("manage_builtin_skills")),
    builtin_storage: BuiltinSkillStorage = Depends(get_builtin_storage),
):
    """从商城 Skill 复制创建内置 Skill。"""
    try:
        builtin = await builtin_storage.create_from_marketplace(
            data.marketplace_name, data.allowed_roles, user.sub
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=409, detail=msg)
    return _to_response(builtin)


# ==========================================
# 更新 / 删除
# ==========================================


@router.patch("/{name}", response_model=BuiltinSkillResponse)
async def update_builtin_skill(
    name: str,
    data: BuiltinSkillUpdate,
    user: TokenPayload = Depends(require_permissions("manage_builtin_skills")),
    builtin_storage: BuiltinSkillStorage = Depends(get_builtin_storage),
):
    """更新内置 Skill（角色 / 描述 / 启停）。"""
    updated = await builtin_storage.update_builtin_skill(name, data)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Builtin skill '{name}' not found")
    await builtin_storage.invalidate_cache()
    return _to_response(updated)


@router.delete("/{name}")
async def delete_builtin_skill(
    name: str,
    user: TokenPayload = Depends(require_permissions("manage_builtin_skills")),
    builtin_storage: BuiltinSkillStorage = Depends(get_builtin_storage),
):
    """删除内置 Skill（中央库 + 所有用户空间中的同名技能）。"""
    existing = await builtin_storage.get_builtin_skill(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Builtin skill '{name}' not found")
    await delete_skill_name_from_all_users(name)
    deleted = await builtin_storage.delete_builtin_skill(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Builtin skill '{name}' not found")
    await delete_builtin_namespace_objects(name)
    await builtin_storage.invalidate_cache()
    return {"message": f"Builtin skill '{name}' deleted"}
