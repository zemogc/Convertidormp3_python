import json
import logging
import time

import pika

from . import mailer
from .config import Config

log = logging.getLogger("notification")


def handle(config: Config, body: bytes, headers: dict | None, send=mailer.send) -> str:
    """Envia el email. Devuelve 'ack', 'retry' o 'drop'."""
    try:
        message = json.loads(body)
        msg = mailer.build_message(
            config, message["username"], message["job_id"], message["output_name"]
        )
    except (ValueError, KeyError, TypeError):
        log.error("Mensaje invalido descartado: %r", body[:200])
        return "drop"
    try:
        send(config, msg)
    except Exception as err:
        attempt = int((headers or {}).get("x-attempt", 1))
        if attempt >= config.max_attempts:
            log.error("Email para %s descartado tras %d intentos: %s", msg["To"], attempt, err)
            return "drop"
        log.warning("Fallo el envio a %s (intento %d): %s", msg["To"], attempt, err)
        return "retry"
    log.info("Email enviado a %s (%s)", msg["To"], message["job_id"])
    return "ack"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = Config.from_env()
    params = pika.ConnectionParameters(
        host=config.rabbitmq_host, connection_attempts=30, retry_delay=2
    )
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.queue_declare(queue=config.mp3_queue, durable=True)
    channel.basic_qos(prefetch_count=10)

    def on_message(ch, method, properties, body):
        headers = properties.headers or {}
        outcome = handle(config, body, headers)
        if outcome == "retry":
            time.sleep(2)  # backoff corto: el envio de email es rapido y no bloquea heartbeats
            ch.basic_publish(
                "",
                config.mp3_queue,
                body,
                pika.BasicProperties(
                    delivery_mode=pika.DeliveryMode.Persistent,
                    content_type="application/json",
                    headers={**headers, "x-attempt": int(headers.get("x-attempt", 1)) + 1},
                ),
            )
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(queue=config.mp3_queue, on_message_callback=on_message)
    log.info("Esperando notificaciones en la cola '%s'", config.mp3_queue)
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        channel.stop_consuming()
    connection.close()


if __name__ == "__main__":
    main()
