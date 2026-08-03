"""Team Agent context — reuses FastAgentContext tool/skill loading."""

from src.agents.fast_agent.context import FastAgentContext
from src.infra.tool.upload_url_tool import get_upload_url_tool
from src.kernel.config import settings


class TeamAgentContext(FastAgentContext):
    """Reuses FastAgentContext tool/skill loading. Team-specific logic is in the node."""

    async def setup(self) -> None:
        await super().setup()
        if settings.ENABLE_SANDBOX and not any(
            getattr(tool, "name", "") == "upload_url_to_sandbox" for tool in self.tools
        ):
            self.tools.append(get_upload_url_tool())
