"""Security helpers for CustomerGraph AI.

This module keeps JWT creation/verification local and simple so CustomerGraph
can run without a large auth framework. Password hashing prefers bcrypt through
passlib. A PBKDF2 fallback exists only for local development when requirements
were not installed yet.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status

from customergraph.core.config import get_settings

try:  # pragma: no cover - depends on local installation
    from passlib.context import CryptContext
except Exception:  # pragma: no cover - safe dev fallback
    CryptContext = None  # type: ignore[assignment]

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto") if CryptContext else None


def utc_now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def utc_iso() -> str:
    """Return current UTC datetime as ISO string."""
    return utc_now().isoformat()


def parse_utc(value: str) -> datetime:
    """Parse ISO datetime and ensure timezone-aware UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_password_backend_name() -> str:
    """Return active password hashing backend name."""
    return "bcrypt/passlib" if _pwd_context else "pbkdf2_sha256_dev_fallback"


def hash_password(password: str) -> str:
    """Hash a plain password. Never store plain passwords."""
    if _pwd_context:
        return _pwd_context.hash(password)

    # Local fallback when passlib is not installed. It still avoids plain-text
    # storage, but production should use the bcrypt requirements above.
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2_sha256${salt}${base64.urlsafe_b64encode(digest).decode('utf-8')}"


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a plain password against the stored password hash."""
    if _pwd_context and hashed_password.startswith("$2"):
        return _pwd_context.verify(password, hashed_password)

    if hashed_password.startswith("pbkdf2_sha256$"):
        try:
            _, salt, stored_digest = hashed_password.split("$", 2)
            digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
            expected = base64.urlsafe_b64encode(digest).decode("utf-8")
            return hmac.compare_digest(expected, stored_digest)
        except ValueError:
            return False

    return False


def hash_token(token: str) -> str:
    """Hash opaque refresh tokens before storing them in SQLite."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_refresh_token() -> str:
    """Create an opaque random refresh token."""
    return secrets.token_urlsafe(48)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def create_access_token(*, user_id: str, email: str, role: str, token_version: int) -> str:
    """Create a signed HS256 JWT access token.

    ``token_version`` is compared against the database for every protected
    request. Logout increments the database version, immediately invalidating
    every old access token for that user.
    """
    settings = get_settings()
    if settings.jwt_algorithm != "HS256":
        raise RuntimeError("CustomerGraph currently supports JWT_ALGORITHM=HS256 only.")
    now = utc_now()
    exp = now + timedelta(minutes=settings.access_token_expire_minutes)
    header = {"alg": settings.jwt_algorithm, "typ": "JWT"}
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "token_type": "access",
        "token_version": token_version,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    signing_input = ".".join(
        [
            _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )
    signature = hmac.new(settings.auth_secret_key.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a signed HS256 JWT access token."""
    settings = get_settings()
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired access token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if settings.jwt_algorithm != "HS256":
        raise RuntimeError("CustomerGraph currently supports JWT_ALGORITHM=HS256 only.")

    try:
        header_raw, payload_raw, signature_raw = token.split(".")
        signing_input = f"{header_raw}.{payload_raw}"
        expected_signature = hmac.new(
            settings.auth_secret_key.encode("utf-8"),
            signing_input.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        actual_signature = _b64url_decode(signature_raw)
        if not hmac.compare_digest(expected_signature, actual_signature):
            raise credentials_error

        header = json.loads(_b64url_decode(header_raw))
        payload = json.loads(_b64url_decode(payload_raw))
        if not isinstance(header, dict) or not isinstance(payload, dict):
            raise credentials_error

        expires_at = payload.get("exp")
        if not isinstance(expires_at, int) or expires_at < int(utc_now().timestamp()):
            raise credentials_error
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise exc
        raise credentials_error from exc

    if header.get("alg") != "HS256":
        raise credentials_error
    if payload.get("token_type") != "access":
        raise credentials_error
    if not isinstance(payload.get("sub"), str) or not payload["sub"].strip():
        raise credentials_error
    if not isinstance(payload.get("token_version"), int) or payload["token_version"] < 0:
        raise credentials_error

    return payload
