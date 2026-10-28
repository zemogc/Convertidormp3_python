import base64
import json


def basic(email, password):
    raw = base64.b64encode(f"{email}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def finish(deps, job_id, data=b"ID3-audio"):
    """Simula que el converter termino el trabajo."""
    deps["storage"].outputs["out-" + job_id] = data
    deps["jobs"].update(
        job_id,
        status="done",
        progress=100.0,
        output_fid="out-" + job_id,
        output_size=len(data),
        waveform=[0.2, 1.0, 0.4],
    )


def test_healthz(client):
    assert client.get("/healthz").json == {"status": "ok"}


def test_formats_are_public(client):
    data = client.get("/api/formats").json
    assert data["default_bitrate"] == 192
    by_id = {f["id"]: f["bitrates"] for f in data["formats"]}
    assert 320 in by_id["mp3"]
    assert by_id["flac"] == []


def test_index_serves_ui(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<html" in resp.data


def test_register_and_login_are_proxied(client):
    resp = client.post("/api/register", json={"email": "ana@example.com", "password": "12345678"})
    assert resp.status_code == 201

    resp = client.post("/api/login", headers=basic("ana@example.com", "12345678"))
    assert resp.status_code == 200
    assert resp.json["token"] == "token-ana"

    assert client.post("/api/login", headers=basic("ana@example.com", "mala")).status_code == 401
    assert client.post("/api/login").status_code == 401


def test_endpoints_require_token(client, upload):
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/jobs", headers={"Authorization": "Bearer falso"}).status_code == 401
    assert upload({}).status_code == 401


def test_upload_queues_job(client, deps, ana, upload):
    resp = upload(ana, filename="Mi Video (final).mp4", format="ogg", bitrate="128")
    assert resp.status_code == 202
    job = resp.json
    assert job["status"] == "queued"
    assert job["output_name"] == "Mi Video _final.ogg"
    assert job["options"]["format"] == "ogg"

    [(queue, message)] = deps["publisher"].messages
    assert queue == "video"
    assert message == {
        "job_id": job["id"],
        "video_fid": deps["jobs"].get(job["id"])["video_fid"],
        "username": "ana@example.com",
    }


def test_upload_validation(client, ana, upload):
    resp = client.post("/api/jobs", headers=ana, data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert upload(ana, format="exe").status_code == 400


def test_upload_too_large(client, ana, upload):
    resp = upload(ana, data=b"x" * (2 * 1024 * 1024))
    assert resp.status_code == 413


def test_queue_down_returns_503_and_cleans_up(client, deps, ana, upload):
    deps["publisher"].down = True
    assert upload(ana).status_code == 503
    assert deps["storage"].videos == {}
    [job] = deps["jobs"].docs.values()
    assert job["status"] == "failed"


def test_identical_upload_is_served_from_cache(client, deps, ana, beto, upload):
    first = upload(ana, data=b"same-bytes").json
    finish(deps, first["id"])

    again = upload(beto, data=b"same-bytes")
    assert again.status_code == 200
    assert again.json["status"] == "done"
    assert again.json["cached"] is True
    assert again.json["waveform"] == [0.2, 1.0, 0.4]
    assert len(deps["publisher"].messages) == 1  # no se volvio a encolar

    # Opciones distintas => clave distinta => se convierte de nuevo.
    other = upload(ana, data=b"same-bytes", format="flac")
    assert other.status_code == 202


def test_cache_ignored_when_output_was_deleted(client, deps, ana, upload):
    first = upload(ana, data=b"bytes").json
    finish(deps, first["id"])
    deps["storage"].outputs.clear()
    assert upload(ana, data=b"bytes").status_code == 202


def test_jobs_are_private(client, deps, ana, beto, upload):
    job = upload(ana).json
    finish(deps, job["id"])
    assert client.get(f"/api/jobs/{job['id']}", headers=ana).status_code == 200
    assert client.get(f"/api/jobs/{job['id']}", headers=beto).status_code == 404
    assert client.get(f"/api/jobs/{job['id']}/download", headers=beto).status_code == 404
    assert client.get("/api/jobs", headers=beto).json == []


def test_list_jobs_newest_first(client, ana, upload):
    a = upload(ana, data=b"a").json
    b = upload(ana, data=b"b").json
    ids = [j["id"] for j in client.get("/api/jobs", headers=ana).json]
    assert ids == [b["id"], a["id"]]


def test_download(client, deps, ana, upload):
    job = upload(ana, filename="canción.mp4").json
    assert client.get(f"/api/jobs/{job['id']}/download", headers=ana).status_code == 409

    finish(deps, job["id"], b"audio-bytes")
    resp = client.get(f"/api/jobs/{job['id']}/download", headers=ana)
    assert resp.status_code == 200
    assert resp.data == b"audio-bytes"
    assert resp.mimetype == "audio/mpeg"
    assert "filename*=UTF-8''canci%C3%B3n.mp3" in resp.headers["Content-Disposition"]


def test_events_stream_until_done(client, deps, ana, upload):
    job = upload(ana).json
    deps["jobs"].update(job["id"], status="processing", progress=42.0)
    finish(deps, job["id"])  # ya termino: el stream emite el estado final y cierra

    resp = client.get(f"/api/jobs/{job['id']}/events", headers=ana)
    assert resp.mimetype == "text/event-stream"
    events = [
        json.loads(line[len("data: ") :])
        for line in resp.get_data(as_text=True).splitlines()
        if line.startswith("data: ")
    ]
    assert events[-1]["status"] == "done"
    assert events[-1]["progress"] == 100.0


def test_events_for_foreign_job(client, ana, beto, upload):
    job = upload(ana).json
    assert client.get(f"/api/jobs/{job['id']}/events", headers=beto).status_code == 404
