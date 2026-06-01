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
from app.core.models import Profile, User
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
    LastActiveGmError,
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
    campaign_role_for_profile,
    ensure_default_campaign,
    ensure_user_membership,
    resolve_active_campaign_for_user,
    resolve_campaign_scope_for_auth,
)
from app.services.jdr.db.repositories import CampaignRepository

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


class LastActiveGmAppError(AppError):
    status_code = status.HTTP_409_CONFLICT
    error_type = "last-active-gm"
    title = "Last active GM"


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
        role=campaign_role_for_profile(owner.profile),
    )
    return CampaignScope(
        campaign_id=campaign.id,
        role=campaign_role_for_profile(owner.profile),
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
    try:
        profile = Profile(payload.profile)
    except ValueError:
        return _front_problem(status.HTTP_403_FORBIDDEN, "Forbidden")

    user = await authenticate_user(
        db,
        username=payload.username,
        profile=profile,
        password=payload.password,
    )
    if user is None:
        logger.info(
            "jdr.auth.login_rejected",
            username=payload.username.strip().lower(),
            profile=payload.profile,
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
        user=AuthMeUserOut(id=validated.user.id, username=validated.user.username),
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
    try:
        user = await create_user(
            db,
            username=payload.username,
            profile=payload.profile,
            password=payload.password,
        )
    except DuplicateUserError as exc:
        raise DuplicateUserAppError("Username already exists.") from exc

    scope = await _active_or_default_scope(db, auth)
    if scope is None:
        owner = user if user.profile == Profile.GM else None
        if owner is not None:
            campaign = await ensure_default_campaign(db, owner_user=owner)
        else:
            campaign = None
    else:
        campaign = await CampaignRepository(db).get(scope.campaign_id)
    if campaign is not None:
        await ensure_user_membership(
            db,
            user=user,
            campaign=campaign,
            role=campaign_role_for_profile(user.profile),
        )

    logger.info("jdr.users.created", username=user.username, profile=user.profile.value)
    return UserOut.model_validate(user)


@router.get("/services/jdr/users", response_model=UserListOut)
async def get_users(
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserListOut:
    scope = await _active_or_default_scope(db, auth)
    user_ids = (
        set(await CampaignRepository(db).list_campaign_user_ids(scope.campaign_id))
        if scope is not None
        else set()
    )
    return UserListOut(
        items=[UserOut.model_validate(user) for user in await list_users(db, user_ids)]
    )


@router.patch("/services/jdr/users/{user_id}", response_model=UserOut)
async def patch_user(
    user_id: UUID,
    payload: UserUpdate,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserOut:
    scope = await _ensure_user_in_active_campaign(db, auth, user_id)
    try:
        user = await update_user(
            db,
            user_id,
            profile=payload.profile,
            password=payload.password,
            status=payload.status,
        )
    except UserNotFoundError as exc:
        raise UserNotFoundAppError("User not found.") from exc
    except LastActiveGmError as exc:
        raise LastActiveGmAppError("Cannot remove the last active GM.") from exc
    membership = await CampaignRepository(db).get_membership(
        user_id=user.id,
        campaign_id=scope.campaign_id,
    )
    if membership is not None:
        membership.role = campaign_role_for_profile(user.profile)
    return UserOut.model_validate(user)


@router.delete("/services/jdr/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_route(
    user_id: UUID,
    auth: Annotated[AuthenticatedKey, Depends(require_gm)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    await _ensure_user_in_active_campaign(db, auth, user_id)
    try:
        await delete_user(db, user_id)
    except UserNotFoundError as exc:
        raise UserNotFoundAppError("User not found.") from exc
    except LastActiveGmError as exc:
        raise LastActiveGmAppError("Cannot delete the last active GM.") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
