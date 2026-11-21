"""Password hashing (PBKDF2) and JWT (HS256) helpers plus FastAPI auth dependencies."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.errors import ApiError

_ITERATIONS = 200_000


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_b64, digest_b64 = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iterations))
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    student_no: str | None


def create_access_token(username: str, role: str, student_no: str | None) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    ttl = settings.jwt_ttl_minutes * 60
    claims = {
        "iss": settings.jwt_issuer,  # Kong JWT plugin uses `iss` as the credential key
        "sub": username,
        "role": role,
        "studentNo": student_no,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256"), ttl


def decode_token(token: str) -> Principal:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer=settings.jwt_issuer,
                            options={"require": ["exp", "sub", "iss"]})
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "TOKEN_EXPIRED", "Access token has expired", headers={"WWW-Authenticate": "Bearer"})
    except jwt.PyJWTError:
        raise ApiError(401, "INVALID_TOKEN", "Access token is invalid", headers={"WWW-Authenticate": "Bearer"})
    return Principal(username=claims["sub"], role=claims.get("role", ""), student_no=claims.get("studentNo"))


_bearer = HTTPBearer(auto_error=False, description="JWT from POST /api/v1/auth/token")


def current_principal(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise ApiError(401, "UNAUTHORIZED", "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    return decode_token(creds.credentials)


def require_roles(*roles: str):
    def _dep(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.role not in roles:
            raise ApiError(403, "FORBIDDEN", f"Role '{principal.role}' may not perform this operation")
        return principal
    return _dep


def ensure_can_access_student(principal: Principal, student_no: str) -> None:
    """Students may only touch their own account; bank/admin service roles may act for any student.

    This is the rule that stops the AI agent from reading another student's data just because a
    student number appeared in a prompt: the agent always calls the API with the logged-in
    student's token, so a foreign student number yields 403 here.
    """
    if principal.role in {"admin", "bank"}:
        return
    if principal.role == "student" and principal.student_no and principal.student_no.upper() == student_no.upper():
        return
    raise ApiError(403, "FORBIDDEN", "You are not authorized to access this student's account")
