import json
import logging
import os
import tempfile
import time
from collections.abc import Callable

from . import ffmpeg
from .config import Config

log = logging.getLogger(__name__)

Publish = Callable[[str, dict], None]


class Worker:
    """Procesa un mensaje de la cola de videos.

    Nunca lanza excepciones: decide si el trabajo termino, se reintenta
    (cola de reintento con espera) o falla definitivamente (cola de mensajes muertos).
    Despues de `handle` el consumer siempre hace ack del mensaje original.
    """

    def __init__(self, storage, jobs, config: Config, runner=ffmpeg, clock=time.monotonic):
        self.storage = storage
        self.jobs = jobs
        self.config = config
        self.ffmpeg = runner
        self.clock = clock

    def handle(self, body: bytes, publish: Publish) -> None:
        try:
            message = json.loads(body)
            job_id = message["job_id"]
        except (ValueError, KeyError, TypeError):
            log.error("Mensaje invalido descartado: %r", body[:200])
            publish(self.config.dead_letter_queue, {"raw": body.decode(errors="replace")})
            return

        job = self.jobs.get(job_id)
        if job is None:
            log.warning("Trabajo %s no existe, se descarta", job_id)
            return
        if job["status"] == "done":
            log.info("Trabajo %s ya estaba terminado (entrega duplicada)", job_id)
            return

        job = self.jobs.start(job_id)
        attempt = job["attempts"]
        log.info("Trabajo %s: intento %d/%d", job_id, attempt, self.config.max_attempts)
        try:
            self._convert(job)
        except ffmpeg.PermanentError as err:
            self._fail(job, str(err), message, publish)
            return
        except Exception as err:
            log.exception("Trabajo %s fallo en el intento %d", job_id, attempt)
            if attempt >= self.config.max_attempts:
                self._fail(job, f"fallo tras {attempt} intentos: {err}", message, publish)
            else:
                self.jobs.update(
                    job_id,
                    status="queued",
                    progress=0.0,
                    error=f"reintentando ({attempt}/{self.config.max_attempts}): {err}",
                )
                publish(self.config.retry_queue, message)
            return

        try:
            publish(
                self.config.mp3_queue,
                {
                    "job_id": job_id,
                    "username": job["owner"],
                    "output_name": job["output_name"],
                },
            )
        except Exception:
            # El audio ya esta listo; perder el email no justifica reconvertir.
            log.exception("No se pudo publicar la notificacion de %s", job_id)

    def _convert(self, job: dict) -> None:
        job_id, options = job["_id"], job["options"]
        with tempfile.TemporaryDirectory(prefix="convert-") as tmp:
            src = os.path.join(tmp, "input")
            dst = os.path.join(tmp, f"output.{options['format']}")
            self.storage.download_video(job["video_fid"], src)

            total = ffmpeg.clip_duration(self.ffmpeg.probe(src), options)
            self.ffmpeg.convert(src, dst, options, total, self._progress_reporter(job_id))

            size = os.path.getsize(dst)
            output_fid = self.storage.put_output(
                dst, job["output_name"], {"job_id": job_id, "owner": job["owner"]}
            )

        self.jobs.update(
            job_id,
            status="done",
            progress=100.0,
            output_fid=output_fid,
            output_size=size,
            duration=round(total, 2),
            error=None,
        )
        # El video original ya no hace falta: libera espacio en GridFS.
        try:
            self.storage.delete_video(job["video_fid"])
            self.jobs.update(job_id, video_fid=None)
        except Exception:
            log.exception("No se pudo borrar el video de %s", job_id)
        log.info("Trabajo %s listo (%d bytes)", job_id, size)

    def _progress_reporter(self, job_id: str) -> Callable[[float], None]:
        """Escribe el progreso en Mongo como mucho cada 0.5 s para no saturarlo."""
        last = {"at": -1.0, "value": -1.0}

        def report(value: float) -> None:
            now = self.clock()
            if value < 100 and (now - last["at"] < 0.5 or value - last["value"] < 1):
                return
            last.update(at=now, value=value)
            self.jobs.update(job_id, progress=round(value, 1))

        return report

    def _fail(self, job: dict, reason: str, message: dict, publish: Publish) -> None:
        log.error("Trabajo %s fallo: %s", job["_id"], reason)
        self.jobs.update(job["_id"], status="failed", error=reason)
        publish(self.config.dead_letter_queue, {**message, "error": reason})
