"""Opaque cursors for the session/share history read contract."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any, Iterable

HISTORY_ORDERING_VERSION = 2
HISTORY_PAGE_LIMIT_MAX = 10000


class InvalidHistoryCursor(ValueError):  # noqa: N818 - public cursor contract name
    """Raised when a cursor is malformed or belongs to another query."""


def _encode(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(value: str) -> Any:
    try:
        padding = "=" * (-len(value) % 4)
        return json.loads(base64.urlsafe_b64decode((value + padding).encode()))
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise InvalidHistoryCursor("invalid history cursor") from exc


def filter_fingerprint(
    *,
    scope: str,
    event_types: Iterable[str] | None = None,
    run_id: str | None = None,
    exclude_run_id: str | None = None,
    run_ids: Iterable[str] | None = None,
) -> str:
    payload = {
        "scope": scope,
        "event_types": list(event_types or []),
        "run_id": run_id,
        "exclude_run_id": exclude_run_id,
        "run_ids": list(run_ids or []),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def encode_history_cursor(*, scope: str, fingerprint: str, key: list[Any]) -> str:
    return _encode({"v": HISTORY_ORDERING_VERSION, "scope": scope, "filter": fingerprint, "key": key})


def decode_history_cursor(
    cursor: str,
    *,
    scope: str,
    fingerprint: str,
) -> list[Any]:
    payload = _decode(cursor)
    if not isinstance(payload, dict) or payload.get("v") != HISTORY_ORDERING_VERSION:
        raise InvalidHistoryCursor("unsupported history cursor")
    if payload.get("scope") != scope or payload.get("filter") != fingerprint:
        raise InvalidHistoryCursor("history cursor does not match this query")
    key = payload.get("key")
    if not isinstance(key, list) or len(key) != 6:
        raise InvalidHistoryCursor("invalid history cursor key")
    legacy_bucket, seq, timestamp, trace_id, event_id, ordinal = key
    if (
        not isinstance(legacy_bucket, int)
        or isinstance(legacy_bucket, bool)
        or legacy_bucket not in (0, 1)
        or not isinstance(seq, int)
        or isinstance(seq, bool)
        or seq < 0
        or not isinstance(timestamp, str)
        or not isinstance(trace_id, str)
        or not isinstance(event_id, str)
        or not isinstance(ordinal, int)
        or isinstance(ordinal, bool)
        or ordinal < 0
    ):
        raise InvalidHistoryCursor("invalid history cursor key")
    return key


def event_ordering_key(event: dict[str, Any], *, ordinal: int | None = None) -> list[Any]:
    """Return the stable compatibility ordering key for an event."""
    seq = event.get("seq")
    has_seq = isinstance(seq, (int, float)) and not isinstance(seq, bool)
    timestamp = str(event.get("timestamp") or "")
    trace_id = str(event.get("trace_id") or "")
    event_id = str(event.get("event_id") or event.get("id") or "")
    seq_number = (
        int(seq)
        if isinstance(seq, (int, float)) and not isinstance(seq, bool)
        else 0
    )
    return [
        1 if has_seq else 0,
        seq_number,
        timestamp,
        trace_id,
        event_id,
        int(ordinal or 0),
    ]
