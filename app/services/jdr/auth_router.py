"""Browser auth and user-management routes for the JDR service."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    AuthenticatedKey,
    UnauthorizedError,
    require_api_key,
    require_gm,
)
from app.core.config import settings
from app.core.db import get_db_session
from app.core.errors import AppError, PROBLEM_CONTENT_TYPE
from app.core.logging import get_logger
from app.core.models import SystemRole, User
from app.core.user_schemas import (
    LoginRequest,
    AuthMeCampaignOut,
    AuthMeOut,
    AuthMeUserOut,
    SetupRequest,
    SetupStatusOut,
    UserCreate,
    UserListOut,
    UserOut,
    UserUpdate,
)
from app.core.users import (
    DuplicateUserError,
    LastActiveAdminError,
    SetupClosedError,
    UserNotFoundError,
    authenticate_user,
    create_first_gm,
    create_user,
    create_web_session,
    delete_user,
    list_users,
    revoke_web_session,
    setup_required,
    update_user,
    validate_web_session,
)
from app.services.jdr.campaign_context import (
    CampaignScope,
    campaign_role_for_system_role,
    ensure_default_campaign,
    ensure_user_membership,
    resolve_active_campaign_for_user,
    resolve_campaign_scope_for_auth,
)
from app.services.jdr.db.models import CampaignRole, LocalModelCategory, ModelProvider
from app.services.jdr.db.repositories import CampaignRepository, ModelSettingsRepository
from app.services.jdr.local_model_validation import (
    create_local_model_validation,
    require_local_model_validation_hash,
)
from app.services.jdr.schemas import (
    LocalModelValidationOut,
    LocalModelValidationRequest,
    ModelSettingsOut,
    ModelSettingsPatch,
)

logger = get_logger(__name__)

router = APIRouter(tags=["jdr-auth"])


class DuplicateUserAppError(AppError):
    status_code = status.HTTP_409_CONFLICT
    error_type = "duplicate-user"
    title = "Duplicate user"


class UserNotFoundAppError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    error_type = "user-not-found"
    title = "User not found"


class SetupClosedAppError(AppError):
    status_code = status.HTTP_409_CONFLICT
    error_type = "setup-closed"
    title = "Setup closed"


class LastActiveAdminAppError(AppError):
    status_code = status.HTTP_409_CONFLICT
    error_type = "last-active-admin"
    title = "Last active admin"


class AdminRequiredAppError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    error_type = "admin-required"
    title = "Administrator privileges required"


def _front_problem(status_code: int, title: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"type": "about:blank", "title": title, "status": status_code},
        media_type=PROBLEM_CONTENT_TYPE,
    )


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
    )


def _request_client_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


async def _owner_user_for_auth(
    db: AsyncSession,
    auth: AuthenticatedKey,
) -> User | None:
    if auth.user_id is not None:
        return await db.get(User, auth.user_id)
    return await db.scalar(select(User).where(User.api_key_id == auth.id).limit(1))


async def _active_or_default_scope(
    db: AsyncSession,
    auth: AuthenticatedKey,
) -> CampaignScope | None:
    scope = await resolve_campaign_scope_for_auth(db, auth)
    if scope is not None:
        return scope

    owner = await _owner_user_for_auth(db, auth)
    if owner is None:
        return None
    campaign = await ensure_default_campaign(db, owner_user=owner)
    await ensure_user_membership(
        db,
        user=owner,
        campaign=campaign,
        role=campaign_role_for_system_role(owner.system_role),
    )
    return CampaignScope(
        campaign_id=campaign.id,
        role=campaign_role_for_system_role(owner.system_role),
        user_id=owner.id,
    )


async def _ensure_user_in_active_campaign(
    db: AsyncSession,
    auth: AuthenticatedKey,
    user_id: UUID,
) -> CampaignScope:
    scope = await _active_or_default_scope(db, auth)
    if scope is None:
        raise UserNotFoundAppError("User not found.")
    membership = await CampaignRepository(db).get_membership(
        user_id=user_id,
        campaign_id=scope.campaign_id,
    )
    if membership is None:
        raise UserNotFoundAppError("User not found.")
    return scope


async def _require_admin_user(
    db: AsyncSession,
    auth: AuthenticatedKey,
) -> User:
    if auth.source != "web_session" or auth.user_id is None:
        raise AdminRequiredAppError("A signed-in administrator is required.")
    user = await db.get(User, auth.user_id)
    if user is None or user.system_role != SystemRole.ADMIN:
        raise AdminRequiredAppError("A signed-in administrator is required.")
    return user


@router.get(
    "/services/jdr/auth/setup/status",
    response_model=SetupStatusOut,
)
async def get_setup_status(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SetupStatusOut:
    return SetupStatusOut(required=await setup_required(db))


@router.post(
    "/services/jdr/auth/setup",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_setup(
    payload: SetupRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserOut:
    try:
        user = await create_first_gm(
            db,
            username=payload.username,
            password=payload.password,
        )
    except SetupClosedError as exc:
        raise SetupClosedAppError("First-run setup is already closed.") from exc
    except DuplicateUserError as exc:
        raise DuplicateUserAppError("Username already exists.") from exc

    campaign = await ensure_default_campaign(db, owner_user=user)
    await ensure_user_membership(db, user=user, campaign=campaign)
    token, _web_session = await create_web_session(
        db,
        user,
        ttl_seconds=settings.WEB_SESSION_TTL_SECONDS,
        user_agent=request.headers.get("user-agent"),
        client_ip=_request_client_ip(request),
    )
    _set_session_cookie(response, token)
    logger.info("jdr.auth.setup_created", username=user.username, user_id=str(user.id))
    return UserOut.model_validate(user)


@router.post("/services/jdr/auth/login")
async def post_login(
    payload: LoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    user = await authenticate_user(
        db,
        username=payload.username,
        password=payload.password,
    )
    if user is None:
        logger.info(
            "jdr.auth.login_rejected",
            username=payload.username.strip().lower(),
            system_role=getattr(payload, "system_role", None),
        )
        return _front_problem(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    token, _web_session = await create_web_session(
        db,
        user,
        ttl_seconds=settings.WEB_SESSION_TTL_SECONDS,
        user_agent=request.headers.get("user-agent"),
        client_ip=_request_client_ip(request),
    )
    response = Response(status_code=status.HTTP_200_OK)
    _set_session_cookie(response, token)
    logger.info("jdr.auth.login_succeeded", username=user.username, user_id=str(user.id))
    return response


@router.post("/services/jdr/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def post_logout(
    request: Request,
    _auth: Annotated[AuthenticatedKey, Depends(require_api_key)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if token:
        await revoke_web_session(db, token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        samesite=settings.SESSION_COOKIE_SAMESITE,
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
    )
    return response


@router.get(
    "/services/jdr/auth/me",
    response_model=AuthMeOut,
    summary="Return the current web user and active JDR campaign context.",
)
async def get_me(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthMeOut:
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        raise UnauthorizedError(detail="Missing or malformed credentials.")

    validated = await validate_web_session(db, token)
    if validated is None:
        raise UnauthorizedError(detail="Missing or malformed credentials.")

    active = await resolve_active_campaign_for_user(db, validated.user)
    response.headers["Cache-Control"] = "no-store"
    return AuthMeOut(
        user=AuthMeUserOut(
            id=validated.user.id,
            username=validated.user.username,
            system_role=validated.user.system_role,
        ),
        active_campaign=(
            AuthMeCampaignOut(
                id=active.id,
                name=active.name,
                role=active.role.value,
                character_id=active.character_id,
            )
            if active is not None
            else None
        ),
    )


@router.post(
    "/services/jdr/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
)
async def post_user(
    payload: UserCreate,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserOut:
    await _require_admin_user(db, auth)
    try:
        user = await create_user(
            db,
            username=payload.username,
            system_role=payload.system_role,
            password=payload.password,
        )
    except DuplicateUserError as exc:
        raise DuplicateUserAppError("Username already exists.") from exc

    scope = await _active_or_default_scope(db, auth)
    if scope is not None:
        campaign = await CampaignRepository(db).get(scope.campaign_id)
        if campaign is not None:
            await ensure_user_membership(
                db,
                user=user,
                campaign=campaign,
                role=CampaignRole.PJ,
            )

    logger.info(
        "jdr.users.created",
        username=user.username,
        system_role=user.system_role.value,
    )
    return UserOut.model_validate(user)


@router.get("/services/jdr/users", response_model=UserListOut)
async def get_users(
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserListOut:
    await _require_admin_user(db, auth)
    return UserListOut(
        items=[UserOut.model_validate(user) for user in await list_users(db)]
    )


def _model_settings_out(row) -> ModelSettingsOut:
    if row is None:
        transcription_provider = _effective_transcription_provider()
        summary_provider = _effective_summary_provider()
        return ModelSettingsOut(
            transcription_provider=transcription_provider,
            summary_provider=summary_provider,
            transcription_cloud_model=(
                settings.TRANSCRIPTION_MODEL
                if transcription_provider is ModelProvider.CLOUD
                else None
            ),
            summary_cloud_model=(
                settings.LLM_MODEL
                if summary_provider is ModelProvider.CLOUD
                else None
            ),
            ollama_model=(
                settings.LLM_MODEL
                if summary_provider is ModelProvider.OLLAMA
                else None
            ),
            deepinfra_api_key_set=False,
        )
    return ModelSettingsOut(
        transcription_provider=row.transcription_provider,
        summary_provider=row.summary_provider,
        transcription_local_path=row.transcription_local_path,
        summary_local_path=row.summary_local_path,
        transcription_cloud_model=row.transcription_cloud_model,
        summary_cloud_model=row.summary_cloud_model,
        ollama_model=row.ollama_model,
        # Expose only whether a key exists, never the key itself.
        deepinfra_api_key_set=bool(row.deepinfra_api_key),
    )


@router.get(
    "/services/jdr/settings/models",
    response_model=ModelSettingsOut,
    summary="Return the current admin's JDR AI model provider settings.",
)
async def get_model_settings(
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ModelSettingsOut:
    user = await _require_admin_user(db, auth)
    row = await ModelSettingsRepository(db).get_for_user(user.id)
    return _model_settings_out(row)


@router.post(
    "/services/jdr/settings/models/local/validation",
    response_model=LocalModelValidationOut,
    summary="Validate a local JDR AI model path and return a short-lived proof.",
)
async def post_local_model_validation(
    payload: LocalModelValidationRequest,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> LocalModelValidationOut:
    user = await _require_admin_user(db, auth)
    return await create_local_model_validation(db, user_id=user.id, payload=payload)


@router.patch(
    "/services/jdr/settings/models",
    response_model=ModelSettingsOut,
    summary="Update the current admin's JDR AI model provider settings.",
)
async def patch_model_settings(
    payload: ModelSettingsPatch,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ModelSettingsOut:
    user = await _require_admin_user(db, auth)
    repo = ModelSettingsRepository(db)
    current = await repo.get_for_user(user.id)
    transcription_validation_hash = await _required_local_validation_hash_if_needed(
        db,
        user_id=user.id,
        current=current,
        payload=payload,
        category=LocalModelCategory.TRANSCRIPTION,
    )
    summary_validation_hash = await _required_local_validation_hash_if_needed(
        db,
        user_id=user.id,
        current=current,
        payload=payload,
        category=LocalModelCategory.SUMMARY,
    )
    row = await repo.upsert_for_user(
        user_id=user.id,
        transcription_provider=payload.transcription_provider,
        summary_provider=payload.summary_provider,
        transcription_local_path=payload.transcription_local_path,
        summary_local_path=payload.summary_local_path,
        transcription_cloud_model=payload.transcription_cloud_model,
        summary_cloud_model=payload.summary_cloud_model,
        ollama_model=payload.ollama_model,
        transcription_local_validation_hash=transcription_validation_hash,
        summary_local_validation_hash=summary_validation_hash,
        deepinfra_api_key=payload.deepinfra_api_key,
    )
    # NOTE: never log payload.deepinfra_api_key — it is a secret.
    logger.info(
        "jdr.settings.models_updated",
        user_id=str(user.id),
        transcription_provider=row.transcription_provider.value,
        summary_provider=row.summary_provider.value,
        ollama_model=row.ollama_model,
        deepinfra_api_key_set=bool(row.deepinfra_api_key),
    )
    return _model_settings_out(row)


async def _required_local_validation_hash_if_needed(
    db: AsyncSession,
    *,
    user_id: UUID,
    current,
    payload: ModelSettingsPatch,
    category: LocalModelCategory,
) -> str | None:
    if category is LocalModelCategory.TRANSCRIPTION:
        provider_field = "transcription_provider"
        path_field = "transcription_local_path"
        proof_field = "transcription_local_validation_id"
        hash_field = "transcription_local_validation_hash"
    else:
        provider_field = "summary_provider"
        path_field = "summary_local_path"
        proof_field = "summary_local_validation_id"
        hash_field = "summary_local_validation_hash"

    current_provider = (
        getattr(current, provider_field) if current is not None else ModelProvider.CLOUD
    )
    current_path = getattr(current, path_field) if current is not None else None
    current_hash = getattr(current, hash_field) if current is not None else None
    effective_provider = getattr(payload, provider_field) or current_provider
    effective_path = (
        getattr(payload, path_field)
        if path_field in payload.model_fields_set
        else current_path
    )
    path_changed = path_field in payload.model_fields_set and effective_path != current_path
    introduces_local = current_provider is not ModelProvider.LOCAL and (
        effective_provider is ModelProvider.LOCAL
    )
    needs_validation = (
        effective_provider is ModelProvider.LOCAL
        and (path_changed or introduces_local or not current_hash)
    )
    if not needs_validation:
        return None

    return await require_local_model_validation_hash(
        db,
        user_id=user_id,
        category=category,
        model_path=effective_path,
        validation_id=getattr(payload, proof_field),
    )


def _effective_transcription_provider() -> ModelProvider:
    provider = settings.TRANSCRIPTION_PROVIDER.strip().lower()
    if provider == ModelProvider.LOCAL.value:
        return ModelProvider.LOCAL
    return ModelProvider.CLOUD


def _effective_summary_provider() -> ModelProvider:
    provider = settings.LLM_PROVIDER.strip().lower()
    if provider == ModelProvider.OLLAMA.value:
        return ModelProvider.OLLAMA
    if provider == "vllm":
        return ModelProvider.LOCAL
    return ModelProvider.CLOUD


@router.patch("/services/jdr/users/{user_id}", response_model=UserOut)
async def patch_user(
    user_id: UUID,
    payload: UserUpdate,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserOut:
    await _require_admin_user(db, auth)
    try:
        user = await update_user(
            db,
            user_id,
            system_role=payload.system_role,
            password=payload.password,
            status=payload.status,
        )
    except UserNotFoundError as exc:
        raise UserNotFoundAppError("User not found.") from exc
    except LastActiveAdminError as exc:
        raise LastActiveAdminAppError(
            "Cannot remove the last active administrator."
        ) from exc
    return UserOut.model_validate(user)


@router.delete("/services/jdr/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_route(
    user_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    await _require_admin_user(db, auth)
    try:
        await delete_user(db, user_id)
    except UserNotFoundError as exc:
        raise UserNotFoundAppError("User not found.") from exc
    except LastActiveAdminError as exc:
        raise LastActiveAdminAppError(
            "Cannot delete the last active administrator."
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
