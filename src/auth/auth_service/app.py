import logging
import re

import jwt
from flask import Flask, jsonify, request
from sqlalchemy.engine import Engine
from werkzeug.security import check_password_hash, generate_password_hash

from . import tokens
from .config import Config
from .db import UserExists, UserRepository, init_schema, make_engine

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8

# Hash fijo para comparar cuando el usuario no existe: el login tarda lo mismo
# exista o no la cuenta, asi no se pueden enumerar usuarios por tiempo de respuesta.
_DUMMY_HASH = generate_password_hash("timing-attack-padding")


def create_app(config: Config | None = None, engine: Engine | None = None) -> Flask:
    config = config or Config.from_env()
    engine = engine or make_engine(config.database_url)
    init_schema(engine)
    repo = UserRepository(engine)
    _bootstrap_admin(repo, config)

    app = Flask(__name__)

    def error(message: str, status: int):
        return jsonify(error=message), status

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    @app.post("/register")
    def register():
        body = request.get_json(silent=True) or {}
        email = str(body.get("email", "")).strip().lower()
        password = str(body.get("password", ""))
        if not EMAIL_RE.match(email):
            return error("email invalido", 400)
        if len(password) < MIN_PASSWORD:
            return error(f"la contrasena debe tener al menos {MIN_PASSWORD} caracteres", 400)
        try:
            repo.create(email, generate_password_hash(password))
        except UserExists:
            return error("el usuario ya existe", 409)
        return jsonify(email=email), 201

    @app.post("/login")
    def login():
        auth = request.authorization
        if not auth or not auth.username or not auth.password:
            return error("faltan credenciales", 401)
        user = repo.get(auth.username.strip().lower())
        valid = check_password_hash(user.password_hash if user else _DUMMY_HASH, auth.password)
        if not user or not valid:
            return error("credenciales invalidas", 401)
        token = tokens.issue(user.email, user.is_admin, config.jwt_secret, config.jwt_ttl_seconds)
        return jsonify(token=token, expires_in=config.jwt_ttl_seconds)

    @app.post("/validate")
    def validate():
        header = request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return error("falta el token", 401)
        try:
            claims = tokens.decode(token, config.jwt_secret)
        except jwt.InvalidTokenError:
            return error("token invalido o expirado", 401)
        return jsonify(email=claims["sub"], admin=bool(claims.get("admin")))

    return app


def _bootstrap_admin(repo: UserRepository, config: Config) -> None:
    if not (config.admin_email and config.admin_password):
        return
    email = config.admin_email.strip().lower()
    if repo.get(email) is None:
        try:
            repo.create(email, generate_password_hash(config.admin_password), is_admin=True)
            logging.getLogger(__name__).info("Usuario admin %s creado", email)
        except UserExists:
            pass  # otra replica lo creo al mismo tiempo
