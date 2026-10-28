import json
import logging
import threading

import pika
from pika.exceptions import AMQPError

log = logging.getLogger(__name__)


class QueueUnavailable(Exception):
    pass


class Publisher:
    """Publica mensajes persistentes en RabbitMQ.

    La conexion se abre bajo demanda y se recrea si el broker la cerro
    (por ejemplo por heartbeat en un worker inactivo). pika no es thread-safe,
    asi que las publicaciones se serializan con un lock.
    """

    def __init__(self, host: str):
        self.params = pika.ConnectionParameters(host=host, connection_attempts=3, retry_delay=1)
        self._conn = None
        self._channel = None
        self._declared: set[str] = set()
        self._lock = threading.Lock()

    def _channel_for(self, queue: str):
        if self._conn is None or self._conn.is_closed:
            self._conn = pika.BlockingConnection(self.params)
            self._channel = self._conn.channel()
            self._channel.confirm_delivery()
            self._declared.clear()
        if queue not in self._declared:
            self._channel.queue_declare(queue=queue, durable=True)
            self._declared.add(queue)
        return self._channel

    def publish(self, queue: str, message: dict) -> None:
        body = json.dumps(message)
        props = pika.BasicProperties(
            delivery_mode=pika.DeliveryMode.Persistent, content_type="application/json"
        )
        with self._lock:
            for attempt in (1, 2):
                try:
                    self._channel_for(queue).basic_publish("", queue, body, props)
                    return
                except AMQPError as err:
                    log.warning("Fallo publicando en %s (intento %d): %r", queue, attempt, err)
                    self._conn = None
            raise QueueUnavailable(queue)
