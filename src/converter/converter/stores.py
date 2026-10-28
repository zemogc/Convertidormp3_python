from datetime import UTC, datetime

import gridfs
from bson import ObjectId
from pymongo import MongoClient, ReturnDocument

CHUNK = 256 * 1024


def utcnow() -> datetime:
    return datetime.now(UTC)


class GridStorage:
    def __init__(self, client: MongoClient):
        self.videos = gridfs.GridFS(client.videos)
        self.outputs = gridfs.GridFS(client.mp3s)

    def download_video(self, fid: str, path: str) -> None:
        source = self.videos.get(ObjectId(fid))
        with open(path, "wb") as fh:
            while data := source.read(CHUNK):
                fh.write(data)

    def put_output(self, path: str, filename: str, metadata: dict) -> str:
        with open(path, "rb") as fh:
            return str(self.outputs.put(fh, filename=filename, metadata=metadata))

    def delete_video(self, fid: str) -> None:
        self.videos.delete(ObjectId(fid))

    def delete_output(self, fid: str) -> None:
        self.outputs.delete(ObjectId(fid))


class MongoJobs:
    def __init__(self, client: MongoClient):
        self.col = client.converter.jobs

    def get(self, job_id: str) -> dict | None:
        return self.col.find_one({"_id": job_id})

    def start(self, job_id: str) -> dict:
        """Marca el trabajo en proceso e incrementa el contador de intentos.

        Devuelve None si mientras tanto se cancelo o termino: el filtro por estado
        hace la transicion atomica frente a una cancelacion desde el gateway.
        """
        return self.col.find_one_and_update(
            {"_id": job_id, "status": {"$in": ["queued", "processing"]}},
            {
                "$set": {"status": "processing", "progress": 0.0, "updated_at": utcnow()},
                "$inc": {"attempts": 1},
            },
            return_document=ReturnDocument.AFTER,
        )

    def update(self, job_id: str, **fields) -> None:
        self.col.update_one({"_id": job_id}, {"$set": {**fields, "updated_at": utcnow()}})

    def finish(self, job_id: str, **fields) -> bool:
        """Marca el trabajo como terminado salvo que se haya pedido cancelarlo."""
        result = self.col.update_one(
            {"_id": job_id, "cancel_requested": {"$ne": True}},
            {"$set": {**fields, "updated_at": utcnow()}},
        )
        return result.matched_count == 1

    def report_progress(self, job_id: str, progress: float) -> bool:
        """Guarda el progreso y devuelve True si el usuario pidio cancelar."""
        doc = self.col.find_one_and_update(
            {"_id": job_id},
            {"$set": {"progress": progress, "updated_at": utcnow()}},
            projection={"cancel_requested": 1},
        )
        return bool(doc and doc.get("cancel_requested"))

    def cancel_requested(self, job_id: str) -> bool:
        doc = self.col.find_one({"_id": job_id}, {"cancel_requested": 1})
        return bool(doc and doc.get("cancel_requested"))
