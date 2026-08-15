from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUser
from app.db.database import get_db
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.services.auth_service import authenticate_user, create_access_token, register_user

router = APIRouter(prefix="/v1/auth", tags=["auth"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest, db: DbSession) -> UserResponse:
    return register_user(db, request)


@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, db: DbSession) -> TokenResponse:
    user = authenticate_user(db, str(request.email), request.password)
    token, expires_in = create_access_token(user)
    return TokenResponse(access_token=token, expires_in=expires_in, user=UserResponse.model_validate(user))


@router.get("/me", response_model=UserResponse)
def me(current_user: CurrentUser) -> UserResponse:
    return current_user
