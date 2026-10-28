import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    database_url: str
    jwt_secret: str
    jwt_ttl_seconds: int = 86400
    admin_email: str | None = None
    admin_password: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        secret = os.environ.get("JWT_SECRET")
        if not secret:
            raise RuntimeError("JWT_SECRET es obligatorio")
        return cls(
            database_url=os.environ.get("DATABASE_URL", "sqlite:///auth.db"),
            jwt_secret=secret,
            jwt_ttl_seconds=int(os.environ.get("JWT_TTL_SECONDS", "86400")),
            admin_email=os.environ.get("ADMIN_EMAIL") or None,
            admin_password=os.environ.get("ADMIN_PASSWORD") or None,
        )
