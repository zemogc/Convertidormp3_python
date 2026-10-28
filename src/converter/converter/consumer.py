import functools
import json
import logging
import threading

import pika
from pymongo import MongoClient

from .config import Config
from .stores import GridStorage, MongoJobs
from .worker import Worker

log = logging.getLogger("converter")


def declare_queues(channel, config: Config) -> None:
    channel.queue_declare(queue=config.video_queue, durable=True)
    channel.queue_declare(queue=config.mp3_queue, durable=True)
    channel.queue_declare(queue=config.dead_letter_queue, durable=True)
    # Cola de espera: el mensaje expira tras retry_delay_ms y RabbitMQ lo
    # devuelve a la cola principal (backoff sin bloquear al worker).
    channel.queue_declare(
        queue=config.retry_queue,
        durable=True,
        arguments={
            "x-message-ttl": config.retry_delay_ms,
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": config.video_queue,
        },
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = Config.from_env()
    mongo = MongoClient(config.mongo_uri, tz_aware=True)
    worker = Worker(GridStorage(mongo), MongoJobs(mongo), config)

    params = pika.ConnectionParameters(
        host=config.rabbitmq_host, heartbeat=60, connection_attempts=30, retry_delay=2
    )
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    declare_queues(channel, config)
    channel.basic_qos(prefetch_count=1)

    persistent = pika.BasicProperties(
        delivery_mode=pika.DeliveryMode.Persistent, content_type="application/json"
    )

    def threadsafe(fn, *args):
        connection.add_callback_threadsafe(functools.partial(fn, *args))

    def publish(queue: str, message: dict) -> None:
        threadsafe(channel.basic_publish, "", queue, json.dumps(message), persistent)

    def work(delivery_tag: int, body: bytes) -> None:
        try:
            worker.handle(body, publish)
        finally:
            threadsafe(channel.basic_ack, delivery_tag)

    def on_message(ch, method, properties, body):
        # La conversion corre en otro hilo para que el hilo de pika siga
        # respondiendo heartbeats; si no, RabbitMQ corta la conexion en videos largos.
        threading.Thread(target=work, args=(method.delivery_tag, body), daemon=True).start()

    channel.basic_consume(queue=config.video_queue, on_message_callback=on_message)
    log.info("Esperando videos en la cola '%s'", config.video_queue)
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        channel.stop_consuming()
    connection.close()


if __name__ == "__main__":
    main()
