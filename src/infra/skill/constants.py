"""
Skill storage constants
"""

# MongoDB collection names
SKILL_FILES_COLLECTION = "skill_files"  # 用户文件
SKILL_MARKETPLACE_COLLECTION = "skill_marketplace"  # 商城 Skill 元数据
SKILL_MARKETPLACE_FILES_COLLECTION = "skill_marketplace_files"  # 商城 Skill 文件
SKILL_BUILTIN_COLLECTION = "skill_builtin"  # 内置 Skill 元数据（admin 管理，角色级注入）
SKILL_BUILTIN_FILES_COLLECTION = "skill_builtin_files"  # 内置 Skill 文件

# Redis cache TTL (seconds), default 30 minutes
SKILLS_CACHE_TTL = 1800
MCP_TOOLS_METADATA_CACHE_TTL = 1800

# Redis cache key prefixes
SKILLS_CACHE_KEY_PREFIX = "user_skills:"
MCP_TOOLS_METADATA_KEY_PREFIX = "mcp_tools_metadata:"

# Global version counter bumped on any builtin skill write. The effective-skills
# cache value carries the version seen at computation time; a mismatch forces a
# recompute so role-matched users pick up builtin changes (coarse-grained
# invalidation — admin operations are low-frequency).
BUILTIN_SKILLS_VERSION_KEY = "builtin_skills:version"
