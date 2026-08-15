"""Password and JWT operations."""

from datetime import datetime, timedelta, timezone

from jose import jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.user import User
from app.schemas.auth import RegisterRequest
from app.services.errors import APIError

password_hash = PasswordHash.recommended()


def _validate_password(password: str) -> None:
    if not any(character.isalpha() for character in password) or not any(character.isdigit() for character in password):
        raise APIError(status_code=422, code="weak_password", message="Password must contain at least one letter and one number")


def register_user(db: Session, request: RegisterRequest) -> User:
    _validate_password(request.password)
    user = User(email=str(request.email).lower(), password_hash=password_hash.hash(request.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise APIError(status_code=409, code="email_already_registered", message="An account with this email already exists") from None
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None or not password_hash.verify(password, user.password_hash):
        raise APIError(status_code=401, code="invalid_credentials", message="Invalid email or password")
    return user


def create_access_token(user: User) -> tuple[str, int]:
    settings = get_settings()
    expires_in = settings.access_token_expire_minutes * 60
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    token = jwt.encode({"sub": str(user.id), "exp": expires_at}, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_in
