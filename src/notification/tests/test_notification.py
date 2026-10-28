import json
import smtplib

from notification.config import Config
from notification.consumer import handle
from notification.mailer import build_message

CONFIG = Config(public_url="https://convertidor.example", max_attempts=3)
BODY = json.dumps(
    {"job_id": "abc123", "username": "ana@example.com", "output_name": "<mi> cancion.mp3"}
).encode()


def test_build_message():
    msg = build_message(CONFIG, "ana@example.com", "abc123", "<mi> cancion.mp3")
    assert msg["To"] == "ana@example.com"
    assert msg["Subject"] == "Tu audio esta listo: <mi> cancion.mp3"
    text = msg.get_body(("plain",)).get_content()
    html = msg.get_body(("html",)).get_content()
    assert "https://convertidor.example/#job-abc123" in text
    assert "&lt;mi&gt; cancion.mp3" in html  # el nombre se escapa en HTML
    assert "<mi>" not in html


def test_handle_sends_email():
    sent = []
    assert handle(CONFIG, BODY, None, send=lambda c, m: sent.append(m)) == "ack"
    assert sent[0]["To"] == "ana@example.com"


def failing_send(config, msg):
    raise smtplib.SMTPServerDisconnected("caido")


def test_handle_retries_then_drops():
    assert handle(CONFIG, BODY, {}, send=failing_send) == "retry"
    assert handle(CONFIG, BODY, {"x-attempt": 2}, send=failing_send) == "retry"
    assert handle(CONFIG, BODY, {"x-attempt": 3}, send=failing_send) == "drop"


def test_handle_drops_malformed_messages():
    assert handle(CONFIG, b"basura", None, send=failing_send) == "drop"
    assert handle(CONFIG, b'{"job_id": "x"}', None, send=failing_send) == "drop"
