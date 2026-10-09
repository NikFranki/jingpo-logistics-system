import hashlib
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select

from business_time import server_now
from errors import IdempotencyKeyReusedError, NetworkError
from models import (LineService, LineServiceStop, OperationLog, TransportLine, ScheduledTrip,
                    Shipment, TransportRoute)
from network.coverage import planned_origin
from scheduling.schedule_support import (anchor_station, line_routes, routes_for_ids, time_and_replay,
                              validate_routes)


def fail(code, message, status=422):
    raise NetworkError(code, message, status)


def service_stops(session, service_id):
    return list(session.scalars(select(LineServiceStop).where(
        LineServiceStop.service_id == service_id).order_by(LineServiceStop.position)))


def service_body(session, service):
    return {
        "id": str(service.id), "line_id": str(service.line_id), "code": service.code,
        "name": service.name, "valid_from": service.valid_from.isoformat(),
        "valid_until": service.valid_until.isoformat() if service.valid_until else None,
        "weekdays": service.weekdays, "timezone": service.timezone,
        "capacity_snapshot": service.capacity_snapshot, "enabled": service.enabled,
        "version": service.version,
        "stops": [{"position": stop.position, "station_id": str(stop.station_id),
            "arrival_day_offset": stop.arrival_day_offset, "arrival_time": stop.arrival_time.isoformat() if stop.arrival_time else None,
            "departure_day_offset": stop.departure_day_offset, "departure_time": stop.departure_time.isoformat() if stop.departure_time else None}
            for stop in service_stops(session, service.id)],
    }


def validate_timetable(stops, weekdays, timezone):
    if not weekdays or len(set(weekdays)) != len(weekdays) or any(day < 1 or day > 7 for day in weekdays):
        fail("INVALID_LINE_SERVICE", "运行星期需为 1 到 7 且不能重复")
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        fail("INVALID_LINE_SERVICE", "时区名称无效")
    timeline = []
    for position, stop in enumerate(stops):
        arrival = stop.get("arrival_time")
        departure = stop.get("departure_time")
        arrival_day = stop.get("arrival_day_offset", 0)
        departure_day = stop.get("departure_day_offset", 0)
        if (arrival is None) != (arrival_day is None):
            fail("INVALID_LINE_SERVICE", "到达时刻和跨日天数必须同时填写")
        if (departure is None) != (departure_day is None):
            fail("INVALID_LINE_SERVICE", "发车时刻和跨日天数必须同时填写")
        if position == 0 and (arrival is not None or departure is None):
            fail("INVALID_LINE_SERVICE", "首站只填写发车时间")
        if position == 0 and departure_day != 0:
            fail("INVALID_LINE_SERVICE", "班次服务日期就是首站发车日期")
        if position == len(stops) - 1 and (arrival is None or departure is not None):
            fail("INVALID_LINE_SERVICE", "末站只填写到达时间")
        if 0 < position < len(stops) - 1 and (arrival is None or departure is None):
            fail("INVALID_LINE_SERVICE", "中间站必须填写到达和发车时间")
        for value in (arrival, departure):
            if value is not None and (value.second or value.microsecond or value.tzinfo is not None):
                fail("INVALID_LINE_SERVICE", "班次时间使用当地时区并精确到分钟")
        stop_events = []
        if arrival is not None:
            stop_events.append(datetime.combine(date(2000, 1, 1) + timedelta(days=arrival_day), arrival, zone))
        if departure is not None:
            stop_events.append(datetime.combine(date(2000, 1, 1) + timedelta(days=departure_day), departure, zone))
        if len(stop_events) == 2 and stop_events[1] <= stop_events[0]:
            fail("INVALID_LINE_SERVICE", "同一站的发车必须晚于到达")
        timeline.extend(stop_events)
    if any(later <= earlier for earlier, later in zip(timeline, timeline[1:])):
        fail("INVALID_LINE_SERVICE", "全程到发时刻必须严格递增，请检查跨日天数")


