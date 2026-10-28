import hashlib
import json
import logging
import re
import time
import uuid
from functools import wraps
from pathlib import PurePath
from urllib.parse import quote

from flask import Flask, Response, g, jsonify, request

from .auth_client import AuthClient, AuthUnavailable
from .config import Config
from .options import DEFAULT_BITRATE, FORMATS, OptionsError, parse_options
from .publisher import QueueUnavailable
from .stores import CHUNK, utcnow

log = logging.getLogger(__name__)

MIME_TYPES = {
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "flac": "audio/flac",
    "wav": "audio/wav",
}
FINAL_STATES = {"done", "failed", "canceled"}
ACTIVE_STATES = {"queued", "processing"}
_UNSAFE_NAME = re.compile(r"[^\w\-. ]+", re.UNICODE)


def create_app(
    config: Config | None = None,
    *,
    auth=None,
    storage=None,
    jobs=None,
    publisher=None,
) -> Flask:
    """Construye la app. Las dependencias se inyectan en tests; si faltan se crean las reales."""
    config = config or Config.from_env()
    if storage is None or jobs is None:
        from pymongo import MongoClient

        from .stores import GridStorage, MongoJobs

        mongo = MongoClient(config.mongo_uri, tz_aware=True)
        storage = storage or GridStorage(mongo)
        jobs = jobs or MongoJobs(mongo)
    if publisher is None:
        from .publisher import Publisher

        publisher = Publisher(config.rabbitmq_host)
    auth = auth or AuthClient(config.auth_svc_address)

    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = config.max_upload_mb * 1024 * 1024

    def error(message: str, status: int):
        return jsonify(error=message), status

    def require_auth(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            scheme, _, token = request.headers.get("Authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not token:
                return error("falta el token", 401)
            try:
                claims = auth.validate(token)
            except AuthUnavailable:
                return error("servicio de autenticacion no disponible", 503)
            if not claims:
                return error("token invalido o expirado", 401)
            g.user = claims["email"]
            return view(*args, **kwargs)

        return wrapper

    def owned_job(job_id: str):
        job = jobs.get(job_id)
        # 404 tambien para trabajos ajenos: no revelamos que existen.
        return job if job and job["owner"] == g.user else None

    @app.errorhandler(413)
    def too_large(_):
        return error(f"el archivo supera {config.max_upload_mb} MB", 413)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok")

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/formats")
    def formats():
        return jsonify(
            default_bitrate=DEFAULT_BITRATE,
            formats=[{"id": f, "bitrates": list(b) if b else []} for f, b in FORMATS.items()],
        )

    @app.post("/api/register")
    def register():
        body = request.get_json(silent=True) or {}
        try:
            status, payload = auth.register(body.get("email", ""), body.get("password", ""))
        except AuthUnavailable:
            return error("servicio de autenticacion no disponible", 503)
        return jsonify(payload), status

    @app.post("/api/login")
    def login():
        creds = request.authorization
        if not creds or not creds.username or not creds.password:
            return error("faltan credenciales", 401)
        try:
            status, payload = auth.login(creds.username, creds.password)
        except AuthUnavailable:
            return error("servicio de autenticacion no disponible", 503)
        return jsonify(payload), status

    @app.get("/api/me")
    @require_auth
    def me():
        return jsonify(email=g.user)

    @app.post("/api/jobs")
    @require_auth
    def create_job():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return error("falta el archivo (campo 'file')", 400)
        try:
            options = parse_options(request.form)
        except OptionsError as err:
            return error(str(err), 400)

        content_hash = _sha256(upload.stream)
        cache_key = hashlib.sha256(
            f"{content_hash}:{json.dumps(options, sort_keys=True)}".encode()
        ).hexdigest()
        now = utcnow()
        job = {
            "_id": uuid.uuid4().hex,
            "owner": g.user,
            "filename": upload.filename,
            "output_name": _output_name(upload.filename, options["format"]),
            "options": options,
            "cache_key": cache_key,
            "status": "queued",
            "progress": 0.0,
            "attempts": 0,
            "video_fid": None,
            "output_fid": None,
            "output_size": None,
            "duration": None,
            "cached": False,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }

        hit = jobs.find_cached(cache_key)
        if hit and storage.open_output(hit["output_fid"]) is not None:
            # Mismo archivo + mismas opciones ya convertido: resultado instantaneo.
            job.update(
                status="done",
                progress=100.0,
                cached=True,
                output_fid=hit["output_fid"],
                output_size=hit.get("output_size"),
                duration=hit.get("duration"),
                waveform=hit.get("waveform"),
            )
            jobs.create(job)
            return jsonify(_public(job)), 200

        job["video_fid"] = storage.put_video(upload.stream, upload.filename, upload.mimetype)
        jobs.create(job)
        try:
            publisher.publish(
                config.video_queue,
                {"job_id": job["_id"], "video_fid": job["video_fid"], "username": g.user},
            )
        except QueueUnavailable:
            storage.delete_video(job["video_fid"])
            jobs.update(job["_id"], status="failed", error="cola no disponible", video_fid=None)
            return error("cola de conversion no disponible, intenta de nuevo", 503)
        return jsonify(_public(job)), 202

    @app.get("/api/jobs")
    @require_auth
    def list_jobs():
        return jsonify([_public(j) for j in jobs.list_for_owner(g.user)])

    @app.get("/api/jobs/<job_id>")
    @require_auth
    def get_job(job_id):
        job = owned_job(job_id)
        return jsonify(_public(job)) if job else error("trabajo no encontrado", 404)

    @app.post("/api/jobs/<job_id>/cancel")
    @require_auth
    def cancel_job(job_id):
        job = owned_job(job_id)
        if not job:
            return error("trabajo no encontrado", 404)
        result = jobs.cancel(job_id)
        if result is None:
            return error("el trabajo ya termino", 409)
        if result == "canceled" and job.get("video_fid"):
            # Nadie lo va a procesar: el converter descarta trabajos cancelados.
            storage.delete_video(job["video_fid"])
            jobs.update(job_id, video_fid=None)
        return jsonify(_public(jobs.get(job_id))), 202 if result == "canceling" else 200

    @app.delete("/api/jobs/<job_id>")
    @require_auth
    def delete_job(job_id):
        job = owned_job(job_id)
        if not job:
            return error("trabajo no encontrado", 404)
        if job["status"] in ACTIVE_STATES:
            return error("cancela el trabajo antes de borrarlo", 409)
        jobs.delete(job_id)
        if job.get("video_fid"):
            storage.delete_video(job["video_fid"])
        # El audio puede estar compartido con resultados de cache de otros trabajos.
        if job.get("output_fid") and not jobs.output_in_use(job["output_fid"], job_id):
            storage.delete_output(job["output_fid"])
        return "", 204

    @app.get("/api/jobs/<job_id>/events")
    @require_auth
    def job_events(job_id):
        """Server-Sent Events con el progreso del trabajo hasta que termina."""
        if not owned_job(job_id):
            return error("trabajo no encontrado", 404)

        def stream():
            last = None
            deadline = time.monotonic() + config.sse_max_seconds
            last_ping = time.monotonic()
            while time.monotonic() < deadline:
                job = jobs.get(job_id)
                if job is None:
                    return
                snapshot = _public(job)
                key = (
                    snapshot["status"],
                    snapshot["progress"],
                    snapshot["error"],
                    snapshot["canceling"],
                )
                if key != last:
                    last = key
                    yield f"event: job\ndata: {json.dumps(snapshot)}\n\n"
                    if snapshot["status"] in FINAL_STATES:
                        return
                elif time.monotonic() - last_ping > 15:
                    last_ping = time.monotonic()
                    yield ": ping\n\n"
                time.sleep(config.sse_poll_seconds)

        return Response(
            stream(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/jobs/<job_id>/download")
    @require_auth
    def download(job_id):
        job = owned_job(job_id)
        if not job:
            return error("trabajo no encontrado", 404)
        if job["status"] != "done":
            return error("el trabajo aun no termina", 409)
        out = storage.open_output(job["output_fid"])
        if out is None:
            return error("el archivo de salida ya no existe", 410)

        def chunks():
            while data := out.read(CHUNK):
                yield data

        name = job["output_name"]
        ascii_name = name.encode("ascii", "ignore").decode() or "audio"
        return Response(
            chunks(),
            mimetype=MIME_TYPES.get(job["options"]["format"], "application/octet-stream"),
            headers={
                "Content-Length": str(out.length),
                "Content-Disposition": (
                    f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"
                ),
            },
        )

    return app


def _sha256(stream) -> str:
    digest = hashlib.sha256()
    while data := stream.read(CHUNK):
        digest.update(data)
    stream.seek(0)
    return digest.hexdigest()


def _output_name(filename: str, fmt: str) -> str:
    stem = _UNSAFE_NAME.sub("_", PurePath(filename.replace("\\", "/")).stem).strip(" ._")
    return f"{stem or 'audio'}.{fmt}"


def _public(job: dict) -> dict:
    return {
        "id": job["_id"],
        "status": job["status"],
        "progress": round(float(job.get("progress") or 0), 1),
        "filename": job["filename"],
        "output_name": job["output_name"],
        "options": job["options"],
        "output_size": job.get("output_size"),
        "duration": job.get("duration"),
        "waveform": job.get("waveform"),
        "cached": job.get("cached", False),
        "canceling": bool(job.get("cancel_requested")) and job["status"] == "processing",
        "error": job.get("error"),
        "created_at": job["created_at"].isoformat(),
        "updated_at": job["updated_at"].isoformat(),
    }
