import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    auth_svc_address: str = "auth:5000"
    mongo_uri: str = "mongodb://localhost:27017"
    rabbitmq_host: str = "localhost"
    video_queue: str = "video"
    max_upload_mb: int = 500
    sse_poll_seconds: float = 0.5
    sse_max_seconds: int = 3600

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        return cls(
            auth_svc_address=env("AUTH_SVC_ADDRESS", cls.auth_svc_address),
            mongo_uri=env("MONGO_URI", cls.mongo_uri),
            rabbitmq_host=env("RABBITMQ_HOST", cls.rabbitmq_host),
            video_queue=env("VIDEO_QUEUE", cls.video_queue),
            max_upload_mb=int(env("MAX_UPLOAD_MB", cls.max_upload_mb)),
        )
