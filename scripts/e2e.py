"""Prueba end-to-end contra el stack de docker compose.

    docker compose up -d --build
    python scripts/e2e.py

Solo usa la libreria estandar. Genera videos de prueba con el ffmpeg de la
imagen del converter, asi que no hace falta tener ffmpeg instalado.
"""

import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "http://localhost:8080"
MAILPIT = "http://localhost:8025"


def request(method, path, *, token=None, body=None, headers=None, base=BASE):
    headers = dict(headers or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if isinstance(body, dict):
        body = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as err:
        return err.code, err.read(), err.headers


def multipart(fields: dict, filename: str, data: bytes):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        header = f'Content-Disposition: form-data; name="{name}"'
        parts.append(f"--{boundary}\r\n{header}\r\n\r\n{value}\r\n".encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: video/x-matroska\r\n\r\n".encode()
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def make_video(seconds: int, audio: bool = True) -> bytes:
    inputs = ["-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=5"]
    if audio:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd = [
        "docker",
        "compose",
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "converter",
        "ffmpeg",
        "-loglevel",
        "error",
        *inputs,
        "-shortest",
        "-f",
        "matroska",
        "pipe:1",
    ]
    return subprocess.run(cmd, check=True, capture_output=True).stdout


def step(msg):
    print(f"\n==> {msg}")


def check(cond, msg):
    if not cond:
        print(f"FALLO: {msg}")
        sys.exit(1)
    print(f"  ok  {msg}")


def wait_healthy(timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if request("GET", "/healthz")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(2)
    check(False, "el gateway no respondio a tiempo")


def follow_events(job_id, token, timeout=180):
    """Lee el stream SSE y devuelve la lista de estados recibidos."""
    req = urllib.request.Request(
        f"{BASE}/api/jobs/{job_id}/events", headers={"Authorization": f"Bearer {token}"}
    )
    events = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if line.startswith("data: "):
                job = json.loads(line[6:])
                events.append(job)
                print(f"      {job['status']:<10} {job['progress']:5.1f}%")
                if job["status"] in ("done", "failed"):
                    break
    return events


def signup(email):
    request("POST", "/api/register", body={"email": email, "password": "contrasena-e2e"})
    basic = base64.b64encode(f"{email}:contrasena-e2e".encode()).decode()
    status, body, _ = request("POST", "/api/login", headers={"Authorization": f"Basic {basic}"})
    check(status == 200, f"login de {email}")
    return json.loads(body)["token"]


def upload(token, data, filename, **fields):
    body, headers = multipart(fields, filename, data)
    status, resp, _ = request("POST", "/api/jobs", token=token, body=body, headers=headers)
    return status, json.loads(resp)


def main():
    step("Esperando al gateway")
    wait_healthy()

    step("Generando videos de prueba")
    # 10 minutos: lo bastante largo para que la conversion emita progreso intermedio.
    video = make_video(600)
    mute = make_video(2, audio=False)
    check(len(video) > 10_000, f"video de prueba ({len(video)} bytes)")

    step("Registro y login")
    run_id = uuid.uuid4().hex[:8]
    email = f"e2e-{run_id}@example.com"
    token = signup(email)
    intruder = signup(f"intruso-{run_id}@example.com")

    step("Subida con opciones: recorte, normalizacion y etiquetas")
    status, job = upload(
        token,
        video,
        "prueba e2e.mkv",
        format="mp3",
        bitrate="128",
        start="0:05",
        end="9:50",
        normalize="1",
        title="Tono",
        artist="E2E",
    )
    check(status == 202, f"trabajo encolado ({status})")

    step("Progreso en vivo por SSE")
    events = follow_events(job["id"], token)
    final = events[-1]
    check(final["status"] == "done", f"estado final 'done' (error: {final['error']})")
    check(abs(final["duration"] - 585) < 0.5, f"duracion recortada a 585 s ({final['duration']})")
    intermediate = [e for e in events if e["status"] == "processing" and 0 < e["progress"] < 100]
    check(
        len(intermediate) >= 1, f"se recibieron {len(intermediate)} eventos de progreso intermedio"
    )

    step("Descarga")
    status, audio, headers = request("GET", f"/api/jobs/{job['id']}/download", token=token)
    check(status == 200, "descarga 200")
    check(audio[:3] == b"ID3", "el archivo es un MP3 con etiquetas ID3")
    check(b"Tono" in audio[:512], "la etiqueta de titulo esta en el archivo")
    check("prueba%20e2e.mp3" in headers["Content-Disposition"], "nombre de archivo correcto")

    step("Cache por contenido")
    start = time.time()
    status, cached = upload(
        token,
        video,
        "copia.mkv",
        format="mp3",
        bitrate="128",
        start="0:05",
        end="9:50",
        normalize="1",
        title="Tono",
        artist="E2E",
    )
    elapsed = time.time() - start
    check(status == 200 and cached["cached"], f"respuesta instantanea desde cache ({elapsed:.2f}s)")

    step("Privacidad")
    status, _, _ = request("GET", f"/api/jobs/{job['id']}/download", token=intruder)
    check(status == 404, "otro usuario no puede descargar el trabajo")

    step("Video sin audio -> error permanente sin reintentos")
    status, bad = upload(token, mute, "mudo.mkv")
    check(status == 202, "encolado")
    final = follow_events(bad["id"], token, timeout=60)[-1]
    check(final["status"] == "failed", "estado 'failed'")
    check("audio" in (final["error"] or ""), f"mensaje claro: {final['error']}")

    step("Email de notificacion (Mailpit)")
    for _ in range(15):
        status, body, _ = request("GET", f"/api/v1/search?query=to:{email}", base=MAILPIT)
        messages = json.loads(body).get("messages", []) if status == 200 else []
        if messages:
            break
        time.sleep(1)
    check(len(messages) == 1, f"llego exactamente 1 email a {email}")
    check("prueba e2e.mp3" in messages[0]["Subject"], f"asunto: {messages[0]['Subject']}")

    print("\nTodo OK")


if __name__ == "__main__":
    main()
