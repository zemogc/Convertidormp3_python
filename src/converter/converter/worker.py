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
        if job["status"] in ("done", "canceled"):
            log.info("Trabajo %s ya esta en estado %s, se descarta", job_id, job["status"])
            return

        job = self.jobs.start(job_id)
        if job is None:
            log.info("Trabajo %s cancelado antes de empezar", job_id)
            return
        attempt = job["attempts"]
        log.info("Trabajo %s: intento %d/%d", job_id, attempt, self.config.max_attempts)
        try:
            self._convert(job)
        except ffmpeg.Canceled:
            self._canceled(job)
            return
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
            self._check_canceled(job_id)

            total = ffmpeg.clip_duration(self.ffmpeg.probe(src), options)
            self.ffmpeg.convert(src, dst, options, total, self._progress_reporter(job_id))
            self._check_canceled(job_id)

            size = os.path.getsize(dst)
            duration = self._output_duration(dst, total)
            peaks = self._waveform(dst, duration, job_id)
            self._check_canceled(job_id)
            output_fid = self.storage.put_output(
                dst, job["output_name"], {"job_id": job_id, "owner": job["owner"]}
            )

        # Cierre atomico: solo pasa a 'done' si nadie pidio cancelar mientras tanto.
        finished = self.jobs.finish(
            job_id,
            status="done",
            progress=100.0,
            output_fid=output_fid,
            output_size=size,
            duration=round(duration, 2),
            waveform=peaks,
            error=None,
        )
        if not finished:
            self.storage.delete_output(output_fid)
            raise ffmpeg.Canceled()
        # El video original ya no hace falta: libera espacio en GridFS.
        try:
            self.storage.delete_video(job["video_fid"])
            self.jobs.update(job_id, video_fid=None)
        except Exception:
            log.exception("No se pudo borrar el video de %s", job_id)
        log.info("Trabajo %s listo (%d bytes)", job_id, size)

    def _check_canceled(self, job_id: str) -> None:
        if self.jobs.cancel_requested(job_id):
            raise ffmpeg.Canceled()

    def _output_duration(self, path: str, fallback: float) -> float:
        # Videos grabados en streaming (p. ej. .webm del navegador) no traen la
        # duracion en la cabecera; el archivo de salida si, asi que se mide ahi.
        try:
            return self.ffmpeg.probe(path) or fallback
        except Exception:
            return fallback

    def _waveform(self, path: str, total: float, job_id: str) -> list[float] | None:
        # Es un extra visual: si falla, el trabajo igual termina bien.
        try:
            return self.ffmpeg.waveform(path, total)
        except Exception:
            log.exception("No se pudo calcular la forma de onda de %s", job_id)
            return None

    def _progress_reporter(self, job_id: str) -> Callable[[float | None], None]:
        """Escribe el progreso en Mongo como mucho cada 0.5 s y revisa si se cancelo.

        Cada escritura devuelve de paso el flag de cancelacion; si el progreso no
        avanza (o no se conoce la duracion) se consulta el flag una vez por segundo.
        Lanzar Canceled aqui hace que ffmpeg.convert mate el proceso.
        """
        last = {"at": -1.0, "value": -1.0, "checked": -1.0}

        def report(value: float | None) -> None:
            now = self.clock()
            if value is not None and (
                value >= 100 or (now - last["at"] >= 0.5 and value - last["value"] >= 1)
            ):
                last.update(at=now, value=value, checked=now)
                canceled = self.jobs.report_progress(job_id, round(value, 1))
            elif now - last["checked"] >= 1.0:
                last["checked"] = now
                canceled = self.jobs.cancel_requested(job_id)
            else:
                return
            if canceled:
                raise ffmpeg.Canceled()

        return report

    def _canceled(self, job: dict) -> None:
        log.info("Trabajo %s cancelado durante la conversion", job["_id"])
        self.jobs.update(
            job["_id"], status="canceled", progress=0.0, cancel_requested=False, error=None
        )
        try:
            self.storage.delete_video(job["video_fid"])
            self.jobs.update(job["_id"], video_fid=None)
        except Exception:
            log.exception("No se pudo borrar el video de %s", job["_id"])

    def _fail(self, job: dict, reason: str, message: dict, publish: Publish) -> None:
        log.error("Trabajo %s fallo: %s", job["_id"], reason)
        self.jobs.update(job["_id"], status="failed", error=reason)
        publish(self.config.dead_letter_queue, {**message, "error": reason})
