"""Password hashing helpers for CustomerGraph authentication."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

try:  # pragma: no cover - depends on local installation
    from passlib.context import CryptContext
except Exception:  # pragma: no cover - safe dev fallback
    CryptContext = None  # type: ignore[assignment]

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto") if CryptContext else None


def get_password_backend_name() -> str:
    """Return the active password hashing backend name."""
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
