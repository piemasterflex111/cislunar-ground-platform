"""Environment configuration for the Phase 2 telemetry services.

The defaults are deliberately local teaching values.  Static tokens are not
production-grade credentials; Phase 5 replaces the vendor token with mutual
Transport Layer Security certificate identity.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True, slots=True)
class GroundSettings:
    database_url: str
    redis_url: str
    vendor_token: str
    operator_token: str
    admin_token: str
    max_telemetry_body_bytes: int = 1_024
    processing_maximum_attempts: int = 3

    def __post_init__(self) -> None:
        tokens = (self.vendor_token, self.operator_token, self.admin_token)
        if any(not token for token in tokens):
            raise ValueError("vendor, operator, and administrator tokens must not be empty")
        if len(set(tokens)) != len(tokens):
            raise ValueError("vendor, operator, and administrator tokens must be distinct")
        if not 1 <= self.max_telemetry_body_bytes <= 1_024:
            raise ValueError("MAX_TELEMETRY_BODY_BYTES must be between 1 and 1024")
        if self.processing_maximum_attempts <= 0:
            raise ValueError("WORKER_MAXIMUM_ATTEMPTS must be greater than zero")

    @classmethod
    def from_environment(cls) -> GroundSettings:
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql+asyncpg://ground:ground-local-password@postgres:5432/ground",
            ),
            redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
            vendor_token=os.getenv("VENDOR_TOKEN", "local-phase2-vendor-token"),
            operator_token=os.getenv("OPERATOR_TOKEN", "local-phase2-operator-token"),
            admin_token=os.getenv("ADMIN_TOKEN", "local-phase2-admin-token"),
            max_telemetry_body_bytes=_positive_int("MAX_TELEMETRY_BODY_BYTES", 1_024),
            processing_maximum_attempts=_positive_int("WORKER_MAXIMUM_ATTEMPTS", 3),
        )


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    database_url: str
    redis_url: str
    claim_timeout_seconds: int = 2

    def __post_init__(self) -> None:
        if self.claim_timeout_seconds <= 0:
            raise ValueError("WORKER_CLAIM_TIMEOUT_SECONDS must be greater than zero")

    @classmethod
    def from_environment(cls) -> WorkerSettings:
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql+asyncpg://ground:ground-local-password@postgres:5432/ground",
            ),
            redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
            claim_timeout_seconds=_positive_int("WORKER_CLAIM_TIMEOUT_SECONDS", 2),
        )


@dataclass(frozen=True, slots=True)
class VendorSettings:
    ground_api_url: str
    vendor_token: str
    boot_id_file: str
    auto_send_interval_seconds: float

    def __post_init__(self) -> None:
        if not self.ground_api_url.startswith(("http://", "https://")):
            raise ValueError("GROUND_API_URL must start with http:// or https://")
        if not self.vendor_token:
            raise ValueError("VENDOR_TOKEN must not be empty")
        if not self.boot_id_file:
            raise ValueError("SIM_BOOT_ID_FILE must not be empty")
        if self.auto_send_interval_seconds < 0:
            raise ValueError("SIM_AUTO_SEND_INTERVAL_SECONDS cannot be negative")

    @classmethod
    def from_environment(cls) -> VendorSettings:
        interval = float(os.getenv("SIM_AUTO_SEND_INTERVAL_SECONDS", "0"))
        if interval < 0:
            raise ValueError("SIM_AUTO_SEND_INTERVAL_SECONDS cannot be negative")
        return cls(
            ground_api_url=os.getenv("GROUND_API_URL", "http://ground-api:8080").rstrip("/"),
            vendor_token=os.getenv("VENDOR_TOKEN", "local-phase2-vendor-token"),
            boot_id_file=os.getenv("SIM_BOOT_ID_FILE", "/var/lib/ground/vendor/boot_id"),
            auto_send_interval_seconds=interval,
        )
