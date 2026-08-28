"""Pure aggregation stages for usage metrics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class UsageFilters:
    start: datetime
    end: datetime
    persona_preset_id: str | None = None
    agent_id: str | None = None
    role_user_ids: list[str] | None = None


def usage_facts_stages(filters: UsageFilters) -> list[dict[str, Any]]:
    """Build the shared trace usage-facts pipeline."""
    match: dict[str, Any] = {
        "started_at": {"$gte": filters.start, "$lt": filters.end},
        "events.event_type": {"$in": ["user:message", "token:usage"]},
    }
    if filters.agent_id:
        match["agent_id"] = filters.agent_id
    if filters.role_user_ids is not None:
        match["user_id"] = {"$in": filters.role_user_ids}

    stages: list[dict[str, Any]] = [
        {"$match": match},
        {
            "$lookup": {
                "from": "sessions",
                "localField": "session_id",
                "foreignField": "session_id",
                "as": "_session",
            }
        },
        {
            "$addFields": {
                "persona_preset_id": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$_session.metadata.persona_preset_id", 0]},
                        "$metadata.persona_preset_id",
                    ]
                },
                "persona_preset_name": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$_session.metadata.persona_preset_name", 0]},
                        "$metadata.persona_preset_name",
                    ]
                },
            }
        },
    ]
    if filters.persona_preset_id:
        stages.append({"$match": {"persona_preset_id": filters.persona_preset_id}})
    stages.extend(
        [
            {
                "$addFields": {
                    "user_messages": {
                        "$size": {
                            "$filter": {
                                "input": "$events",
                                "as": "event",
                                "cond": {"$eq": ["$$event.event_type", "user:message"]},
                            }
                        }
                    },
                    "tokens": {
                        "$sum": {
                            "$map": {
                                "input": {
                                    "$filter": {
                                        "input": "$events",
                                        "as": "event",
                                        "cond": {"$eq": ["$$event.event_type", "token:usage"]},
                                    }
                                },
                                "as": "event",
                                "in": {"$ifNull": ["$$event.data.total_tokens", 0]},
                            }
                        }
                    },
                }
            },
            {"$project": {"_session": 0, "events": 0}},
        ]
    )
    return stages


def new_sessions_match(filters: UsageFilters) -> dict[str, Any]:
    """Build the sessions query for newly created sessions."""
    match: dict[str, Any] = {
        "created_at": {"$gte": filters.start, "$lt": filters.end}
    }
    if filters.persona_preset_id:
        match["metadata.persona_preset_id"] = filters.persona_preset_id
    if filters.agent_id:
        match["agent_id"] = filters.agent_id
    if filters.role_user_ids is not None:
        match["user_id"] = {"$in": filters.role_user_ids}
    return match
