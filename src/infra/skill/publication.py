"""Marketplace publication flow for ordinary user Skills."""

from typing import Optional

from src.infra.skill.marketplace import MarketplaceStorage
from src.infra.skill.parser import parse_skill_md, sanitize_skill_name
from src.infra.skill.storage import SkillStorage
from src.infra.skill.types import (
    InstalledFrom,
    MarketplaceSkillCreate,
    MarketplaceSkillResponse,
    MarketplaceSkillUpdate,
    PublishToMarketplaceRequest,
)


class SkillPublicationError(Exception):
    """Structured publication error safe to expose through the API."""

    def __init__(self, code: str, items: list[dict[str, object]]):
        super().__init__(code)
        self.code = code
        self.items = items

    def as_detail(self) -> dict:
        return {
            "code": self.code,
            "items": self.items,
        }


async def publish_user_skill(
    local_name: str,
    *,
    user_id: str,
    storage: SkillStorage,
    marketplace: MarketplaceStorage,
    data: Optional[PublishToMarketplaceRequest] = None,
) -> tuple[MarketplaceSkillResponse, bool]:
    """Publish one user Skill using the same contract as the public route."""
    user_files = await storage.get_skill_files(local_name, user_id)
    if not user_files:
        raise SkillPublicationError(
            "local_skill_not_found",
            [{"local_name": local_name, "marketplace_name": local_name, "reason": "local_skill_not_found"}],
        )
    if not user_files.get("SKILL.md"):
        raise SkillPublicationError(
            "skill_md_required",
            [{"local_name": local_name, "marketplace_name": local_name, "reason": "skill_md_required"}],
        )

    _, default_description, default_tags = parse_skill_md(user_files["SKILL.md"])
    target_name = sanitize_skill_name(
        (data.skill_name if data and data.skill_name else local_name).strip()
    )
    if not target_name:
        raise SkillPublicationError(
            "marketplace_skill_name_required",
            [{"local_name": local_name, "marketplace_name": local_name, "reason": "invalid_marketplace_name"}],
        )

    meta = await storage.get_skill_meta(local_name, user_id)
    existing = await marketplace.get_marketplace_skill(target_name)
    created = existing is None
    if existing and (
        existing.created_by != user_id or not meta or meta.published_marketplace_name != target_name
    ):
        raise SkillPublicationError(
            "marketplace_skill_name_taken",
            [{
                "local_name": local_name,
                "marketplace_name": target_name,
                "version": existing.version,
                "reason": (
                    "name_owned_by_other"
                    if existing.created_by != user_id
                    else "same_name_requires_verification"
                ),
            }],
        )

    if existing:
        await marketplace.update_marketplace_skill(
            target_name,
            MarketplaceSkillUpdate(
                description=(
                    data.description
                    if data and data.description is not None
                    else default_description
                ),
                tags=data.tags if data and data.tags is not None else existing.tags,
                version=data.version if data and data.version is not None else existing.version,
                is_active=True,
            ),
        )
    else:
        await marketplace.create_marketplace_skill(
            MarketplaceSkillCreate(
                skill_name=target_name,
                description=(
                    data.description
                    if data and data.description is not None
                    else default_description
                ),
                tags=data.tags if data and data.tags is not None else default_tags,
                version=data.version if data and data.version is not None else "1.0.0",
            ),
            user_id=user_id,
        )

    try:
        await marketplace.sync_marketplace_files(target_name, user_files)
        await storage.set_skill_meta(
            local_name,
            user_id,
            installed_from=InstalledFrom.MANUAL,
            published_marketplace_name=target_name,
        )
    except Exception:
        if created:
            await marketplace.delete_marketplace_skill(target_name)
        raise

    response = await marketplace.get_marketplace_skill_response(target_name, viewer_id=user_id)
    if not response:
        raise RuntimeError("Failed to publish skill")
    return response, created
