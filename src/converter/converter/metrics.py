"""Metricas Prometheus del converter (un proceso por replica: registro normal)."""

from prometheus_client import Counter, Gauge, Histogram

JOBS = Counter(
    "converter_jobs_total",
    "Trabajos procesados por resultado (done, failed, canceled, retry)",
    ["result"],
)
DURATION = Histogram(
    "converter_job_duration_seconds",
    "Duracion de punta a punta de un intento (descarga, conversion y subida)",
    buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1200, 1800),
)
AUDIO_SECONDS = Counter("converter_audio_seconds_total", "Segundos de audio producidos")
IN_PROGRESS = Gauge("converter_jobs_in_progress", "Trabajos convirtiendose ahora")

# Series en 0 desde el arranque para que increase()/rate() vean el primer evento.
for _result in ("done", "failed", "canceled", "retry"):
    JOBS.labels(_result)
