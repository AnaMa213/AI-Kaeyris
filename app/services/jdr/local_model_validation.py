"""Business rules for JDR local model validation proofs (BD-20)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.local_models import (
    LocalModelProbeError,
    normalize_model_path,
    probe_local_model,
)
from app.core.config import settings
from app.core.datetime_serialization import ensure_aware_utc
from app.core.errors import AppError
from app.services.jdr.db.models import (
    LocalModelCategory,
    LocalModelValidationStatus,
)
from app.services.jdr.db.repositories import LocalModelValidationRepository
from app.services.jdr.schemas import (
    LocalModelValidationOut,
    LocalModelValidationRequest,
)


class LocalModelValidationAppError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    error_type = "local-model-validation-required"
    title = "Local model validation required"

    def __init__(self, problem_type: str, title: str, detail: str) -> None:
        super().__init__(detail)
        self.error_type = problem_type
        self.title = title


def validation_id_hash(validation_id: str) -> str:
    return hashlib.sha256(validation_id.encode("utf-8")).hexdigest()


def model_path_hash(model_path: str) -> str:
    return hashlib.sha256(model_path.encode("utf-8")).hexdigest()


async def create_local_model_validation(
    db: AsyncSession,
    *,
    user_id: UUID,
    payload: LocalModelValidationRequest,
) -> LocalModelValidationOut:
    normalized_path = normalize_model_path(payload.model_path)
    try:
        probe = await probe_local_model(
            category=payload.category.value,
            model_path=normalized_path,
            timeout_seconds=settings.LOCAL_MODEL_VALIDATION_TIMEOUT_SECONDS,
        )
    except LocalModelProbeError as exc:
        raise LocalModelValidationAppError(
            exc.problem_type,
            exc.title,
            exc.detail,
        ) from exc

    validation_id = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(
        seconds=settings.LOCAL_MODEL_VALIDATION_TTL_SECONDS
    )
    await LocalModelValidationRepository(db).create_succeeded(
        validation_hash=validation_id_hash(validation_id),
        user_id=user_id,
        category=payload.category,
        model_path=normalized_path,
        path_hash=model_path_hash(normalized_path),
        runtime=probe.runtime,
        model_format=probe.model_format,
        message=probe.message,
        expires_at=expires_at,
    )
    return LocalModelValidationOut(
        validation_id=validation_id,
        category=payload.category,
        model_path=normalized_path,
        status=LocalModelValidationStatus.SUCCEEDED,
        runtime=probe.runtime,
        model_format=probe.model_format,
        message=probe.message,
        expires_at=expires_at,
    )


async def require_local_model_validation_hash(
    db: AsyncSession,
    *,
    user_id: UUID,
    category: LocalModelCategory,
    model_path: str | None,
    validation_id: str | None,
) -> str:
    if not model_path or not model_path.strip():
        raise LocalModelValidationAppError(
            "local-model-validation-required",
            "Local model validation required",
            "A Local model path is required before Local settings can be saved.",
        )
    if not validation_id or not validation_id.strip():
        raise LocalModelValidationAppError(
            "local-model-validation-required",
            "Local model validation required",
            "Validate the Local model path before saving Local settings.",
        )

    normalized_path = normalize_model_path(model_path)
    expected_hash = validation_id_hash(validation_id.strip())
    row = await LocalModelValidationRepository(db).get_by_hash(expected_hash)
    if row is None:
        raise LocalModelValidationAppError(
            "local-model-validation-required",
            "Local model validation required",
            "Validate the Local model path before saving Local settings.",
        )
    if (
        row.user_id != user_id
        or row.category is not category
        or row.path_hash != model_path_hash(normalized_path)
        or row.status is not LocalModelValidationStatus.SUCCEEDED
    ):
        raise LocalModelValidationAppError(
            "local-model-validation-required",
            "Local model validation required",
            "The supplied Local model validation proof does not match this setting.",
        )
    if ensure_aware_utc(row.expires_at) <= datetime.now(UTC):
        raise LocalModelValidationAppError(
            "local-model-validation-expired",
            "Local model validation expired",
            "Validate the Local model path again before saving.",
        )
    return row.validation_hash