def materialize_trips(session, line, service, from_date, to_date):
    days = (to_date - from_date).days
    if days < 0 or days > 30:
        fail("INVALID_DATE_RANGE", "生成日期范围最多 31 天")
    zone = ZoneInfo(service.timezone)
    if from_date < server_now().astimezone(zone).date():
        fail("INVALID_DATE_RANGE", "不能为过去日期生成新的计划车次")
    routes = line_routes(session, line)
    stops = service_stops(session, service.id)
    created = 0
    slots = 0
    for day_offset in range(days + 1):
        service_date = from_date + timedelta(days=day_offset)
        if service_date < service.valid_from or (service.valid_until and service_date > service.valid_until):
            continue
        if service_date.isoweekday() not in service.weekdays:
            continue
        existing = session.scalar(select(ScheduledTrip).where(
            ScheduledTrip.service_id == service.id, ScheduledTrip.service_date == service_date,
            ScheduledTrip.service_version == service.version).with_for_update())
        if existing:
            slots += 1
            continue
        snapshot = []
        for position, stop in enumerate(stops):
            route = routes[position] if position < len(routes) else None
            arrival = datetime.combine(service_date + timedelta(days=stop.arrival_day_offset), stop.arrival_time, zone).isoformat() if stop.arrival_time else None
            departure = datetime.combine(service_date + timedelta(days=stop.departure_day_offset), stop.departure_time, zone).isoformat() if stop.departure_time else None
            snapshot.append({"position": position, "station_id": str(stop.station_id),
                "arrival_at": arrival, "departure_at": departure,
                "outbound_route_id": str(route.id) if route else None,
                "outbound_route_code": route.code if route else None})
        session.add(ScheduledTrip(service_id=service.id, service_date=service_date,
            service_version=service.version, line_id=line.id, line_version=line.version,
            status="PLANNED", stops_snapshot=snapshot, capacity_snapshot=dict(service.capacity_snapshot)))
        session.flush()
        created += 1
        slots += 1
    return {"created": created, "trip_slots": slots}


def list_services(session, line_id):
    if session.get(TransportLine, line_id) is None:
        raise NetworkError("TRANSPORT_LINE_NOT_FOUND", "运输线路不存在", 404)
    services = session.scalars(select(LineService).where(LineService.line_id == line_id)
                               .order_by(LineService.code, LineService.id))
    return [service_body(session, service) for service in services]


