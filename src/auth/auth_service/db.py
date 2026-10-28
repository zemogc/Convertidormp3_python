import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import StaticPool

log = logging.getLogger(__name__)

metadata = MetaData()

users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("email", String(255), unique=True, nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("is_admin", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


class UserExists(Exception):
    pass


@dataclass(frozen=True)
class User:
    email: str
    password_hash: str
    is_admin: bool


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite") and ":memory:" in url:
        # Una sola conexion compartida para que la BD en memoria sobreviva entre requests.
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return create_engine(url, pool_pre_ping=True)


def init_schema(engine: Engine, attempts: int = 30, delay: float = 1.0) -> None:
    """Crea las tablas, esperando a que la base de datos acepte conexiones."""
    for attempt in range(1, attempts + 1):
        try:
            metadata.create_all(engine)
            return
        except OperationalError:
            if attempt == attempts:
                raise
            log.warning("Base de datos no disponible (intento %d/%d)", attempt, attempts)
            time.sleep(delay)


class UserRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def create(self, email: str, password_hash: str, is_admin: bool = False) -> User:
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    insert(users).values(
                        email=email,
                        password_hash=password_hash,
                        is_admin=is_admin,
                        created_at=datetime.now(UTC),
                    )
                )
        except IntegrityError as err:
            raise UserExists(email) from err
        return User(email, password_hash, is_admin)

    def get(self, email: str) -> User | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(users.c.email, users.c.password_hash, users.c.is_admin).where(
                    users.c.email == email
                )
            ).first()
        return User(*row) if row else None
