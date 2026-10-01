"""Environment-based application configuration."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    cors_allowed_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv(
            "CORS_ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    )
    database_url: str = os.environ["DATABASE_URL"]
    mongo_host: str = os.getenv("MONGO_HOST", "localhost")
    mongo_port: int = int(os.getenv("MONGO_PORT", "27017"))
    mongo_user: str = os.getenv("MONGO_INITDB_ROOT_USERNAME", "evfleet")
    mongo_password: str = os.environ["MONGO_INITDB_ROOT_PASSWORD"]
    redis_host: str = os.getenv("REDIS_HOST", "localhost")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    seed_vehicle_count: int = int(os.getenv("SEED_VEHICLE_COUNT", "100000"))
    simulator_seed: int = int(os.getenv("SIMULATOR_SEED", "20260930"))
    # Only the API signs tokens. Other services reuse this settings module for
    # storage configuration and must not receive the signing secret.
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    demo_operator_email: str = os.getenv("DEMO_OPERATOR_EMAIL", "operator@demo.local")
    # The seed process consumes this in the API container; telemetry workers
    # share storage settings without needing the demo account credential.
    demo_operator_password: str = os.getenv("DEMO_OPERATOR_PASSWORD", "")


settings = Settings()
