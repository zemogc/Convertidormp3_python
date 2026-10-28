import io
import itertools

import pytest

from gateway.app import create_app
from gateway.config import Config
from gateway.publisher import QueueUnavailable
from gateway.stores import utcnow

TOKENS = {"token-ana": "ana@example.com", "token-beto": "beto@example.com"}


class FakeAuth:
    def __init__(self):
        self.users = {}

    def register(self, email, password):
        if email in self.users:
            return 409, {"error": "el usuario ya existe"}
        self.users[email] = password
        return 201, {"email": email}

    def login(self, email, password):
        if self.users.get(email) == password:
            return 200, {"token": f"token-{email.split('@')[0]}", "expires_in": 60}
        return 401, {"error": "credenciales invalidas"}

    def validate(self, token):
        email = TOKENS.get(token)
        return {"email": email, "admin": False} if email else None


class FakeFile(io.BytesIO):
    @property
    def length(self):
        return len(self.getvalue())


class FakeStorage:
    def __init__(self):
        self.videos, self.outputs = {}, {}
        self._ids = itertools.count(1)

    def put_video(self, stream, filename, content_type):
        fid = f"v{next(self._ids)}"
        self.videos[fid] = stream.read()
        return fid

    def delete_video(self, fid):
        self.videos.pop(fid, None)

    def delete_output(self, fid):
        del self.outputs[fid]

    def open_output(self, fid):
        data = self.outputs.get(fid)
        return FakeFile(data) if data is not None else None


class FakeJobs:
    def __init__(self):
        self.docs = {}

    def create(self, job):
        self.docs[job["_id"]] = dict(job)

    def get(self, job_id):
        job = self.docs.get(job_id)
        return dict(job) if job else None

    def list_for_owner(self, owner, limit=50):
        mine = [dict(j) for j in self.docs.values() if j["owner"] == owner]
        return sorted(mine, key=lambda j: j["created_at"], reverse=True)[:limit]

    def find_cached(self, cache_key):
        for job in self.docs.values():
            if job["cache_key"] == cache_key and job["status"] == "done" and job["output_fid"]:
                return dict(job)
        return None

    def update(self, job_id, **fields):
        self.docs[job_id].update(fields, updated_at=utcnow())

    def cancel(self, job_id):
        job = self.docs[job_id]
        if job["status"] == "queued":
            job.update(status="canceled", progress=0.0, error=None)
            return "canceled"
        if job["status"] == "processing":
            job["cancel_requested"] = True
            return "canceling"
        return None

    def delete(self, job_id):
        del self.docs[job_id]

    def output_in_use(self, output_fid, excluding):
        return any(
            j["output_fid"] == output_fid and j["_id"] != excluding for j in self.docs.values()
        )


class FakePublisher:
    def __init__(self):
        self.messages = []
        self.down = False

    def publish(self, queue, message):
        if self.down:
            raise QueueUnavailable(queue)
        self.messages.append((queue, message))


@pytest.fixture
def deps():
    return {
        "auth": FakeAuth(),
        "storage": FakeStorage(),
        "jobs": FakeJobs(),
        "publisher": FakePublisher(),
    }


@pytest.fixture
def app(deps):
    config = Config(max_upload_mb=1, sse_poll_seconds=0.01, sse_max_seconds=2)
    return create_app(config, **deps)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def ana():
    return {"Authorization": "Bearer token-ana"}


@pytest.fixture
def beto():
    return {"Authorization": "Bearer token-beto"}


@pytest.fixture
def upload(client):
    def _upload(headers, data=b"fake-video-bytes", filename="clip.mp4", **form):
        return client.post(
            "/api/jobs",
            headers=headers,
            data={"file": (io.BytesIO(data), filename), **form},
            content_type="multipart/form-data",
        )

    return _upload
