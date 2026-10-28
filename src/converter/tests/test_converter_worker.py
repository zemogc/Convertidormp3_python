import json

import pytest

from converter.config import Config
from converter.ffmpeg import PermanentError
from converter.worker import Worker

CONFIG = Config(max_attempts=3)


class FakeStorage:
    def __init__(self):
        self.videos = {"vid1": b"video-bytes"}
        self.outputs = {}

    def download_video(self, fid, path):
        with open(path, "wb") as fh:
            fh.write(self.videos[fid])

    def put_output(self, path, filename, metadata):
        with open(path, "rb") as fh:
            self.outputs["out1"] = (fh.read(), filename, metadata)
        return "out1"

    def delete_video(self, fid):
        del self.videos[fid]

    def delete_output(self, fid):
        del self.outputs[fid]


class FakeJobs:
    def __init__(self, **job):
        self.doc = {
            "_id": "job1",
            "owner": "ana@example.com",
            "status": "queued",
            "attempts": 0,
            "video_fid": "vid1",
            "output_name": "clip.mp3",
            "options": {"format": "mp3", "bitrate": 192, "start": None, "end": None},
            **job,
        }
        self.progress = []
        self.cancel_after = None  # cancelar cuando se hayan reportado N progresos
        self.cancel_checks = 0
        self.cancel_on_finish = False

    def get(self, job_id):
        return dict(self.doc) if job_id == self.doc["_id"] else None

    def start(self, job_id):
        if self.doc["status"] not in ("queued", "processing"):
            return None
        self.doc.update(status="processing", progress=0.0, attempts=self.doc["attempts"] + 1)
        return dict(self.doc)

    def update(self, job_id, **fields):
        self.doc.update(fields)

    def finish(self, job_id, **fields):
        if self.cancel_on_finish:
            self.doc["cancel_requested"] = True
        if self.doc.get("cancel_requested"):
            return False
        self.doc.update(fields)
        return True

    def _maybe_cancel(self):
        if self.cancel_after is not None and len(self.progress) >= self.cancel_after:
            self.doc["cancel_requested"] = True
        return bool(self.doc.get("cancel_requested"))

    def report_progress(self, job_id, progress):
        self.progress.append(progress)
        self.doc["progress"] = progress
        return self._maybe_cancel()

    def cancel_requested(self, job_id):
        self.cancel_checks += 1
        return self._maybe_cancel()


class FakeFfmpeg:
    def __init__(self, fail=None, duration=10.0, input_duration=None):
        self.fail = fail
        self.duration = duration
        self.input_duration = duration if input_duration is None else input_duration
        self.steps = (10.0, 10.5, 50.0, 100.0)

    def probe(self, path):
        if isinstance(self.fail, PermanentError):
            raise self.fail
        if path.endswith("input"):
            return self.input_duration
        return self.duration

    def waveform(self, path, duration):
        if self.fail == "waveform":
            raise RuntimeError("sin forma de onda")
        return [0.5, 1.0]

    def convert(self, src, dst, options, total, on_progress):
        if isinstance(self.fail, Exception):
            raise self.fail
        for pct in self.steps:
            on_progress(pct)
        with open(dst, "wb") as fh:
            fh.write(b"mp3-data")


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 1.0
        return self.t


def run(jobs, ffmpeg, storage=None, body=None):
    storage = storage or FakeStorage()
    published = []
    worker = Worker(storage, jobs, CONFIG, runner=ffmpeg, clock=Clock())
    message = body if body is not None else json.dumps({"job_id": "job1", "video_fid": "vid1"})
    worker.handle(
        message.encode() if isinstance(message, str) else message,
        lambda q, m: published.append((q, m)),
    )
    return storage, published


def test_successful_conversion():
    jobs = FakeJobs()
    storage, published = run(jobs, FakeFfmpeg())

    assert jobs.doc["status"] == "done"
    assert jobs.doc["output_fid"] == "out1"
    assert jobs.doc["output_size"] == len(b"mp3-data")
    assert jobs.doc["duration"] == 10.0
    assert jobs.doc["waveform"] == [0.5, 1.0]
    assert jobs.doc["video_fid"] is None
    assert storage.videos == {}  # el video original se borra
    assert storage.outputs["out1"][1] == "clip.mp3"
    assert published == [
        ("mp3", {"job_id": "job1", "username": "ana@example.com", "output_name": "clip.mp3"})
    ]


