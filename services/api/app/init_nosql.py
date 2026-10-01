"""Prepare MongoDB indexes and verify the Redis cache dependency."""

from pymongo import ASCENDING, DESCENDING, MongoClient
from redis import Redis

from app.config import settings


def main() -> None:
    mongo_uri = (
        f"mongodb://{settings.mongo_user}:{settings.mongo_password}"
        f"@{settings.mongo_host}:{settings.mongo_port}/?authSource=admin"
    )
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10_000)
    client.admin.command("ping")
    telemetry = client["evfleet"]["telemetry"]
    telemetry.create_index([("event_id", ASCENDING)], unique=True, name="uq_telemetry_event_id")
    telemetry.create_index([("vehicle_id", ASCENDING), ("timestamp", DESCENDING)], name="ix_vehicle_timestamp")
    telemetry.create_index([("timestamp", ASCENDING)], name="ix_telemetry_timestamp")
    telemetry.create_index([("ingested_at", ASCENDING)], expireAfterSeconds=7_776_000, name="ttl_telemetry_90d")
    client.close()

    Redis(host=settings.redis_host, port=settings.redis_port, socket_connect_timeout=5).ping()
    print("MongoDB indexes ready; Redis connection verified.")


if __name__ == "__main__":
    main()
