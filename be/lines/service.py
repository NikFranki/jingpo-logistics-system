"""One public transport line, backed by reusable execution segments."""
import hashlib
from sqlalchemy import select, delete, func

from errors import NetworkError
from models import LineService, TransportLine, TransportLineLeg, TransportRoute, OperationLog
from network.service import station_body, require_station
from scheduling.schedule_support import line_summary, line_routes, validate_routes, time_and_replay, digest_request, anchor_station
from network.coverage import planned_origin
from lines.schemas import LineCreateRequest


def line_body(session, line):
    base = line_summary(session, line)
    routes = line_routes(session, line)
    entries = list(session.scalars(select(TransportLineLeg).where(TransportLineLeg.line_id == line.id)
                                    .order_by(TransportLineLeg.position)))
    station_ids = [routes[0].origin_station_id] + [r.destination_station_id for r in routes] if routes else []
    legs = []
    total = 0
    complete = True
    for i, (route, entry) in enumerate(zip(routes, entries)):
        travel = entry.travel_override_minutes if entry.travel_override_minutes is not None else route.travel_minutes
        transfer = 0 if i == 0 else entry.origin_transfer_override_minutes
        if i and transfer is None:
            transfer = require_station(session, route.origin_station_id).transfer_minutes
        if travel is None or transfer is None:
            complete = False
        else:
            total += travel + transfer
        legs.append(dict(position=i, segment_id=str(route.id), origin_station_id=str(route.origin_station_id),
                         destination_station_id=str(route.destination_station_id), travel_minutes=travel))
    return {k: base[k] for k in ('id', 'code', 'name', 'version', 'enabled', 'usable', 'reason',
                                 'origin_station_id', 'destination_station_id')} | dict(
        transfer_overrides=[dict(station_id=str(t['station_id']),minutes=t['minutes']) for t in base['transfer_overrides']],
        station_ids=[str(i) for i in station_ids], stations=[station_body(require_station(session, i)) for i in station_ids],
        legs=legs, total_reference_minutes=total if complete else None)


def list_lines(session, enabled=None, origin_station_id=None, destination_station_id=None):
    query = select(TransportLine)
    for column, value in [(TransportLine.enabled, enabled), (TransportLine.origin_station_id, origin_station_id),
                          (TransportLine.destination_station_id, destination_station_id)]:
        if value is not None:
            query = query.where(column == value)
    result = [line_body(session, p) for p in session.scalars(query.order_by(TransportLine.id))]
    return sorted(result, key=lambda x: (not x['usable'], x['total_reference_minutes'] is None,
        len(x['legs']), x['total_reference_minutes'] or 0, x['code']))


def line_options(session, shipment):
    origin = anchor_station(session, shipment) or planned_origin(session, shipment)
    candidates = list_lines(session, True, origin, shipment.destination_station_id) if origin else []
    candidates = [c for c in candidates if c['usable']]
    return dict(origin_station_id=str(origin) if origin else None,
                destination_station_id=str(shipment.destination_station_id), lines=candidates,
                recommended_line_id=candidates[0]['id'] if candidates else None)


def ensure_direct_line(session, route):
    existing = session.scalar(select(TransportLineLeg.line_id).where(TransportLineLeg.route_id == route.id,
        TransportLineLeg.line_id.in_(select(TransportLineLeg.line_id).group_by(TransportLineLeg.line_id)
                                     .having(func.count() == 1))).limit(1))
    if existing:
        return existing
    origin, destination = require_station(session, route.origin_station_id), require_station(session, route.destination_station_id)
    line = TransportLine(code=f'L_SEG_{route.id}', name=f'{origin.name} → {destination.name}'[:100],
                    origin_station_id=origin.id, destination_station_id=destination.id, enabled=route.enabled)
    session.add(line)
    session.flush()
    session.add(TransportLineLeg(line_id=line.id, position=0, route_id=route.id))
    session.flush()
    return line.id


