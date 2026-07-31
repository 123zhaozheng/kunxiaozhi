"""内置 Skill 存储（admin 管理，按角色自动注入）。

存储结构对齐 ``MarketplaceStorage``：独立的 ``skill_builtin``（元数据）与
``skill_builtin_files``（文件）两个 collection，避免污染用户 skill 与商城查询。
二进制文件沿用 ``SkillBinaryRef`` 机制（content 字段存 ``_binary_ref`` JSON）。
"""

from typing import TYPE_CHECKING, Any, Optional

from src.infra.logging import get_logger
from src.infra.skill.binary import (
    SkillBinaryRef,
    build_binary_ref_content,
    build_storage_key,
    guess_mime_type,
)
from src.infra.skill.constants import (
    BUILTIN_SKILLS_VERSION_KEY,
    SKILL_BUILTIN_COLLECTION,
    SKILL_BUILTIN_FILES_COLLECTION,
)
from src.infra.skill.storage_helpers import normalize_skill_name_list
from src.infra.skill.types import (
    BuiltinSkill,
    BuiltinSkillCreate,
    BuiltinSkillResponse,
    BuiltinSkillUpdate,
)
from src.infra.storage.mongodb import get_mongo_client
from src.infra.utils.datetime import utc_now_iso
from src.kernel.config import settings

logger = get_logger(__name__)

# 内置 Skill 二进制文件在 S3/local 存储中的命名空间（替代 user_id 段）
BUILTIN_BINARY_NAMESPACE = "_builtin"

BUILTIN_FILE_COPY_BATCH_SIZE = 25
BUILTIN_FILES_PER_SKILL_LIMIT = 100
BUILTIN_SKILL_NAMES_LIMIT = 100  # 注入时单个用户最多合并的 builtin 数量上限

# parsed zip 条目类型：(skill_name, text_files, binary_files)
ParsedZipSkill = tuple[str, dict[str, str], dict[str, bytes]]


if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

    from src.infra.skill.marketplace import MarketplaceStorage


