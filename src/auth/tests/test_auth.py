import base64

import pytest
from auth_service import tokens
from auth_service.app import create_app
from auth_service.config import Config
from auth_service.db import UserRepository, make_engine

SECRET = "test-secret-with-enough-length-for-hs256"


@pytest.fixture
def engine():
    return make_engine("sqlite:///:memory:")


@pytest.fixture
def client(engine):
    config = Config(
        database_url="sqlite:///:memory:",
        jwt_secret=SECRET,
        jwt_ttl_seconds=60,
        admin_email="Admin@Example.com",
        admin_password="admin-password",
    )
    app = create_app(config, engine)
    return app.test_client()


def basic(email, password):
    raw = base64.b64encode(f"{email}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def register(client, email="ana@example.com", password="supersecreta"):
    return client.post("/register", json={"email": email, "password": password})


def test_register_and_login(client):
    assert register(client).status_code == 201

    resp = client.post("/login", headers=basic("ana@example.com", "supersecreta"))
    assert resp.status_code == 200
    claims = tokens.decode(resp.json["token"], SECRET)
    assert claims["sub"] == "ana@example.com"
    assert claims["admin"] is False


def test_email_is_case_insensitive(client):
    register(client, email="Ana@Example.com")
    resp = client.post("/login", headers=basic("ANA@example.com", "supersecreta"))
    assert resp.status_code == 200


def test_duplicate_registration_conflicts(client):
    register(client)
    assert register(client).status_code == 409


@pytest.mark.parametrize(
    "email,password",
    [("no-es-email", "supersecreta"), ("ana@example.com", "corta"), ("", "")],
)
def test_register_validation(client, email, password):
    assert register(client, email, password).status_code == 400


def test_login_wrong_password(client):
    register(client)
    resp = client.post("/login", headers=basic("ana@example.com", "otra-clave"))
    assert resp.status_code == 401


def test_login_unknown_user(client):
    resp = client.post("/login", headers=basic("nadie@example.com", "supersecreta"))
    assert resp.status_code == 401


def test_login_without_credentials(client):
    assert client.post("/login").status_code == 401


def test_passwords_are_hashed(client, engine):
    register(client)
    stored = UserRepository(engine).get("ana@example.com").password_hash
    assert "supersecreta" not in stored
    assert stored.startswith(("scrypt:", "pbkdf2:"))


def test_validate_token(client):
    register(client)
    token = client.post("/login", headers=basic("ana@example.com", "supersecreta")).json["token"]

    resp = client.post("/validate", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json == {"email": "ana@example.com", "admin": False}


def test_validate_rejects_bad_tokens(client):
    assert client.post("/validate").status_code == 401
    assert client.post("/validate", headers={"Authorization": "Bearer basura"}).status_code == 401
    forged = tokens.issue("ana@example.com", True, "otro-secreto-distinto-al-real-123", 60)
    assert (
        client.post("/validate", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    )


def test_validate_rejects_expired_token(client):
    expired = tokens.issue("ana@example.com", False, SECRET, -1)
    assert (
        client.post("/validate", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    )


def test_admin_is_bootstrapped(client):
    token = client.post("/login", headers=basic("admin@example.com", "admin-password")).json[
        "token"
    ]
    resp = client.post("/validate", headers={"Authorization": f"Bearer {token}"})
    assert resp.json["admin"] is True
