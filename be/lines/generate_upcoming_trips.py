"""Generate a rolling window of dated trips from enabled line services."""
import argparse
import sys
from datetime import date, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import delete, exists, select

from business_time import server_now
from db import SessionLocal
from lines.schemas import GenerateTripsRequest
from lines.service_schedules import generate_trips
from models import (LineService, PathPlan, ScheduledTrip, ShipmentScheduleVersion,
                    TransportTask)

TIMEZONE = ZoneInfo("Asia/Shanghai")


def cleanup_expired_trips(today, retention_days):
    cutoff = today - timedelta(days=retention_days)
    task_reference = exists(select(TransportTask.id).where(
        TransportTask.scheduled_trip_id == ScheduledTrip.id))
    schedule_reference = exists(select(ShipmentScheduleVersion.id).where(
        ShipmentScheduleVersion.scheduled_trip_id == ScheduledTrip.id))
    with SessionLocal() as session, session.begin():
        result = session.execute(delete(ScheduledTrip).where(
            ScheduledTrip.service_date < cutoff,
            ~task_reference,
            ~schedule_reference))
        return result.rowcount or 0


def run(days=7, from_date=None, retention_days=30):
    if not 1 <= days <= 31:
        raise ValueError("days must be between 1 and 31")
    if not 1 <= retention_days <= 3650:
        raise ValueError("retention_days must be between 1 and 3650")
    today = server_now().astimezone(TIMEZONE).date()
    start_date = from_date or today
    end_date = start_date + timedelta(days=days - 1)
    with SessionLocal() as session:
        services = list(session.scalars(select(LineService).join(
            PathPlan, PathPlan.id == LineService.line_id).where(
            LineService.enabled.is_(True), PathPlan.enabled.is_(True)).order_by(
                LineService.id)))

    trip_slots = 0
    failures = []
    request = GenerateTripsRequest(from_date=start_date, to_date=end_date)
    for service in services:
        key = uuid5(NAMESPACE_URL,
            f"jingpo/rolling-trips/v2/{service.id}/{start_date}/{end_date}")
        try:
            with SessionLocal() as session:
                result = generate_trips(session, service.line_id, service.id, request, key,
                                        compact_response=True, record_operation=False)
            trip_slots += result["trip_slots"]
        except Exception as exc:
            failures.append((service.id, service.line_id, exc))
            print(f"failed service={service.id} line={service.line_id}: {exc}", file=sys.stderr)

    try:
        deleted = cleanup_expired_trips(today, retention_days)
    except Exception as exc:
        failures.append(("cleanup", "expired trips", exc))
        deleted = 0
        print(f"failed cleanup of trips older than {retention_days} days: {exc}", file=sys.stderr)

    print(f"window={start_date}..{end_date} services={len(services)} "
          f"trip_slots={trip_slots} expired_deleted={deleted} "
          f"retention_days={retention_days} failures={len(failures)}")
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7, help="rolling window, 1 to 31 days")
    parser.add_argument("--from-date", type=date.fromisoformat,
                        help="first service date; defaults to today in Asia/Shanghai")
    parser.add_argument("--retention-days", type=int, default=30,
                        help="keep unreferenced past trips for this many days")
    args = parser.parse_args()
    try:
        return run(args.days, args.from_date, args.retention_days)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
