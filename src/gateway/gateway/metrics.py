"""Metricas Prometheus del gateway.

gunicorn corre varios workers (procesos): con PROMETHEUS_MULTIPROC_DIR cada uno
escribe sus valores en archivos y /metrics los suma. Sin la variable (tests,
desarrollo) se usa el registro normal en memoria.
"""

import os

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    multiprocess,
)

REQUESTS = Counter(
    "gateway_http_requests_total", "Peticiones HTTP", ["method", "endpoint", "status"]
)
LATENCY = Histogram(
    "gateway_http_request_duration_seconds",
    "Tiempo hasta empezar a responder (las subidas incluyen recibir el archivo)",
    ["endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
JOBS_CREATED = Counter("gateway_jobs_created_total", "Trabajos creados", ["format", "cached"])
JOBS_REJECTED = Counter("gateway_jobs_rejected_total", "Trabajos rechazados", ["reason"])
UPLOAD_BYTES = Counter("gateway_upload_bytes_total", "Bytes de video recibidos")


def init_labels(formats) -> None:
    """Crea las series en 0: sin esto increase() no ve el primer incremento."""
    for fmt in formats:
        for cached in ("true", "false"):
            JOBS_CREATED.labels(fmt, cached)
    for reason in ("active_limit", "queue_unavailable"):
        JOBS_REJECTED.labels(reason)


def render() -> tuple[bytes, str]:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        registry = REGISTRY
    return generate_latest(registry), CONTENT_TYPE_LATEST
