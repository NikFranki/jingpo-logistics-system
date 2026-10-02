"""Server-owned UTC timestamps, independent of business concurrency control."""
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

# Preserve the existing serialization of business writes without a clock row.
BUSINESS_WRITE_LOCK = 74219001


def server_now() -> datetime:
    return datetime.now(timezone.utc)


def begin_business_write(session: Session) -> datetime:
    """Call inside a transaction; sample time after waiting for the write lock."""
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": BUSINESS_WRITE_LOCK})
    return server_now()


def normalize_cached_response(value):
    """Read legacy responses without changing stored action/history timestamps."""
    if isinstance(value, list):
        return [normalize_cached_response(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: normalize_cached_response(item) for key, item in value.items()
              if key != "simulation_time"}
    if "simulation_time" in value:
        result["server_time"] = server_now().isoformat()
    return result