class BuiltinSkillStorage:
    """内置 Skill 存储"""

    def __init__(self):
        self._client: Optional["AsyncIOMotorClient"] = None
        self._meta_collection: Optional["AsyncIOMotorCollection"] = None
        self._files_collection: Optional["AsyncIOMotorCollection"] = None

    def _get_meta_collection(self) -> "AsyncIOMotorCollection":
        if self._meta_collection is None:
            self._client = get_mongo_client()
            db = self._client[settings.MONGODB_DB]
            self._meta_collection = db[SKILL_BUILTIN_COLLECTION]
        return self._meta_collection

    def _get_files_collection(self) -> "AsyncIOMotorCollection":
        if self._files_collection is None:
            self._client = get_mongo_client()
            db = self._client[settings.MONGODB_DB]
            self._files_collection = db[SKILL_BUILTIN_FILES_COLLECTION]
        return self._files_collection

    async def ensure_indexes(self) -> None:
        """创建索引"""
        meta = self._get_meta_collection()
        await meta.create_index("skill_name", unique=True, background=True)
        await meta.create_index("is_active", background=True)

        files = self._get_files_collection()
        await files.create_index(
            [("skill_name", 1), ("file_path", 1)],
            unique=True,
            background=True,
        )

    # ==========================================
    # 元数据操作
    # ==========================================

    async def get_builtin_skill(self, skill_name: str) -> Optional[BuiltinSkill]:
        """获取内置 Skill 元数据"""
        collection = self._get_meta_collection()
        doc = await collection.find_one({"skill_name": skill_name})
        if not doc:
            return None
        return self._doc_to_skill(doc)

    async def list_builtin_skills(
        self,
        *,
        include_inactive: bool = True,
        allowed_role: Optional[str] = None,
        source: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[BuiltinSkillResponse]:
        """列出内置 Skill（admin 视图，含文件数量）"""
        meta = self._get_meta_collection()
        query: dict[str, Any] = {}
        if not include_inactive:
            query["is_active"] = {"$ne": False}
        if source:
            query["source"] = source
        if allowed_role:
            # 匹配：allowed_roles 为空（全员）或包含该角色
            query["$or"] = [
                {"allowed_roles": {"$size": 0}},
                {"allowed_roles": {"$exists": False}},
                {"allowed_roles": allowed_role},
            ]

        pipeline: list[dict[str, Any]] = [
            {"$match": query},
            {"$sort": {"updated_at": -1}},
            {"$skip": skip},
            {"$limit": limit},
            {
                "$lookup": {
                    "from": SKILL_BUILTIN_FILES_COLLECTION,
                    "let": {"skill": "$skill_name"},
                    "pipeline": [
                        {"$match": {"$expr": {"$eq": ["$skill_name", "$$skill"]}}},
                        {"$count": "count"},
                    ],
                    "as": "_file_count_docs",
                }
            },
            {
                "$addFields": {
                    "_file_count": {
                        "$ifNull": [{"$first": "$_file_count_docs.count"}, 0]
                    }
                }
            },
            {"$unset": "_file_count_docs"},
        ]

        results: list[BuiltinSkillResponse] = []
        async for doc in meta.aggregate(pipeline):  # type: ignore[arg-type]
            results.append(self._doc_to_response(doc))
        return results

    async def create_builtin_skill(
        self, data: BuiltinSkillCreate, created_by: str
    ) -> BuiltinSkill:
        """创建内置 Skill 元数据"""
        collection = self._get_meta_collection()
        now = utc_now_iso()

        existing = await collection.find_one({"skill_name": data.skill_name})
        if existing:
            raise ValueError(f"Builtin skill '{data.skill_name}' already exists")

        doc = {
            "skill_name": data.skill_name,
            "description": data.description,
            "allowed_roles": list(data.allowed_roles),
            "source": data.source,
            "source_ref": data.source_ref,
            "is_active": True,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
        await collection.insert_one(doc)
        return self._doc_to_skill(doc)

    async def update_builtin_skill(
        self, skill_name: str, data: BuiltinSkillUpdate
    ) -> Optional[BuiltinSkill]:
        """更新内置 Skill 元数据（角色 / 描述 / 启停）"""
        collection = self._get_meta_collection()
        existing = await collection.find_one({"skill_name": skill_name})
        if not existing:
            return None

        update_data: dict[str, Any] = {"updated_at": utc_now_iso()}
        if data.description is not None:
            update_data["description"] = data.description
        if data.allowed_roles is not None:
            update_data["allowed_roles"] = list(data.allowed_roles)
        if data.is_active is not None:
            update_data["is_active"] = data.is_active

        await collection.update_one({"skill_name": skill_name}, {"$set": update_data})
        updated = await collection.find_one({"skill_name": skill_name})
        return self._doc_to_skill(updated) if updated else None

    async def set_builtin_active(
        self, skill_name: str, is_active: bool
    ) -> Optional[BuiltinSkill]:
        """激活或停用内置 Skill"""
        collection = self._get_meta_collection()
        now = utc_now_iso()
        result = await collection.find_one_and_update(
            {"skill_name": skill_name},
            {"$set": {"is_active": is_active, "updated_at": now}},
            return_document=True,
        )
        return self._doc_to_skill(result) if result else None

    async def delete_builtin_skill(self, skill_name: str) -> bool:
        """删除内置 Skill 元数据和所有文件"""
        meta = self._get_meta_collection()
        files = self._get_files_collection()

        meta_result = await meta.delete_one({"skill_name": skill_name})
        await files.delete_many({"skill_name": skill_name})

        return meta_result.deleted_count > 0

    # ==========================================
    # 注入查询
    # ==========================================

    async def list_builtin_skill_names_for_roles(
        self,
        user_roles: list[str],
        is_admin: bool,
    ) -> list[str]:
        """返回 is_active 且角色匹配的 skill_name 列表（注入用）。

        - is_admin=True：返回所有 active 的 builtin skill_name。
        - allowed_roles 为空：对所有角色生效。
        - 否则：用户 roles 与 allowed_roles 有交集即匹配。
        """
        collection = self._get_meta_collection()
        query: dict[str, Any] = {"is_active": {"$ne": False}}
        if not is_admin:
            roles = [r for r in (user_roles or []) if isinstance(r, str) and r]
            query["$or"] = [
                {"allowed_roles": {"$in": roles}},
                {"allowed_roles": {"$size": 0}},
                {"allowed_roles": {"$exists": False}},
            ]

        names: list[str] = []
        cursor = collection.find(query, {"skill_name": 1}).sort("skill_name", 1).limit(
            BUILTIN_SKILL_NAMES_LIMIT
        )
        async for doc in cursor:
            names.append(doc["skill_name"])
        return names

    # ==========================================
    # 文件操作
    # ==========================================

    async def get_builtin_files(self, skill_name: str) -> dict[str, str]:
        """获取内置 Skill 所有文件"""
        collection = self._get_files_collection()
        files: dict[str, str] = {}
        cursor = collection.find(
            {"skill_name": skill_name},
            {"_id": 0, "file_path": 1, "content": 1},
        ).limit(BUILTIN_FILES_PER_SKILL_LIMIT)
        async for doc in cursor:
            files[doc["file_path"]] = doc["content"]
        return files

    async def iter_builtin_file_batches(
        self,
        skill_name: str,
        *,
        batch_size: int = BUILTIN_FILE_COPY_BATCH_SIZE,
    ):
        """Yield builtin files in bounded batches without materializing all contents."""
        collection = self._get_files_collection()
        size = max(1, int(batch_size))
        cursor = (
            collection.find(
                {"skill_name": skill_name},
                {"_id": 0, "file_path": 1, "content": 1},
            )
            .sort("file_path", 1)
            .limit(BUILTIN_FILES_PER_SKILL_LIMIT)
            .batch_size(size)
        )
        batch: dict[str, str] = {}
        async for doc in cursor:
            batch[doc["file_path"]] = doc.get("content", "")
            if len(batch) >= size:
                yield batch
                batch = {}
        if batch:
            yield batch

    async def get_builtin_file(
        self, skill_name: str, file_path: str
    ) -> Optional[str]:
        """获取内置 Skill 单个文件"""
        collection = self._get_files_collection()
        doc = await collection.find_one(
            {"skill_name": skill_name, "file_path": file_path}
        )
        return doc["content"] if doc else None

    async def set_builtin_file(
        self, skill_name: str, file_path: str, content: str
    ) -> None:
        """设置内置 Skill 单个文件（文本或已构建的二进制引用 JSON）"""
        collection = self._get_files_collection()
        now = utc_now_iso()
        await collection.update_one(
            {"skill_name": skill_name, "file_path": file_path},
            {
                "$set": {"content": content, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def set_builtin_binary_file(
        self,
        skill_name: str,
        file_path: str,
        data: bytes,
        mime_type: Optional[str] = None,
    ) -> SkillBinaryRef:
        """上传二进制文件到 S3/local 存储，并在 MongoDB 存储引用。"""
        from src.infra.storage.s3.service import get_or_init_storage

        if not mime_type:
            mime_type = guess_mime_type(file_path)

        storage_key = build_storage_key(BUILTIN_BINARY_NAMESPACE, skill_name, file_path)
        storage_service = await get_or_init_storage()
        await storage_service.upload_to_key(
            data=data,
            key=storage_key,
            content_type=mime_type,
            skip_size_limit=True,
        )

        ref_content = build_binary_ref_content(storage_key, mime_type, len(data))
        await self.set_builtin_file(skill_name, file_path, ref_content)
        return SkillBinaryRef(
            storage_key=storage_key,
            mime_type=mime_type,
            size=len(data),
        )

    async def sync_builtin_files(
        self, skill_name: str, files: dict[str, str]
    ) -> None:
        """批量同步内置 Skill 文件（删除多余路径，upsert 提供的文件）"""
        if not files:
            return
        if len(files) > BUILTIN_FILES_PER_SKILL_LIMIT:
            raise ValueError(
                f"Builtin skill contains too many files "
                f"(max {BUILTIN_FILES_PER_SKILL_LIMIT})"
            )
        collection = self._get_files_collection()
        now = utc_now_iso()

        from pymongo import UpdateOne

        await collection.delete_many(
            {
                "skill_name": skill_name,
                "file_path": {"$nin": list(files.keys())},
            }
        )

        operations: list = []
        for file_path, content in files.items():
            operations.append(
                UpdateOne(
                    {"skill_name": skill_name, "file_path": file_path},
                    {
                        "$set": {"content": content, "updated_at": now},
                        "$setOnInsert": {"created_at": now},
                    },
                    upsert=True,
                )
            )
        if operations:
            await collection.bulk_write(operations, ordered=True)

    async def upsert_builtin_files_batch(
        self,
        skill_name: str,
        files: dict[str, str],
    ) -> int:
        """Upsert a bounded batch of builtin files without deleting other paths."""
        if not files:
            return 0

        from pymongo import UpdateOne

        collection = self._get_files_collection()
        now = utc_now_iso()
        operations = [
            UpdateOne(
                {"skill_name": skill_name, "file_path": file_path},
                {
                    "$set": {"content": content, "updated_at": now},
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )
            for file_path, content in files.items()
        ]
        await collection.bulk_write(operations, ordered=True)
        return len(operations)

    async def list_builtin_file_paths(self, skill_name: str) -> list[str]:
        """列出内置 Skill 所有文件路径"""
        collection = self._get_files_collection()
        paths: list[str] = []
        cursor = collection.find(
            {"skill_name": skill_name}, {"file_path": 1}
        ).limit(BUILTIN_FILES_PER_SKILL_LIMIT)
        async for doc in cursor:
            paths.append(doc["file_path"])
        return paths

    async def batch_get_builtin_skill_files(
        self, skill_names: list[str]
    ) -> dict[str, dict[str, str]]:
        """批量获取多个内置 Skill 的文件。

        Returns:
            ``{skill_name: {file_path: content}}``，对齐 ``SkillStorage.batch_get_skill_files``
            的形态（去掉 user_id 维度），供注入直接合并使用。
        """
        names = normalize_skill_name_list(skill_names, BUILTIN_FILES_PER_SKILL_LIMIT)
        if not names:
            return {}

        collection = self._get_files_collection()
        result: dict[str, dict[str, str]] = {name: {} for name in names}
        cursor = collection.find(
            {"skill_name": {"$in": names}},
            {"_id": 0, "skill_name": 1, "file_path": 1, "content": 1},
        ).limit(BUILTIN_FILES_PER_SKILL_LIMIT * len(names))
        async for doc in cursor:
            skill_name = doc["skill_name"]
            if skill_name in result:
                result[skill_name][doc["file_path"]] = doc.get("content", "")
        return result

    # ==========================================
    # 创建来源
    # ==========================================

    async def import_parsed_skills(
        self,
        parsed: list[ParsedZipSkill],
        allowed_roles: list[str],
        created_by: str,
    ) -> list[str]:
        """从已解析的 zip 结果创建内置 Skill。

        Args:
            parsed: ``_parse_zip_skills`` 返回的 ``(skill_name, text_files, binary_files)`` 列表。
            allowed_roles: 绑定的允许角色。
            created_by: admin user_id。

        Returns:
            成功创建的 skill_name 列表。

        Raises:
            ValueError: 任一 skill 已存在或文件超限。
        """
        created_names: list[str] = []
        for skill_name, text_files, binary_files in parsed:
            if not text_files and not binary_files:
                continue

            create_data = BuiltinSkillCreate(
                skill_name=skill_name,
                description=self._extract_description(text_files),
                allowed_roles=list(allowed_roles),
                source="zip",
                source_ref=None,
            )
            builtin = await self.create_builtin_skill(create_data, created_by)

            # 先写文本文件
            await self.sync_builtin_files(skill_name, text_files)
            # 再上传二进制文件（覆盖对应的 content 为 _binary_ref JSON）
            for file_path, data in binary_files.items():
                await self.set_builtin_binary_file(skill_name, file_path, data)

            created_names.append(builtin.skill_name)

        if created_names:
            await invalidate_builtin_skills_cache()

        return created_names

    async def create_from_marketplace(
        self,
        marketplace_name: str,
        allowed_roles: list[str],
        created_by: str,
        marketplace_storage: "Optional[MarketplaceStorage]" = None,
    ) -> BuiltinSkill:
        """从商城 Skill 复制创建内置 Skill。

        文件内容（含二进制引用 JSON）原样复制；二进制对象仍指向商城原 storage key，
        与用户侧 ``install_marketplace_skill`` 行为一致。
        """
        from src.infra.skill.marketplace import MarketplaceStorage

        marketplace = marketplace_storage or MarketplaceStorage()
        source = await marketplace.get_marketplace_skill(marketplace_name)
        if not source:
            raise ValueError(f"Marketplace skill '{marketplace_name}' not found")

        create_data = BuiltinSkillCreate(
            skill_name=source.skill_name,
            description=source.description,
            allowed_roles=list(allowed_roles),
            source="marketplace",
            source_ref=marketplace_name,
        )
        builtin = await self.create_builtin_skill(create_data, created_by)

        try:
            async for batch in marketplace.iter_marketplace_file_batches(
                marketplace_name
            ):
                await self.upsert_builtin_files_batch(source.skill_name, batch)
        except Exception:
            # 文件复制失败回滚已创建的元数据，避免半状态
            await self.delete_builtin_skill(source.skill_name)
            raise

        await invalidate_builtin_skills_cache()
        return builtin

    # ==========================================
    # 缓存失效
    # ==========================================

    async def invalidate_cache(self) -> None:
        """内置 Skill 写操作后失效全局 effective-skills 缓存。"""
        await invalidate_builtin_skills_cache()

    # ==========================================
    # helpers
    # ==========================================

    @staticmethod
    def _extract_description(text_files: dict[str, str]) -> str:
        """从 SKILL.md frontmatter 解析 description（zip 来源用）。"""
        skill_md = text_files.get("SKILL.md") or text_files.get("skill.md")
        if not skill_md:
            return ""
        try:
            from src.infra.skill.parser import parse_skill_md

            _, description, _ = parse_skill_md(skill_md)
            return description or ""
        except Exception:
            return ""

    @staticmethod
    def _doc_to_skill(doc: dict) -> BuiltinSkill:
        return BuiltinSkill(
            skill_name=doc["skill_name"],
            description=doc.get("description", ""),
            allowed_roles=list(doc.get("allowed_roles", [])),
            source=doc.get("source", "zip"),
            source_ref=doc.get("source_ref"),
            is_active=doc.get("is_active", True),
            created_by=doc.get("created_by"),
            created_at=doc.get("created_at"),
            updated_at=doc.get("updated_at"),
        )

    @staticmethod
    def _doc_to_response(doc: dict) -> BuiltinSkillResponse:
        return BuiltinSkillResponse(
            skill_name=doc["skill_name"],
            description=doc.get("description", ""),
            allowed_roles=list(doc.get("allowed_roles", [])),
            source=doc.get("source", "zip"),
            source_ref=doc.get("source_ref"),
            is_active=doc.get("is_active", True),
            created_by=doc.get("created_by"),
            created_at=doc.get("created_at"),
            updated_at=doc.get("updated_at"),
            file_count=int(doc.get("_file_count", 0) or 0),
        )

    async def close(self):
        """关闭连接（仅清理本地引用，不关闭全局 MongoDB 客户端）"""
        self._meta_collection = None
        self._files_collection = None


async def invalidate_builtin_skills_cache() -> None:
    """Bump the global builtin-skills version counter.

    The effective-skills cache value carries the version at computation time; a
    mismatch forces a recompute on next read. This is the MVP coarse-grained
    invalidation strategy (admin operations are low-frequency, so a single global
    bump is simpler than enumerating role-matched users).
    """
    try:
        from src.infra.storage.redis import get_redis_client

        redis_client = get_redis_client()
        await redis_client.incr(BUILTIN_SKILLS_VERSION_KEY)
    except Exception as e:
        logger.warning(f"[Builtin Skills Cache] Redis incr failed: {e}")
