"""Persistencia del gateway: archivos en GridFS y estado de trabajos en MongoDB."""

from datetime import UTC, datetime

import gridfs
from bson import ObjectId
from bson.errors import InvalidId
from pymongo import DESCENDING, MongoClient

CHUNK = 256 * 1024


def utcnow() -> datetime:
    return datetime.now(UTC)


class GridStorage:
    def __init__(self, client: MongoClient):
        # Mismas bases que el proyecto original: videos de entrada y audios de salida.
        self.videos = gridfs.GridFS(client.videos)
        self.outputs = gridfs.GridFS(client.mp3s)

    def put_video(self, stream, filename: str, content_type: str | None) -> str:
        return str(self.videos.put(stream, filename=filename, content_type=content_type))

    def delete_video(self, fid: str) -> None:
        self.videos.delete(ObjectId(fid))

    def delete_output(self, fid: str) -> None:
        self.outputs.delete(ObjectId(fid))

    def open_output(self, fid: str):
        """Devuelve un archivo con .read(n), .length y .filename, o None."""
        try:
            return self.outputs.get(ObjectId(fid))
        except (gridfs.NoFile, InvalidId):
            return None


class MongoJobs:
    def __init__(self, client: MongoClient):
        self.col = client.converter.jobs
        self.col.create_index([("owner", 1), ("created_at", DESCENDING)])
        self.col.create_index("cache_key")

    def create(self, job: dict) -> None:
        self.col.insert_one(job)

    def get(self, job_id: str) -> dict | None:
        return self.col.find_one({"_id": job_id})

    def list_for_owner(self, owner: str, limit: int = 50) -> list[dict]:
        cursor = self.col.find({"owner": owner}).sort("created_at", DESCENDING).limit(limit)
        return list(cursor)

    def find_cached(self, cache_key: str) -> dict | None:
        return self.col.find_one(
            {"cache_key": cache_key, "status": "done", "output_fid": {"$ne": None}},
            sort=[("updated_at", DESCENDING)],
        )

    def update(self, job_id: str, **fields) -> None:
        self.col.update_one({"_id": job_id}, {"$set": {**fields, "updated_at": utcnow()}})

    def cancel(self, job_id: str) -> str | None:
        """Cancela un trabajo activo. Devuelve 'canceled', 'canceling' o None.

        En cola: pasa a 'canceled' de inmediato (el filtro por estado gana o pierde
        la carrera contra el converter de forma atomica). En proceso: se marca
        cancel_requested y el converter detiene ffmpeg en su siguiente reporte.
        """
        now = utcnow()
        queued = self.col.update_one(
            {"_id": job_id, "status": "queued"},
            {"$set": {"status": "canceled", "progress": 0.0, "error": None, "updated_at": now}},
        )
        if queued.modified_count:
            return "canceled"
        processing = self.col.update_one(
            {"_id": job_id, "status": "processing"},
            {"$set": {"cancel_requested": True, "updated_at": now}},
        )
        return "canceling" if processing.matched_count else None

    def count_active(self, owner: str) -> int:
        return self.col.count_documents(
            {"owner": owner, "status": {"$in": ["queued", "processing"]}}
        )

    def delete(self, job_id: str) -> None:
        self.col.delete_one({"_id": job_id})

    def output_in_use(self, output_fid: str, excluding: str) -> bool:
        """True si otro trabajo (p. ej. un resultado de cache) usa el mismo archivo."""
        return (
            self.col.count_documents({"output_fid": output_fid, "_id": {"$ne": excluding}}, limit=1)
            > 0
        )
