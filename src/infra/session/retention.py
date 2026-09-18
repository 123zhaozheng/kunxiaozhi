"""会话 checkpoint 保留状态的共享判定。"""

from __future__ import annotations

from typing import Any

CHECKPOINTS_CLEANED_AT = "checkpoints_cleaned_at"


def is_session_checkpoints_cleaned(session: Any) -> bool:
    """Return whether a session's resumable checkpoint state was reclaimed.

    The stamp is only trusted when it is at least as recent as the session's
    last activity: a later turn rebuilds checkpoints, which makes an older
    stamp stale.
    """
    metadata = getattr(session, "metadata", None) or {}
    cleaned_at = metadata.get(CHECKPOINTS_CLEANED_AT)
    if not cleaned_at:
        return False

    updated_at = getattr(session, "updated_at", None)
    if updated_at is None:
        return True
    try:
        return not (cleaned_at < updated_at)
    except TypeError:
        # Mixed naive/aware or string timestamps: treat the stamp as valid
        # rather than claiming a session is resumable when it may not be.
        return True