def test_progress_is_throttled():
    jobs = FakeJobs()
    run(jobs, FakeFfmpeg())
    # 10.5 se descarta (menos de 1 punto de diferencia); 100 siempre se escribe
    assert jobs.progress == [10.0, 50.0, 100.0]


def test_transient_failure_is_retried():
    jobs = FakeJobs()
    _, published = run(jobs, FakeFfmpeg(fail=RuntimeError("mongo caido")))

    assert jobs.doc["status"] == "queued"
    assert "reintentando (1/3)" in jobs.doc["error"]
    assert published == [("video.retry", {"job_id": "job1", "video_fid": "vid1"})]


def test_gives_up_after_max_attempts():
    jobs = FakeJobs(attempts=2)
    _, published = run(jobs, FakeFfmpeg(fail=RuntimeError("boom")))

    assert jobs.doc["status"] == "failed"
    assert "fallo tras 3 intentos" in jobs.doc["error"]
    [(queue, message)] = published
    assert queue == "video.dlq"
    assert message["job_id"] == "job1"


def test_permanent_error_is_not_retried():
    jobs = FakeJobs()
    _, published = run(jobs, FakeFfmpeg(fail=PermanentError("el archivo no tiene pista de audio")))

    assert jobs.doc["status"] == "failed"
    assert jobs.doc["error"] == "el archivo no tiene pista de audio"
    assert [q for q, _ in published] == ["video.dlq"]


def test_duplicate_delivery_of_finished_job_is_ignored():
    jobs = FakeJobs(status="done")
    _, published = run(jobs, FakeFfmpeg(fail=AssertionError("no deberia convertir")))
    assert published == []
    assert jobs.doc["attempts"] == 0


def test_unknown_job_is_dropped():
    jobs = FakeJobs()
    _, published = run(jobs, FakeFfmpeg(), body=json.dumps({"job_id": "otro"}))
    assert published == []


@pytest.mark.parametrize("body", ["no-es-json", json.dumps({"sin": "job_id"}), "[]"])
def test_malformed_message_goes_to_dlq(body):
    _, published = run(FakeJobs(), FakeFfmpeg(), body=body)
    assert [q for q, _ in published] == ["video.dlq"]


def test_waveform_failure_does_not_fail_the_job():
    jobs = FakeJobs()
    _, published = run(jobs, FakeFfmpeg(fail="waveform"))
    assert jobs.doc["status"] == "done"
    assert jobs.doc["waveform"] is None
    assert [q for q, _ in published] == ["mp3"]


def test_duration_is_measured_on_output_when_input_has_none():
    # .webm grabado en streaming: ffprobe no da duracion para la entrada.
    jobs = FakeJobs()
    run(jobs, FakeFfmpeg(duration=95.0, input_duration=0.0))
    assert jobs.doc["duration"] == 95.0


def test_job_canceled_while_queued_is_skipped():
    jobs = FakeJobs(status="canceled")
    storage, published = run(jobs, FakeFfmpeg(fail=AssertionError("no deberia convertir")))
    assert published == []
    assert jobs.doc["attempts"] == 0


def test_cancel_during_conversion():
    jobs = FakeJobs()
    jobs.cancel_after = 1  # el usuario cancela tras el primer progreso
    storage, published = run(jobs, FakeFfmpeg())

    assert jobs.doc["status"] == "canceled"
    assert jobs.doc["cancel_requested"] is False
    assert jobs.progress == [10.0]  # se detuvo en el primer reporte
    assert storage.videos == {} and storage.outputs == {}
    assert published == []  # ni reintento, ni DLQ, ni email


def test_cancel_is_detected_without_known_duration():
    # Sin duracion el progreso llega como None: igual se revisa el flag.
    jobs = FakeJobs()
    jobs.doc["cancel_requested"] = True
    ff = FakeFfmpeg()
    ff.steps = (None, None, None)
    run(jobs, ff)
    assert jobs.cancel_checks == 1
    assert jobs.doc["status"] == "canceled"


def test_cancel_right_before_finishing_discards_the_output():
    # La cancelacion llega despues de ffmpeg (p. ej. subiendo el audio a GridFS).
    jobs = FakeJobs()
    jobs.cancel_on_finish = True
    storage, published = run(jobs, FakeFfmpeg())
    assert jobs.doc["status"] == "canceled"
    assert storage.outputs == {}  # el audio subido se borra
    assert published == []
