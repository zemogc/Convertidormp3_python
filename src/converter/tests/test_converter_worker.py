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

    def get(self, job_id):
        return dict(self.doc) if job_id == self.doc["_id"] else None

    def start(self, job_id):
        self.doc.update(status="processing", progress=0.0, attempts=self.doc["attempts"] + 1)
        return dict(self.doc)

    def update(self, job_id, **fields):
        if set(fields) == {"progress"}:
            self.progress.append(fields["progress"])
        self.doc.update(fields)


class FakeFfmpeg:
    def __init__(self, fail=None, duration=10.0):
        self.fail = fail
        self.duration = duration

    def probe(self, path):
        if isinstance(self.fail, PermanentError):
            raise self.fail
        return self.duration

    def convert(self, src, dst, options, total, on_progress):
        if self.fail:
            raise self.fail
        for pct in (10.0, 10.5, 50.0, 100.0):
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
