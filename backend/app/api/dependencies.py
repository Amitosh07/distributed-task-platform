"""Authentication dependencies shared by protected endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.database import get_db
from app.db.models.user import User
from app.services.errors import APIError

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if credentials is None:
        raise APIError(status_code=401, code="authentication_required", message="Authentication is required")
    try:
        payload = jwt.decode(credentials.credentials, get_settings().jwt_secret_key, algorithms=[get_settings().jwt_algorithm])
        subject = payload.get("sub")
        user_id = UUID(subject) if subject else None
    except (JWTError, ValueError):
        raise APIError(status_code=401, code="invalid_token", message="Invalid or expired access token") from None
    user = db.get(User, user_id)
    if user is None:
        raise APIError(status_code=401, code="invalid_token", message="Invalid or expired access token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
