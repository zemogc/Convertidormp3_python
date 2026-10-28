import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    rabbitmq_host: str = "localhost"
    mp3_queue: str = "mp3"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = False
    mail_from: str = "Convertidor <no-reply@convertidor.local>"
    public_url: str = "http://localhost:8080"
    max_attempts: int = 5

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        return cls(
            rabbitmq_host=env("RABBITMQ_HOST", cls.rabbitmq_host),
            mp3_queue=env("MP3_QUEUE", cls.mp3_queue),
            smtp_host=env("SMTP_HOST", cls.smtp_host),
            smtp_port=int(env("SMTP_PORT", cls.smtp_port)),
            smtp_user=env("SMTP_USER") or None,
            smtp_password=env("SMTP_PASSWORD") or None,
            smtp_starttls=env("SMTP_STARTTLS", "false").lower() in {"1", "true", "yes"},
            mail_from=env("MAIL_FROM", cls.mail_from),
            public_url=env("PUBLIC_URL", cls.public_url).rstrip("/"),
            max_attempts=int(env("MAX_ATTEMPTS", cls.max_attempts)),
        )
