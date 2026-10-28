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


class MongoJobs:
    def __init__(self, client: MongoClient):
        self.col = client.converter.jobs

    def get(self, job_id: str) -> dict | None:
        return self.col.find_one({"_id": job_id})

    def start(self, job_id: str) -> dict:
        """Marca el trabajo en proceso e incrementa el contador de intentos."""
        return self.col.find_one_and_update(
            {"_id": job_id},
            {
                "$set": {"status": "processing", "progress": 0.0, "updated_at": utcnow()},
                "$inc": {"attempts": 1},
            },
            return_document=ReturnDocument.AFTER,
        )

    def update(self, job_id: str, **fields) -> None:
        self.col.update_one({"_id": job_id}, {"$set": {**fields, "updated_at": utcnow()}})