def write_service(session, line_id, request, key, service_id=None):
    body = request.model_dump(mode="json", exclude_unset=service_id is not None)
    content = json.dumps({"action": "UPDATE_LINE_SERVICE" if service_id else "CREATE_LINE_SERVICE",
        "line_id": line_id, "service_id": service_id, "body": body}, sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(content.encode()).hexdigest()
    with session.begin():
        now, replay = time_and_replay(session, key, request_hash)
        if replay:
            return replay.response_body
        line = session.scalar(select(TransportLine).where(TransportLine.id == line_id).with_for_update())
        if line is None:
            raise NetworkError("TRANSPORT_LINE_NOT_FOUND", "运输线路不存在", 404)
        current = None
        before = None
        if service_id is not None:
            current = session.scalar(select(LineService).where(LineService.id == service_id,
                LineService.line_id == line_id).with_for_update())
            if current is None:
                raise NetworkError("LINE_SERVICE_NOT_FOUND", "线路班次不存在", 404)
            if current.version != request.expected_version:
                raise NetworkError("LINE_SERVICE_VERSION_CONFLICT", "班次规则已变化，请刷新后重试")
            before = service_body(session, current)
        values = body if current is None else service_body(session, current) | body
        values.pop("id", None)
        values.pop("line_id", None)
        values.pop("version", None)
        values.pop("expected_version", None)
        values["valid_from"] = date.fromisoformat(values["valid_from"]) if isinstance(values["valid_from"], str) else values["valid_from"]
        values["valid_until"] = date.fromisoformat(values["valid_until"]) if isinstance(values.get("valid_until"), str) else values.get("valid_until")
        stops = values["stops"]
        for stop in stops:
            for time_field in ("arrival_time", "departure_time"):
                if isinstance(stop.get(time_field), str):
                    stop[time_field] = time.fromisoformat(stop[time_field])
        route_rows = line_routes(session, line)
        expected_stations = ([route_rows[0].origin_station_id] + [route.destination_station_id for route in route_rows]) if route_rows else []
        if [int(stop["station_id"]) for stop in stops] != expected_stations:
            fail("INVALID_LINE_SERVICE", "班次站点顺序必须与运输线路完全一致")
        validate_timetable(stops, values["weekdays"], values["timezone"])
        if values["valid_until"] and values["valid_until"] < values["valid_from"]:
            fail("INVALID_LINE_SERVICE", "班次结束日期不能早于生效日期")
        if current is None:
            if session.scalar(select(LineService.id).where(LineService.line_id == line_id,
                    LineService.code == values["code"])):
                fail("LINE_SERVICE_CODE_CONFLICT", "该线路已有相同班次编码", 409)
            current = LineService(line_id=line_id, code=values["code"], name=values["name"],
                valid_from=values["valid_from"], valid_until=values["valid_until"],
                weekdays=values["weekdays"], timezone=values["timezone"],
                capacity_snapshot=values["capacity_snapshot"], enabled=values["enabled"], version=1)
            session.add(current)
            session.flush()
        else:
            for field in ("name", "valid_from", "valid_until", "weekdays", "timezone", "capacity_snapshot", "enabled"):
                setattr(current, field, values[field])
            current.version += 1
            session.execute(delete(LineServiceStop).where(LineServiceStop.service_id == current.id))
        session.add_all([LineServiceStop(service_id=current.id, position=position,
            station_id=int(stop["station_id"]), arrival_day_offset=stop["arrival_day_offset"],
            arrival_time=stop["arrival_time"], departure_day_offset=stop["departure_day_offset"],
            departure_time=stop["departure_time"]) for position, stop in enumerate(stops)])
        session.flush()
        if line.enabled and current.enabled:
            today = server_now().astimezone(ZoneInfo(current.timezone)).date()
            materialize_trips(session, line, current, today, today + timedelta(days=6))
        result = service_body(session, current)
        session.add(OperationLog(idempotency_key=key, request_hash=request_hash,
            action="UPDATE_LINE_SERVICE" if service_id else "CREATE_LINE_SERVICE", resource_type="LINE_SERVICE",
            resource_id=current.id, before_data=before, after_data=result, response_body=result,
            response_status=200 if service_id else 201, occurred_at=now))
        return result


def generate_trips(session, line_id, service_id, request, key, compact_response=False,
                   record_operation=True):
    body = request.model_dump(mode="json")
    content = json.dumps({"action": "GENERATE_SCHEDULED_TRIPS", "service_id": service_id,
        "body": body, "compact_response": compact_response}, sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(content.encode()).hexdigest()
    with session.begin():
        now, replay = time_and_replay(session, key, request_hash)
        if replay:
            return replay.response_body
        service = session.scalar(select(LineService).where(LineService.id == service_id,
            LineService.line_id == line_id).with_for_update())
        if service is None:
            raise NetworkError("LINE_SERVICE_NOT_FOUND", "线路班次不存在", 404)
        if not service.enabled:
            fail("LINE_SERVICE_DISABLED", "已停用的班次不能生成车次", 409)
        line = session.get(TransportLine, line_id)
        if not line.enabled:
            fail("TRANSPORT_LINE_DISABLED", "已停用的运输线路不能生成车次", 409)
        from_date = date.fromisoformat(body["from_date"])
        to_date = date.fromisoformat(body["to_date"])
        generated = materialize_trips(session, line, service, from_date, to_date)
        created_count, trip_slots = generated["created"], generated["trip_slots"]
        items = []
        if not compact_response:
            trips = session.scalars(select(ScheduledTrip).where(
                ScheduledTrip.service_id == service.id,
                ScheduledTrip.service_version == service.version,
                ScheduledTrip.service_date >= from_date,
                ScheduledTrip.service_date <= to_date).order_by(ScheduledTrip.service_date))
            items = [trip_body(trip, service) for trip in trips]
        response = ({"trip_slots": trip_slots, "created": created_count,
            "from_date": from_date.isoformat(), "to_date": to_date.isoformat()} if compact_response else
            {"items": items, "created": created_count,
             "from_date": from_date.isoformat(), "to_date": to_date.isoformat()})
        if record_operation:
            session.add(OperationLog(idempotency_key=key, request_hash=request_hash,
                action="GENERATE_SCHEDULED_TRIPS", resource_type="LINE_SERVICE", resource_id=service.id,
                before_data=None, after_data=response, response_body=response, response_status=200, occurred_at=now))
        return response


def trip_body(trip, service=None):
    return {"id": str(trip.id), "line_id": str(trip.line_id), "line_version": trip.line_version,
        "service_id": str(trip.service_id), "service_code": service.code if service else None,
        "service_name": service.name if service else None, "service_date": trip.service_date.isoformat(),
        "service_version": trip.service_version, "status": trip.status,
        "stops": trip.stops_snapshot, "capacity_snapshot": trip.capacity_snapshot}


def shipment_service_options(session, shipment_id, from_date, to_date):
    shipment = session.get(Shipment, shipment_id)
    if shipment is None:
        raise NetworkError("SHIPMENT_NOT_FOUND", "运单不存在", 404)
    if (to_date - from_date).days < 0 or (to_date - from_date).days > 30:
        fail("INVALID_DATE_RANGE", "查询日期范围最多 31 天")
    origin = anchor_station(session, shipment) or planned_origin(session, shipment)
    if origin is None:
        fail("SCHEDULE_ORIGIN_REQUIRED", "无法确定运单起点")
    now = server_now()
    earliest = max(shipment.earliest_handover_at or now, now)
    trips = session.scalars(select(ScheduledTrip).join(LineService, LineService.id == ScheduledTrip.service_id)
        .join(TransportLine, TransportLine.id == ScheduledTrip.line_id).where(
        ScheduledTrip.status == "PLANNED", LineService.enabled.is_(True),
        LineService.version == ScheduledTrip.service_version, TransportLine.enabled.is_(True),
        ScheduledTrip.service_date >= from_date - timedelta(days=30),
        ScheduledTrip.service_date <= to_date).order_by(ScheduledTrip.service_date, ScheduledTrip.id))
    options = []
    for trip in trips:
        stops = trip.stops_snapshot
        origins = [index for index, stop in enumerate(stops) if int(stop["station_id"]) == origin]
        destinations = [index for index, stop in enumerate(stops) if int(stop["station_id"]) == shipment.destination_station_id]
        for start in origins:
            end = next((index for index in destinations if index > start), None)
            if end is None:
                continue
            departure = datetime.fromisoformat(stops[start]["departure_at"])
            arrival = datetime.fromisoformat(stops[end]["arrival_at"])
            if departure.date() < from_date or departure.date() > to_date or departure < earliest or (
                    shipment.latest_delivery_at and arrival > shipment.latest_delivery_at):
                continue
            legs = [{"route_id": stop["outbound_route_id"], "route_code": stop["outbound_route_code"],
                "origin_station_id": stop["station_id"], "destination_station_id": stops[index + 1]["station_id"],
                "planned_departure_at": stop["departure_at"], "planned_arrival_at": stops[index + 1]["arrival_at"]}
                for index, stop in enumerate(stops[start:end], start=start)]
            routes = routes_for_ids(session, [int(leg["route_id"]) for leg in legs])
            try:
                validate_routes(session, routes, origin, shipment.destination_station_id)
            except NetworkError:
                continue
            service = session.get(LineService, trip.service_id)
            options.append({"trip_id": str(trip.id), "line_id": str(trip.line_id), "line_version": trip.line_version,
                "service_id": str(service.id), "service_code": service.code, "service_name": service.name,
                "service_date": trip.service_date.isoformat(), "origin_station_id": str(origin),
                "destination_station_id": str(shipment.destination_station_id), "departure_at": departure.isoformat(),
                "arrival_at": arrival.isoformat(), "capacity_snapshot": trip.capacity_snapshot, "legs": legs})
    options.sort(key=lambda item: item["departure_at"])
    return {"origin_station_id": str(origin), "destination_station_id": str(shipment.destination_station_id), "items": options}
