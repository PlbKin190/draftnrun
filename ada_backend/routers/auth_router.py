import logging
from typing import Annotated
from uuid import UUID
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Header, Body, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from supabase import Client, create_client
from sqlalchemy.orm import Session

from ada_backend.repositories.project_repository import get_project
from settings import settings
from ada_backend.database.setup_db import get_db
from ada_backend.services.api_key_service import (
    get_api_keys_service,
    generate_api_key,
    deactivate_api_key_service,
    verify_api_key,
    verify_ingestion_api_key,
)
from ada_backend.services.user_roles_service import get_user_access_to_organization
from ada_backend.schemas.auth_schema import (
    SupabaseUser,
    ApiKeyGetResponse,
    ApiKeyCreateRequest,
    ApiKeyCreatedResponse,
    ApiKeyDeleteRequest,
    ApiKeyDeleteResponse,
    VerifiedApiKey,
)

LOGGER = logging.getLogger(__name__)


class UserRights(Enum):
    SUPER_ADMIN = ("super-admin",)
    ADMIN = ("super-admin", "admin")
    WRITER = ("super-admin", "admin", "developer")
    READER = ("super-admin", "admin", "developer", "member")
    USER = ("super-admin", "admin", "developer", "member", "user")


if not settings.SUPABASE_PROJECT_URL or not settings.SUPABASE_PROJECT_KEY:
    raise ValueError("SUPABASE_PROJECT_URL and SUPABASE_PROJECT_KEY must be set")

supabase: Client = create_client(settings.SUPABASE_PROJECT_URL, settings.SUPABASE_PROJECT_KEY)
bearer = HTTPBearer()
router = APIRouter(prefix="/auth", tags=["Auth"])


async def get_user_from_supabase_token(
    authorization: HTTPAuthorizationCredentials = Depends(bearer),
) -> SupabaseUser:
    """
    Validate Supabase JWT from Authorization header and return user info.

    Args:
        authorization (Optional[str]): Supabase JWT in the 'Authorization' header.

    Returns:
        SupabaseUser: User information (e.g., user.id, user.email).
    """
    supabase_token = authorization.credentials

    try:
        user_response = supabase.auth.get_user(supabase_token)
        if not user_response or not user_response.user:
            raise HTTPException(status_code=401, detail="Invalid Supabase token")

        return SupabaseUser(
            id=user_response.user.id,
            email=user_response.user.email,
            token=supabase_token,
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail="Failed to validate Supabase token") from e


def user_has_access_to_project_dependency(allowed_roles: set[str]):
    """
    Dependency that checks if a user has access to a project and returns the user if they do.
    Raises HTTP 403 if the user doesn't have access.
    """

    async def wrapper(
        project_id: UUID,
        user: Annotated[SupabaseUser, Depends(get_user_from_supabase_token)],
        session: Session = Depends(get_db),
    ) -> SupabaseUser:
        try:
            project = get_project(session, project_id)
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            access = await get_user_access_to_organization(
                user=user,
                organization_id=project.organization_id,
            )

            LOGGER.info(f"User {user.id=} has access to project {project_id=} with role {access.role=}")
            if access.role not in allowed_roles:
                raise HTTPException(
                    status_code=403,
                    detail="You don't have access to this project",
                )
        except ValueError as e:
            raise HTTPException(
                status_code=403,
                detail=str(e),
            ) from e
        return user

    return wrapper


def user_has_access_to_organization_dependency(allowed_roles: set[str]):
    """
    Dependency that checks if a user has access to a project and returns the user if they do.
    Raises HTTP 403 if the user doesn't have access.
    """

    async def wrapper(
        organization_id: UUID,
        user: Annotated[SupabaseUser, Depends(get_user_from_supabase_token)],
    ) -> SupabaseUser:
        try:
            access = await get_user_access_to_organization(
                user=user,
                organization_id=organization_id,
            )
            LOGGER.info(f"User {user.id=} has access to organization {organization_id=} with role {access.role=}")
            if access.role not in allowed_roles:
                raise HTTPException(
                    status_code=403,
                    detail="You don't have access to this organization",
                )
        except ValueError as e:
            raise HTTPException(
                status_code=403,
                detail="You don't have access to this organization",
            ) from e
        return user

    return wrapper


