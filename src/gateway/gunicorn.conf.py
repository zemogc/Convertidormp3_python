"""Hooks de gunicorn para las metricas Prometheus en modo multiproceso."""

import os
import shutil

from prometheus_client import multiprocess

_DIR = os.environ.get("PROMETHEUS_MULTIPROC_DIR")


def on_starting(server):
    # Al reiniciar el contenedor el directorio sobrevive: se limpia para no
    # sumar contadores de procesos que ya no existen.
    if _DIR:
        shutil.rmtree(_DIR, ignore_errors=True)
        os.makedirs(_DIR, exist_ok=True)


def child_exit(server, worker):
    if _DIR:
        multiprocess.mark_process_dead(worker.pid)