def write_line(session, request, key, line_id=None):
    action = 'CREATE_TRANSPORT_LINE' if line_id is None else 'UPDATE_TRANSPORT_LINE'
    digest = digest_request(action, line_id, request)
    with session.begin():
        now, replay = time_and_replay(session, key, digest)
        if replay is not None:
            body = dict(replay.response_body)
            body['transfer_overrides'] = [dict(t, station_id=str(t['station_id'])) for t in body.get('transfer_overrides', [])]
            return body
        before = None
        if line_id is None:
            if session.scalar(select(TransportLine.id).where(TransportLine.code == request.code)):
                raise NetworkError('NETWORK_CODE_CONFLICT', '运输线路编码已存在')
            definition = request
            line = TransportLine(code=request.code, name=request.name, enabled=request.enabled,
                            origin_station_id=request.station_ids[0], destination_station_id=request.station_ids[-1])
        else:
            line = session.scalar(select(TransportLine).where(TransportLine.id == line_id).with_for_update())
            if line is None:
                raise NetworkError('TRANSPORT_LINE_NOT_FOUND', '运输线路不存在', 404)
            if line.version != request.expected_version:
                raise NetworkError('LINE_VERSION_CONFLICT', '线路已变化，请刷新后重试')
            before = line_body(session, line)
            stations = request.station_ids if request.station_ids is not None else [int(i) for i in before['station_ids']]
            if stations != [int(i) for i in before['station_ids']] and session.scalar(select(LineService.id).where(
                    LineService.line_id == line.id, LineService.enabled.is_(True)).limit(1)):
                raise NetworkError('LINE_SERVICES_REQUIRE_UPDATE', '先停用该线路的班次规则，再调整站点顺序')
            timing = request.legs if request.legs is not None else [dict(travel_minutes=l['travel_minutes']) for l in before['legs']]
            transfer = request.transfer_overrides
            if transfer is None:
                transfer = [dict(station_id=int(t['station_id']),minutes=t['minutes']) for t in before['transfer_overrides'] if int(t['station_id']) in stations[1:-1]]
            definition = LineCreateRequest(code=line.code, name=request.name if request.name is not None else line.name,
                enabled=request.enabled if request.enabled is not None else line.enabled,
                station_ids=stations, legs=timing, transfer_overrides=transfer)
        nodes = [require_station(session, i) for i in definition.station_ids]
        if definition.enabled and (any(not s.enabled for s in nodes) or not nodes[0].allows_first_arrival or not nodes[-1].allows_delivery):
            raise NetworkError('INVALID_NETWORK_CONFIGURATION', '启用线路要求站点启用，首站可接收且末站可派送')
        routes = []
        for origin, destination in zip(nodes, nodes[1:]):
            route = session.scalar(select(TransportRoute).where(TransportRoute.origin_station_id == origin.id,
                                                              TransportRoute.destination_station_id == destination.id))
            if route is None:
                code = 'SEG_' + hashlib.sha256(f'{origin.id}:{destination.id}'.encode()).hexdigest()[:24].upper()
                route = TransportRoute(code=code, origin_station_id=origin.id, destination_station_id=destination.id,
                                       enabled=True, delay_monitoring_enabled=False)
                session.add(route)
                session.flush()
            routes.append(route)
        validate_routes(session, routes, definition.station_ids[0], definition.station_ids[-1], definition.enabled)
        if line_id is not None:
            line.name, line.enabled = definition.name, definition.enabled
            line.origin_station_id, line.destination_station_id = definition.station_ids[0], definition.station_ids[-1]
            line.version += 1
        session.add(line)
        session.flush()
        if line_id is not None:
            session.execute(delete(TransportLineLeg).where(TransportLineLeg.line_id == line.id))
        transfer = {v.station_id: v.minutes for v in definition.transfer_overrides}
        session.add_all([TransportLineLeg(line_id=line.id, position=i, route_id=r.id,
            travel_override_minutes=definition.legs[i].travel_minutes,
            origin_transfer_override_minutes=transfer.get(r.origin_station_id)) for i, r in enumerate(routes)])
        session.flush()
        body = line_body(session, line)
        session.add(OperationLog(idempotency_key=key, request_hash=digest, action=action, resource_type='TRANSPORT_LINE',
            resource_id=line.id, before_data=before, after_data=body, response_body=body,
            response_status=201 if line_id is None else 200, occurred_at=now))
        return body
