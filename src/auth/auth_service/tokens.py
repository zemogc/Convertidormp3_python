from datetime import UTC, datetime, timedelta

import jwt

ALGORITHM = "HS256"


def issue(email: str, is_admin: bool, secret: str, ttl_seconds: int) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": email,
        "admin": is_admin,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def decode(token: str, secret: str) -> dict:
    """Devuelve los claims o lanza jwt.InvalidTokenError."""
    return jwt.decode(token, secret, algorithms=[ALGORITHM], options={"require": ["sub", "exp"]})
