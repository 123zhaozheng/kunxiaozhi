"""Persona preset schemas."""

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.infra.utils.datetime import utc_now

PreferredAgentId = Literal["fast", "search", "team"]
DEFAULT_PREFERRED_AGENT_ID: PreferredAgentId = "fast"
PREFERRED_AGENT_IDS: frozenset[str] = frozenset({"fast", "search", "team"})


class PersonaPresetScope(str, Enum):
    """Preset ownership scope."""

    GLOBAL = "global"
    USER = "user"


class PersonaPresetVisibility(str, Enum):
    """Preset visibility."""

    PUBLIC = "public"
    PRIVATE = "private"


class PersonaPresetStatus(str, Enum):
    """Preset publication status."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class PersonaStarterPrompt(BaseModel):
    """Prompt suggestion shown after selecting a persona."""

    icon: Optional[str] = None
    text: str | dict[str, str]

    @field_validator("icon")
    @classmethod
    def _normalize_icon(cls, value: str | None) -> str | None:
        if value is None:
            return None
        item = value.strip()
        return item or None

    @field_validator("text")
    @classmethod
    def _normalize_text(cls, value: str | dict[str, str]) -> str | dict[str, str]:
        if isinstance(value, str):
            item = value.strip()
            if not item:
                raise ValueError("starter_prompt_text_required")
            return item

        result: dict[str, str] = {}
        for lang, text in value.items():
            lang_key = str(lang).strip()
            localized_text = str(text).strip()
            if lang_key and localized_text:
                result[lang_key] = localized_text
        if not result:
            raise ValueError("starter_prompt_text_required")
        return result


class PersonaMarketplaceSkillRef(BaseModel):
    """Public Marketplace Skill dependency mounted by a Persona."""

    name: str = Field(..., min_length=1)
    version: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        item = value.strip()
        if not item:
            raise ValueError("marketplace_skill_name_required")
        return item


class PersonaSkillPublicationItem(BaseModel):
    """A local Skill's resolved Marketplace publication state."""

    local_name: str
    marketplace_name: str
    version: Optional[str] = None
    reason: Optional[str] = None


class PersonaSkillPublicationPreflightRequest(BaseModel):
    """Resolve selected local Skills before publishing a public Persona."""

    skill_names: list[str] = Field(default_factory=list)

    @field_validator("skill_names")
    @classmethod
    def _normalize_skill_names(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            item = value.strip()
            if item and item not in seen:
                seen.add(item)
                result.append(item)
        return result


class PersonaSkillPublicationPreflightResponse(BaseModel):
    """Grouped publication plan shown by the Persona editor."""

    ready: list[PersonaSkillPublicationItem] = Field(default_factory=list)
    requires_publish: list[PersonaSkillPublicationItem] = Field(default_factory=list)
    conflicts: list[PersonaSkillPublicationItem] = Field(default_factory=list)


class PersonaPresetBase(BaseModel):
    """Common persona preset fields."""

    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    avatar: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    system_prompt: str = Field(..., min_length=1)
    starter_prompts: list[PersonaStarterPrompt] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    marketplace_skills: list[PersonaMarketplaceSkillRef] = Field(default_factory=list)
    dify_kb_dataset_ids: list[str] = Field(default_factory=list)
    preferred_agent_id: PreferredAgentId = DEFAULT_PREFERRED_AGENT_ID
    scope: PersonaPresetScope = PersonaPresetScope.USER
    visibility: PersonaPresetVisibility = PersonaPresetVisibility.PRIVATE
    status: PersonaPresetStatus = PersonaPresetStatus.DRAFT

    @field_validator("tags", "skill_names", "dify_kb_dataset_ids")
    @classmethod
    def _dedupe_strings(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            item = value.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            result.append(item)
        return result


class PersonaPresetCreate(PersonaPresetBase):
    """Create persona preset request."""

    marketplace_skills: list[PersonaMarketplaceSkillRef] = Field(
        default_factory=list,
        exclude=True,
    )
    publish_personal_skills: bool = Field(default=False, exclude=True)


class PersonaPresetUpdate(BaseModel):
    """Update persona preset request."""

    name: Optional[str] = Field(None, min_length=1, max_length=80)
    description: Optional[str] = Field(None, max_length=500)
    avatar: Optional[str] = None
    tags: Optional[list[str]] = None
    system_prompt: Optional[str] = Field(None, min_length=1)
    starter_prompts: Optional[list[PersonaStarterPrompt]] = None
    skill_names: Optional[list[str]] = None
    marketplace_skills: Optional[list[PersonaMarketplaceSkillRef]] = Field(
        default=None,
        exclude=True,
    )
    dify_kb_dataset_ids: Optional[list[str]] = None
    preferred_agent_id: Optional[PreferredAgentId] = None
    scope: Optional[PersonaPresetScope] = None
    visibility: Optional[PersonaPresetVisibility] = None
    status: Optional[PersonaPresetStatus] = None
    publish_personal_skills: Optional[bool] = Field(default=None, exclude=True)

    @field_validator("tags", "skill_names", "dify_kb_dataset_ids")
    @classmethod
    def _dedupe_optional_strings(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return PersonaPresetBase._dedupe_strings(values)


class PersonaPresetPreferenceUpdate(BaseModel):
    """Update the current user's presentation preferences for a preset."""

    is_favorite: Optional[bool] = None
    is_pinned: Optional[bool] = None


class PersonaPreset(BaseModel):
    """Persona preset response model."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: PersonaPresetScope
    owner_user_id: Optional[str] = None
    name: str
    description: str = ""
    avatar: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    system_prompt: str
    starter_prompts: list[PersonaStarterPrompt] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    marketplace_skills: list[PersonaMarketplaceSkillRef] = Field(default_factory=list)
    dify_kb_dataset_ids: list[str] = Field(default_factory=list)
    preferred_agent_id: PreferredAgentId = DEFAULT_PREFERRED_AGENT_ID
    visibility: PersonaPresetVisibility
    status: PersonaPresetStatus
    source_preset_id: Optional[str] = None
    copied_from_version: Optional[int] = None
    version: int = 1
    usage_count: int = 0
    is_favorite: bool = False
    is_pinned: bool = False
    has_wecom: bool = False
    last_used_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PersonaPresetSnapshot(BaseModel):
    """Immutable runtime snapshot saved with a chat session."""

    preset_id: str
    name: str
    system_prompt: str
    starter_prompts: list[PersonaStarterPrompt] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    marketplace_skills: list[PersonaMarketplaceSkillRef] = Field(default_factory=list)
    dify_kb_dataset_ids: list[str] = Field(default_factory=list)
    preferred_agent_id: PreferredAgentId = DEFAULT_PREFERRED_AGENT_ID
    missing_skill_names: list[str] = Field(default_factory=list)
    version: int = 1
    avatar: Optional[str] = None


class PersonaPresetListResponse(BaseModel):
    """Paginated persona preset list."""

    presets: list[PersonaPreset]
    total: int
    skip: int = 0
    limit: int = 100
