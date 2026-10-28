import requests

TIMEOUT = 5


class AuthUnavailable(Exception):
    pass


class AuthClient:
    """Cliente HTTP del servicio auth. Devuelve (status, json) para reenviar tal cual."""

    def __init__(self, address: str):
        self.base = f"http://{address}"

    def _post(self, path: str, **kwargs) -> tuple[int, dict]:
        try:
            resp = requests.post(f"{self.base}{path}", timeout=TIMEOUT, **kwargs)
        except requests.RequestException as err:
            raise AuthUnavailable(str(err)) from err
        try:
            body = resp.json()
        except ValueError:
            body = {"error": resp.text}
        return resp.status_code, body

    def register(self, email: str, password: str) -> tuple[int, dict]:
        return self._post("/register", json={"email": email, "password": password})

    def login(self, email: str, password: str) -> tuple[int, dict]:
        return self._post("/login", auth=(email, password))

    def validate(self, token: str) -> dict | None:
        status, body = self._post("/validate", headers={"Authorization": f"Bearer {token}"})
        return body if status == 200 else None
