"""Shared Marketplace publication flows for Skills and public Personas."""

from dataclasses import dataclass
from typing import Optional

from src.infra.skill.marketplace import MarketplaceStorage
from src.infra.skill.parser import parse_skill_md, sanitize_skill_name
from src.infra.skill.storage import SkillStorage, normalize_skill_name_list
from src.infra.skill.types import (
    InstalledFrom,
    MarketplaceSkillCreate,
    MarketplaceSkillResponse,
    MarketplaceSkillUpdate,
    PublishToMarketplaceRequest,
    SkillMeta,
)
from src.kernel.schemas.persona_preset import (
    PersonaMarketplaceSkillRef,
    PersonaSkillPublicationItem,
    PersonaSkillPublicationPreflightResponse,
)


class SkillPublicationError(Exception):
    """Structured publication error safe to expose through the API."""

    def __init__(self, code: str, items: list[PersonaSkillPublicationItem]):
        super().__init__(code)
        self.code = code
        self.items = items

    def as_detail(self) -> dict:
        return {
            "code": self.code,
            "items": [item.model_dump(mode="json") for item in self.items],
        }


@dataclass
class CreatedSkillPublication:
    local_name: str
    marketplace_name: str
    previous_meta: Optional[SkillMeta]


async def preflight_user_skill_publications(
    skill_names: list[str],
    *,
    user_id: str,
    storage: SkillStorage,
    marketplace: MarketplaceStorage,
) -> PersonaSkillPublicationPreflightResponse:
    """Classify local Skills for coordinated public Persona publication."""
    result = PersonaSkillPublicationPreflightResponse()
    for local_name in normalize_skill_name_list(skill_names):
        files = await storage.get_skill_files(local_name, user_id)
        if not files:
            result.conflicts.append(
                PersonaSkillPublicationItem(
                    local_name=local_name,
                    marketplace_name=local_name,
                    reason="local_skill_not_found",
                )
            )
            continue
        if not files.get("SKILL.md"):
            result.conflicts.append(
                PersonaSkillPublicationItem(
                    local_name=local_name,
                    marketplace_name=local_name,
                    reason="skill_md_required",
                )
            )
            continue

        meta = await storage.get_skill_meta(local_name, user_id)
        target_name = sanitize_skill_name(
            (meta.published_marketplace_name if meta else None) or local_name
        )
        if not target_name:
            result.conflicts.append(
                PersonaSkillPublicationItem(
                    local_name=local_name,
                    marketplace_name=local_name,
                    reason="invalid_marketplace_name",
                )
            )
            continue

        existing = await marketplace.get_marketplace_skill(target_name)
        item = PersonaSkillPublicationItem(
            local_name=local_name,
            marketplace_name=target_name,
            version=existing.version if existing else None,
        )
        is_installed_marketplace_copy = bool(
            meta and meta.installed_from == InstalledFrom.MARKETPLACE and local_name == target_name
        )
        is_linked_owner_publication = bool(
            meta
            and meta.published_marketplace_name == target_name
            and existing
            and existing.created_by == user_id
        )
        if (
            existing
            and existing.is_active
            and (is_installed_marketplace_copy or is_linked_owner_publication)
        ):
            result.ready.append(item)
        elif existing:
            reason = (
                "marketplace_skill_inactive"
                if is_linked_owner_publication and not existing.is_active
                else "same_name_requires_verification"
            )
            if reason == "marketplace_skill_inactive":
                result.requires_publish.append(item.model_copy(update={"reason": reason}))
            else:
                result.conflicts.append(item.model_copy(update={"reason": reason}))
        else:
            reason = "inactive_owned_skill" if existing else "not_published"
            result.requires_publish.append(item.model_copy(update={"reason": reason}))
    return result


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
            [
                PersonaSkillPublicationItem(
                    local_name=local_name,
                    marketplace_name=local_name,
                    reason="local_skill_not_found",
                )
            ],
        )
    if not user_files.get("SKILL.md"):
        raise SkillPublicationError(
            "skill_md_required",
            [
                PersonaSkillPublicationItem(
                    local_name=local_name,
                    marketplace_name=local_name,
                    reason="skill_md_required",
                )
            ],
        )

    _, default_description, default_tags = parse_skill_md(user_files["SKILL.md"])
    target_name = sanitize_skill_name(
        (data.skill_name if data and data.skill_name else local_name).strip()
    )
    if not target_name:
        raise SkillPublicationError(
            "marketplace_skill_name_required",
            [
                PersonaSkillPublicationItem(
                    local_name=local_name,
                    marketplace_name=local_name,
                    reason="invalid_marketplace_name",
                )
            ],
        )

    meta = await storage.get_skill_meta(local_name, user_id)
    existing = await marketplace.get_marketplace_skill(target_name)
    created = existing is None
    if existing and (
        existing.created_by != user_id or not meta or meta.published_marketplace_name != target_name
    ):
        raise SkillPublicationError(
            "marketplace_skill_name_taken",
            [
                PersonaSkillPublicationItem(
                    local_name=local_name,
                    marketplace_name=target_name,
                    version=existing.version,
                    reason=(
                        "name_owned_by_other"
                        if existing.created_by != user_id
                        else "same_name_requires_verification"
                    ),
                )
            ],
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
            installed_from=meta.installed_from if meta else InstalledFrom.MANUAL,
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


async def ensure_public_persona_skill_dependencies(
    skill_names: list[str],
    *,
    user_id: str,
    confirmed: bool,
    storage: SkillStorage,
    marketplace: MarketplaceStorage,
) -> tuple[list[PersonaMarketplaceSkillRef], list[CreatedSkillPublication]]:
    """Resolve and optionally publish every Skill required by a public Persona."""
    plan = await preflight_user_skill_publications(
        skill_names,
        user_id=user_id,
        storage=storage,
        marketplace=marketplace,
    )
    if plan.conflicts:
        raise SkillPublicationError("persona_skill_publication_conflict", plan.conflicts)
    if plan.requires_publish and not confirmed:
        raise SkillPublicationError(
            "persona_skill_publication_required",
            plan.requires_publish,
        )

    created: list[CreatedSkillPublication] = []
    try:
        for item in plan.requires_publish:
            previous_meta = await storage.get_skill_meta(item.local_name, user_id)
            response, was_created = await publish_user_skill(
                item.local_name,
                user_id=user_id,
                storage=storage,
                marketplace=marketplace,
            )
            if was_created:
                created.append(
                    CreatedSkillPublication(
                        local_name=item.local_name,
                        marketplace_name=response.skill_name,
                        previous_meta=previous_meta,
                    )
                )
    except Exception:
        for publication in reversed(created):
            await marketplace.delete_marketplace_skill(publication.marketplace_name)
            if publication.previous_meta is None:
                await storage.delete_skill_meta(publication.local_name, user_id)
            else:
                await storage.set_skill_meta(
                    publication.local_name,
                    user_id,
                    installed_from=publication.previous_meta.installed_from,
                    published_marketplace_name=(
                        publication.previous_meta.published_marketplace_name
                    ),
                )
        raise

    final_plan = await preflight_user_skill_publications(
        skill_names,
        user_id=user_id,
        storage=storage,
        marketplace=marketplace,
    )
    if final_plan.conflicts or final_plan.requires_publish:
        unresolved = [*final_plan.conflicts, *final_plan.requires_publish]
        raise SkillPublicationError("persona_skill_publication_incomplete", unresolved)
    return (
        [
            PersonaMarketplaceSkillRef(
                name=item.marketplace_name,
                version=item.version,
            )
            for item in final_plan.ready
        ],
        created,
    )


async def rollback_created_skill_publications(
    publications: list[CreatedSkillPublication],
    *,
    user_id: str,
    storage: SkillStorage,
    marketplace: MarketplaceStorage,
) -> None:
    """Compensate new Marketplace records when the coordinated Persona save fails."""
    for publication in reversed(publications):
        await marketplace.delete_marketplace_skill(publication.marketplace_name)
        if publication.previous_meta is None:
            await storage.delete_skill_meta(publication.local_name, user_id)
        else:
            await storage.set_skill_meta(
                publication.local_name,
                user_id,
                installed_from=publication.previous_meta.installed_from,
                published_marketplace_name=(publication.previous_meta.published_marketplace_name),
            )
