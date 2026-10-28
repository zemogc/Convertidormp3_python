import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    mongo_uri: str = "mongodb://localhost:27017"
    rabbitmq_host: str = "localhost"
    video_queue: str = "video"
    mp3_queue: str = "mp3"
    max_attempts: int = 3
    retry_delay_ms: int = 5000

    @property
    def retry_queue(self) -> str:
        return f"{self.video_queue}.retry"

    @property
    def dead_letter_queue(self) -> str:
        return f"{self.video_queue}.dlq"

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        return cls(
            mongo_uri=env("MONGO_URI", cls.mongo_uri),
            rabbitmq_host=env("RABBITMQ_HOST", cls.rabbitmq_host),
            video_queue=env("VIDEO_QUEUE", cls.video_queue),
            mp3_queue=env("MP3_QUEUE", cls.mp3_queue),
            max_attempts=int(env("MAX_ATTEMPTS", cls.max_attempts)),
            retry_delay_ms=int(env("RETRY_DELAY_MS", cls.retry_delay_ms)),
        )