@router.get("/api-key", summary="Get API Keys")
async def get_api_keys(
    user: Annotated[
        SupabaseUser,
        Depends(user_has_access_to_project_dependency(allowed_roles=UserRights.USER.value)),
    ],
    session: Session = Depends(get_db),
    project_id: UUID = Query(..., description="The ID of the project to retrieve API keys for"),
) -> ApiKeyGetResponse:
    """
    Get all active API keys for the authenticated user.
    """
    if not user.id:
        raise HTTPException(status_code=400, detail="User ID not found")
    try:
        return get_api_keys_service(session, project_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get API keys") from e


@router.post("/api-key", summary="Create API Key")
async def create_api_key(
    user: Annotated[SupabaseUser, Depends(get_user_from_supabase_token)],
    session: Session = Depends(get_db),
    api_key_create: ApiKeyCreateRequest = Body(...),
) -> ApiKeyCreatedResponse:
    """
    Generate and store a new API key for the authenticated user.
    This API key gives users access to run inferences using an any Agent or Pipeline
    that they have access to (i.e. they are a member of the organization that
    the Agent or Pipeline belongs to)

    Args:
        user (SupabaseUser): User information from Supabase.
        db (Session): Database session.

    Returns:
        ApiKeyCreatedResponse: Generated API key.

    Raises:
        HTTPException: If the API key cannot be created.
    """
    if not user.id:
        raise HTTPException(status_code=400, detail="User ID not found")

    _is_user = user_has_access_to_project_dependency(
        allowed_roles=set(UserRights.USER.value),
    )
    # Check if user has access to project. If not, a 403 is raised
    user = await _is_user(project_id=api_key_create.project_id, user=user, session=session)

    try:
        return generate_api_key(
            session=session,
            project_id=api_key_create.project_id,
            key_name=api_key_create.key_name,
            creator_user_id=user.id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to create API key") from e


@router.delete("/api-key", summary="Delete API Key")
async def delete_api_key_route(
    user: Annotated[
        SupabaseUser,
        Depends(user_has_access_to_project_dependency(allowed_roles=UserRights.ADMIN.value)),
    ],
    session: Session = Depends(get_db),
    api_key_delete: ApiKeyDeleteRequest = Body(...),
) -> ApiKeyDeleteResponse:
    """
    Delete the API key for the authenticated user.

    Args:
        user (SupabaseUser): User information from Supabase.
        db (Session): Database session.

    Returns:
        ApiKeyDeleteResponse: Success message.
    """
    if not user.id:
        raise HTTPException(status_code=400, detail="User ID not found")

    try:
        deactivate_api_key_service(
            session,
            key_id=api_key_delete.key_id,
            revoker_user_id=user.id,
        )

        return ApiKeyDeleteResponse(
            message="API key deleted successfully",
            key_id=api_key_delete.key_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to delete API key") from e


async def verify_api_key_dependency(
    x_api_key: str = Header(..., alias="X-API-Key"),
    session: Session = Depends(get_db),
) -> VerifiedApiKey:
    """
    Dependency to verify an API key from the 'X-API-Key' header.
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    cleaned_x_api_key = x_api_key.replace("\\n", "\n").strip('"')

    try:
        return verify_api_key(session, private_key=cleaned_x_api_key)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e


async def verify_ingestion_api_key_dependency(
    ingestion_api_key: str = Header(..., alias="X-Ingestion-API-Key"),
) -> None:
    """
    Dependency to verify an ingestion API key from the 'X-Ingestion-API-Key' header.
    """
    hashed_key = verify_ingestion_api_key(private_key=ingestion_api_key)
    if hashed_key != settings.INGESTION_API_KEY_HASHED:
        raise HTTPException(status_code=401, detail="Invalid ingestion API key")
