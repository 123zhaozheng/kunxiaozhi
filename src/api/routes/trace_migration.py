"""Admin-only duplicate trace migration endpoints.

The endpoint defaults to a read-only plan.  Mutations require both
``apply=true`` and ``confirm=true``; rollback is separately addressed by the
operation id returned from apply.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.deps import require_permissions
from src.infra.session.trace_migration import (
    DuplicateTraceMigrationPlan,
    DuplicateTraceMigrationResult,
    MigrationConfirmationRequiredError,
    migrate_duplicate_traces,
    rollback_duplicate_trace_migration,
)
from src.kernel.schemas.user import TokenPayload

router = APIRouter()


@router.post("/duplicates/migrate", response_model=DuplicateTraceMigrationResult)
async def migrate_duplicate_trace_documents(
    apply: bool = Query(False),
    confirm: bool = Query(False),
    session_id: str | None = Query(None),
    operation_id: str | None = Query(None),
    include_active: bool = Query(False),
    _: TokenPayload = Depends(require_permissions("settings:manage")),
) -> DuplicateTraceMigrationResult:
    try:
        result = await migrate_duplicate_traces(
            apply=apply,
            confirm=confirm,
            session_id=session_id,
            operation_id=operation_id,
            include_active=include_active,
        )
    except (MigrationConfirmationRequiredError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Keep one stable Pydantic response shape for clients while preserving the
    # plan's dry-run marker and zero mutation counters.
    if isinstance(result, DuplicateTraceMigrationPlan) and not isinstance(result, DuplicateTraceMigrationResult):
        return DuplicateTraceMigrationResult(**result.model_dump(), state="planned")
    return result


@router.post("/duplicates/{operation_id}/rollback", response_model=DuplicateTraceMigrationResult)
async def rollback_duplicate_trace_documents(
    operation_id: str,
    _: TokenPayload = Depends(require_permissions("settings:manage")),
) -> DuplicateTraceMigrationResult:
    try:
        return await rollback_duplicate_trace_migration(operation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
