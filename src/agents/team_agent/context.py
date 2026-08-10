"""Team Agent context — reuses FastAgentContext tool/skill loading."""

from src.agents.fast_agent.context import FastAgentContext
from src.infra.logging import get_logger
from src.kernel.config import settings

logger = get_logger(__name__)

# 团队主代理是路由者/整合者：与路由职责无关的管理/交互工具一律不暴露。
# （deepagents 子代理默认共享主代理工具，因此裁剪同时覆盖团队成员子代理。）
TEAM_ROUTER_EXCLUDED_TOOLS = frozenset(
    {
        "ask_human",  # 用户交互走 SOP 确认门禁，主代理不直接问用户
        "find_skills",  # 团队角色技能在团队配置时注入，不动态装技能
        "install_skill",
        "create_persona_preset",  # persona/团队管理工具对执行中的团队无意义
        "update_persona_preset",
        "search_persona_presets",
        "create_agent_team",
    }
)


class TeamAgentContext(FastAgentContext):
    """Reuses FastAgentContext tool/skill loading. Team-specific logic is in the node."""

    async def setup(self) -> None:
        """初始化：复用 FastAgentContext 工具/技能加载，裁剪路由无关工具，沙箱模式追加 upload_url_to_sandbox。"""
        await super().setup()

        # 裁剪团队主代理不需要的工具（路由职责之外的管理/交互工具）
        before = len(self.tools)
        self.tools = [
            tool
            for tool in self.tools
            if getattr(tool, "name", "") not in TEAM_ROUTER_EXCLUDED_TOOLS
        ]
        removed = before - len(self.tools)
        if removed:
            logger.info("[TeamAgentContext] Removed %d router-irrelevant tools", removed)

        # 沙箱专属工具（与 SearchAgentContext 对齐；团队沙箱提示词已指导模型使用该工具）
        if settings.ENABLE_SANDBOX:
            from src.infra.tool.upload_url_tool import get_upload_url_tool

            self.tools.append(get_upload_url_tool())
            logger.info("[TeamAgentContext] Added upload_url_to_sandbox tool (sandbox mode)")
