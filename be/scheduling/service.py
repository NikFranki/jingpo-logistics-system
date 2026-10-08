import business_time
"""Pure previews, reviewed atomic confirmations and activation of existing tasks."""
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid5, NAMESPACE_URL

from sqlalchemy import select, func
from errors import NetworkError, ShipmentNotFoundError
from models import (Shipment, Station, TransportRoute, TransportTask, TaskShipment, ShipmentPathLeg,
                    ShipmentScheduleVersion, OperationLog, PathPlan, PathPlanLeg, ScheduledTrip, LineService)
from planning.service import (active_task, current_legs, leg_state, plan_routes, routes_for_ids,
                              validate_routes, save_path, time_and_replay)
from network.coverage import planned_origin
from planning.service import candidate_route_paths
from scheduling.schemas import SchedulePreviewRequest

_SECRET = os.getenv('SCHEDULE_SIGNING_KEY', '').encode() or secrets.token_bytes(32)
UNSTARTED = ('WAITING_CARGO', 'WAITING_PREDECESSOR', 'PENDING_DEPARTURE')


def error(code, message, status=409):
    raise NetworkError(code, message, status)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def sign(kind, value):
    encoded = base64.urlsafe_b64encode(canonical({'kind': kind, 'expires': time.time()+600, 'data': value}).encode()).decode()
    return encoded + '.' + hmac.new(_SECRET, encoded.encode(), hashlib.sha256).hexdigest()


def unsign(token, kind):
    try:
        encoded, signature = token.rsplit('.', 1)
        if not hmac.compare_digest(signature, hmac.new(_SECRET, encoded.encode(), hashlib.sha256).hexdigest()):
            raise ValueError()
        body = json.loads(base64.urlsafe_b64decode(encoded))
        if body['kind'] != kind:
            raise ValueError()
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError):
        error('PREVIEW_STALE', '预览凭据无效，请重新预览')
    if body['expires'] < time.time():
        error('PREVIEW_EXPIRED', '预览已过期，请重新预览')
    return body['data']



def parcel(session, shipment_id):
    value = session.get(Shipment, shipment_id)
    if value is None:
        raise ShipmentNotFoundError
    return value


def members(session, task_id, open_only=True):
    query = select(TaskShipment).where(TaskShipment.task_id == task_id).order_by(TaskShipment.id)
    if open_only:
        query = query.where(TaskShipment.association_state != 'RELEASED')
    return list(session.scalars(query))


def forecast(session, task, now=None, cache=None, visiting=None):
    now = now or business_time.server_now()
    cache = {} if cache is None else cache
    visiting = set() if visiting is None else visiting
    if task.id in cache:
        return cache[task.id]
    if task.id in visiting:
        error('INVALID_TASK_DEPENDENCY', '任务依赖存在循环')
    visiting.add(task.id)
    departure, arrival, stale = None, None, False
    if task.status == 'ARRIVED':
        departure, arrival = task.departed_at, task.arrived_at
    elif task.scheduling_source == 'PLAN' and task.status != 'CANCELLED':
        duration = timedelta(minutes=task.planned_travel_minutes)
        if task.status == 'IN_TRANSIT':
            departure = task.departed_at
            raw = departure + duration
            stale = now > raw
            arrival = max(raw, now)
        else:
            readiness = []
            unknown = False
            for entry in members(session, task.id):
                if entry.association_state == 'ACTIVE':
                    readiness.append(entry.ready_at or now)
                elif entry.predecessor_association_id:
                    predecessor = session.get(TaskShipment, entry.predecessor_association_id)
                    previous_task = session.get(TransportTask, predecessor.task_id)
                    _, previous_arrival, previous_stale = forecast(session, previous_task, now, cache, visiting)
                    stale = stale or previous_stale
                    if previous_arrival is None:
                        unknown = True
                    else:
                        readiness.append(previous_arrival + timedelta(minutes=entry.approved_transfer_minutes))
                elif entry.planned_origin_arrival_at:
                    readiness.append(max(now, entry.planned_origin_arrival_at))
                else:
                    unknown = True
            trip_missed = bool(task.scheduled_trip_id and
                now.replace(second=0, microsecond=0) > task.planned_departure_at)
            if not unknown and not trip_missed:
                departure = max([task.planned_departure_at, now, *readiness])
                arrival = departure + duration
            elif trip_missed:
                stale = True
    visiting.remove(task.id)
    result = departure, arrival, stale
    cache[task.id] = result
    return result


def task_extra(session, task, now=None, cache=None):
    departure, arrival, stale = forecast(session, task, now, cache)
    waiting = []
    for entry in members(session, task.id):
        if entry.association_state == 'PLANNED':
            waiting.append({'shipment_id': str(entry.shipment_id), 'reason_code':
                'PREDECESSOR_NOT_ARRIVED' if entry.predecessor_association_id else 'FIRST_ARRIVAL_REQUIRED'})
    return {'scheduling_source': task.scheduling_source, 'planned_departure_at':
            task.planned_departure_at.isoformat() if task.planned_departure_at else None,
            'scheduled_trip_id': str(task.scheduled_trip_id) if task.scheduled_trip_id else None,
            'scheduled_trip_missed': bool(task.scheduled_trip_id and task.planned_departure_at and
                now.replace(second=0, microsecond=0) > task.planned_departure_at and task.status in UNSTARTED),
            'schedule_revision': task.schedule_revision, 'forecast_departure_at': departure.isoformat() if departure else None,
            'forecast_arrival_at': arrival.isoformat() if arrival else None, 'forecast_stale': stale,
            'waiting_members': waiting}


def departure_problem(session, task, now):
    if task.scheduling_source != 'PLAN':
        return None
    if task.scheduled_trip_id and now.replace(second=0, microsecond=0) > task.planned_departure_at:
        return 'SCHEDULED_TRIP_MISSED', '已错过该班次，请重新审核并改排后续班次'
    entries = members(session, task.id)
    if not entries or any(e.association_state != 'ACTIVE' for e in entries):
        return 'TASK_PREDECESSOR_NOT_ARRIVED', '仍有运单未实际到达本段起点'
    earliest = max([task.planned_departure_at, *[e.ready_at or now for e in entries]])
    if now < earliest:
        return 'TASK_NOT_READY', '尚未到已确认发车或中转就绪时间：' + earliest.isoformat()
    return None


def recompute_task_state(session, task):
    if task.status not in UNSTARTED:
        return
    entries = members(session, task.id)
    if any(e.association_state == 'PLANNED' and e.predecessor_association_id for e in entries):
        task.status = 'WAITING_PREDECESSOR'
    elif any(e.association_state == 'PLANNED' for e in entries):
        task.status = 'WAITING_CARGO'
    else:
        task.status = 'PENDING_DEPARTURE'


def sub_log(session, parent, action, entry, body):
    key = uuid5(NAMESPACE_URL, f'jingpo-v7:{parent.id}:{action}:{entry.shipment_id}:{entry.id}')
    session.add(OperationLog(idempotency_key=key, request_hash=digest(body), action=action,
        resource_type='TRANSPORT_TASK', resource_id=entry.task_id, parent_operation_id=parent.id,
        before_data=None, after_data=body, response_body=body, response_status=200,
        occurred_at=parent.occurred_at))


def activate_next(session, shipment, now, parent):
    if shipment.scheduling_mode != 'REVIEWED' or shipment.schedule_status in ('NEEDS_RECONFIRMATION', 'BLOCKED'):
        return
    pending = list(session.scalars(select(TaskShipment).join(ShipmentPathLeg, TaskShipment.path_leg_id == ShipmentPathLeg.id)
        .where(TaskShipment.shipment_id == shipment.id, TaskShipment.association_state == 'PLANNED',
               ShipmentPathLeg.superseded_at.is_(None)).order_by(ShipmentPathLeg.position)))
    if shipment.last_scanned_station_id == shipment.destination_station_id and not pending:
        shipment.schedule_status = 'COMPLETED'
        return
    if not pending:
        if shipment.schedule_version:
            shipment.schedule_status = 'NEEDS_RECONFIRMATION'
            shipment.schedule_reason = '后续任务链缺失，请重新确认运输计划'
        return
    entry = pending[0]
    task = session.get(TransportTask, entry.task_id)
    route = session.get(TransportRoute, task.route_id)
    if route.origin_station_id != shipment.last_scanned_station_id:
        shipment.schedule_status = 'BLOCKED'
        shipment.schedule_reason = '实际入站与计划起点不一致，请重新确认路线'
        return
    if entry.predecessor_association_id:
        predecessor = session.get(TaskShipment, entry.predecessor_association_id)
        if not predecessor or predecessor.shipment_id != shipment.id or session.get(TransportTask, predecessor.task_id).status != 'ARRIVED':
            shipment.schedule_status = 'NEEDS_RECONFIRMATION'
            shipment.schedule_reason = '前序运输尚未完成或依赖不完整'
            return
    if active_task(session, shipment) is not None:
        error('INVALID_TASK_DEPENDENCY', '运单已经有当前执行占用')
    entry.association_state = 'ACTIVE'
    entry.ready_at = now + timedelta(minutes=entry.approved_transfer_minutes)
    session.flush()
    recompute_task_state(session, task)
    sub_log(session, parent, 'ACTIVATE_PLANNED_TASK', entry, {'association_id': str(entry.id), 'state': 'ACTIVE'})


def context(session, shipment):
    if shipment.stage in ('OUT_FOR_DELIVERY', 'SIGNED'):
        error('INVALID_SCHEDULE_STAGE', '派送中或签收后不能规划站间运输')
    pair = active_task(session, shipment)
    frozen_task = pair[1] if pair and pair[1].status == 'IN_TRANSIT' else None
    anchor = session.get(TransportRoute, frozen_task.route_id).destination_station_id if frozen_task else shipment.last_scanned_station_id
    return pair, frozen_task, anchor


def shared_candidate(session, route, departure, arrival, shipment_id, now, not_before=None,
                     arrive_by=None, use_window=False, scheduled_trip_id=None):
    if departure < now:
        return None
    query = select(TransportTask).where(TransportTask.route_id == route.id,
        TransportTask.scheduling_source == 'PLAN', TransportTask.status.in_(UNSTARTED),
        TransportTask.delay_monitoring_enabled == route.delay_monitoring_enabled)
    if scheduled_trip_id is not None:
        query = query.where(TransportTask.scheduled_trip_id == scheduled_trip_id)
    else:
        query = query.where(TransportTask.scheduled_trip_id.is_(None))
    if use_window:
        query = query.where(TransportTask.planned_departure_at >= (not_before or now))
        if arrive_by is not None:
            query = query.where(TransportTask.expected_arrival_at <= arrive_by)
        query = query.order_by(TransportTask.planned_departure_at, TransportTask.id)
    else:
        query = query.where(TransportTask.planned_departure_at == departure,
            TransportTask.expected_arrival_at == arrival).order_by(TransportTask.id)
    candidates = session.scalars(query)
    for task in candidates:
        entries = members(session, task.id)
        if len(entries) < 100 and all(e.shipment_id != shipment_id for e in entries):
            return task
    return None


def scheduled_trip_segments(session, trip, origin_station_id, destination_station_id):
    stops = trip.stops_snapshot
    starts = [i for i, stop in enumerate(stops) if int(stop['station_id']) == origin_station_id]
    ends = [i for i, stop in enumerate(stops) if int(stop['station_id']) == destination_station_id]
    start = starts[0] if starts else None
    end = next((i for i in ends if start is not None and i > start), None)
    if start is None or end is None:
        error('SCHEDULED_TRIP_ROUTE_MISMATCH', '该班次不经过运单起点和目的站')
    segments = []
    for index in range(start, end):
        stop = stops[index]
        next_stop = stops[index + 1]
        if not stop.get('outbound_route_id') or not stop.get('departure_at') or not next_stop.get('arrival_at'):
            error('SCHEDULED_TRIP_INVALID', '班次时刻快照缺少线路或到发时间')
        segments.append({'route_id': int(stop['outbound_route_id']),
            'planned_departure_at': stop['departure_at'], 'planned_arrival_at': next_stop['arrival_at']})
    return routes_for_ids(session, [segment['route_id'] for segment in segments]), segments


def make_preview(session, shipment, request, now=None):
    now = now or business_time.server_now()
    pair, frozen_task, anchor = context(session, shipment)
    for expected, actual, code in [(request.expected_path_version, shipment.path_version, 'PATH_VERSION_CONFLICT'),
        (request.expected_schedule_version, shipment.schedule_version, 'SCHEDULE_VERSION_CONFLICT'),
        (request.expected_destination_station_id, shipment.destination_station_id, 'DESTINATION_VERSION_CONFLICT')]:
        if expected is not None and expected != actual:
            error(code, '页面数据已变化，请刷新后重新预览')
    proposed_origin = anchor or request.origin_station_id or planned_origin(session, shipment)
    plan = None
    scheduled_trip = None
    trip_segments = None
    if request.scheduled_trip_id is not None:
        if proposed_origin is None:
            error('SCHEDULE_ORIGIN_REQUIRED', '首次入站前请明确计划起点')
        scheduled_trip = session.get(ScheduledTrip, request.scheduled_trip_id)
        if scheduled_trip is None or scheduled_trip.status != 'PLANNED':
            error('SCHEDULED_TRIP_UNAVAILABLE', '所选班次已取消或不存在')
        plan = session.get(PathPlan, scheduled_trip.line_id)
        service = session.get(LineService, scheduled_trip.service_id)
        if not service or not service.enabled or service.version != scheduled_trip.service_version or not plan or not plan.enabled:
            error('SCHEDULED_TRIP_UNAVAILABLE', '班次或运输线路已停用/更新，请重新选择有效班次')
        if request.expected_plan_version is not None and request.expected_plan_version != scheduled_trip.line_version:
            error('PREVIEW_STALE', '班次线路版本与预览不一致')
        routes, trip_segments = scheduled_trip_segments(session, scheduled_trip, proposed_origin,
                                                         shipment.destination_station_id)
        if not routes:
            error('SCHEDULED_TRIP_ROUTE_MISMATCH', '班次没有可执行的运输分段')
    elif request.plan_id is not None:
        plan = session.get(PathPlan, request.plan_id)
        if plan is None:
            error('NETWORK_RESOURCE_NOT_FOUND', '运输线路不存在', 404)
        if not plan.enabled or (request.expected_plan_version is not None and request.expected_plan_version != plan.version):
            error('PREVIEW_STALE', '运输线路已停用或版本已变化')
        routes = plan_routes(session, plan)
    elif request.route_ids is not None:
        routes = routes_for_ids(session, request.route_ids)
    else:
        routes = [session.get(TransportRoute, leg.route_id) for leg in current_legs(session, shipment.id)
                  if leg_state(session, leg)[0] not in ('ARRIVED', 'IN_TRANSIT')]
        if not routes:
            if proposed_origin is None:
                error('SCHEDULE_ORIGIN_REQUIRED', '首次入站前请明确计划起点')
            matches = list(session.scalars(select(PathPlan).where(PathPlan.enabled.is_(True),
                PathPlan.origin_station_id == proposed_origin, PathPlan.destination_station_id == shipment.destination_station_id)))
            usable = []
            for candidate in matches:
                try:
                    validate_routes(session, plan_routes(session, candidate), proposed_origin, shipment.destination_station_id)
                except NetworkError:
                    continue
                usable.append(candidate)
            if usable:
                from lines.service import list_lines
                recommended = [item for item in list_lines(session, True, proposed_origin,
                    shipment.destination_station_id) if item['usable']]
                plan = session.get(PathPlan, int(recommended[0]['id']))
                routes = plan_routes(session, plan)
            elif proposed_origin == shipment.destination_station_id:
                routes = []
            else:
                error('SCHEDULE_LINE_REQUIRED', '起终站之间没有启用的运输线路，请先配置线路')
    anchor = proposed_origin
    if anchor is None:
        error('SCHEDULE_ORIGIN_REQUIRED', '首次入站前请明确计划起点')
    if request.origin_station_id is not None and request.origin_station_id != anchor:
        error('PATH_ANCHOR_CONFLICT', '实际接续站与指定起点不一致')
    if shipment.last_scanned_station_id is None:
        source = session.get(Station, anchor)
        if not source or not source.enabled or not source.allows_first_arrival:
            error('INVALID_TRANSPORT_PATH', '计划首站必须允许首次入站')
    validate_routes(session, routes, anchor, shipment.destination_station_id)
    overrides = {l.position: l.origin_transfer_override_minutes for l in session.scalars(select(PathPlanLeg).where(PathPlanLeg.plan_id == plan.id))} if plan and scheduled_trip is None else {}
    travel_overrides = {l.position: l.travel_override_minutes for l in session.scalars(select(PathPlanLeg).where(PathPlanLeg.plan_id == plan.id))} if plan and scheduled_trip is None else {}
    anchor_arrival = None
    completed_predecessor = None
    for leg in current_legs(session, shipment.id):
        state, linked_task = leg_state(session, leg)
        if state == 'ARRIVED':
            completed_predecessor = linked_task
        else:
            break
    if frozen_task:
        _, anchor_arrival, _ = forecast(session, frozen_task, now)
        anchor_arrival = anchor_arrival or max(now, frozen_task.expected_arrival_at)
    elif completed_predecessor:
        anchor_arrival = completed_predecessor.arrived_at
    warnings, missing, rows, configuration = [], [], [], []
    windowed = shipment.earliest_handover_at is not None or shipment.latest_delivery_at is not None
    if request.legs is not None and [l.route_id for l in request.legs] != [r.id for r in routes]:
        error('INVALID_SCHEDULE_TIMES', '时间段必须与完整未来路线一一对应', 422)
    if scheduled_trip is not None and request.legs is not None:
        error('INVALID_SCHEDULE_TIMES', '已选择班次，不能单独修改分段时刻', 422)
    previous_arrival = anchor_arrival
    for i, route in enumerate(routes):
        travel_reference = travel_overrides.get(i) if travel_overrides.get(i) is not None else route.travel_minutes
        station = session.get(Station, route.origin_station_id)
        transfer = overrides.get(i)
        if transfer is None:
            transfer = station.transfer_minutes if i or anchor_arrival is not None else 0
        configuration.append({'route': route.id, 'enabled': route.enabled, 'travel': travel_reference,
            'monitor': route.delay_monitoring_enabled, 'origin_enabled': station.enabled,
            'transfer': transfer, 'destination_enabled': session.get(Station, route.destination_station_id).enabled})
        reference_departure = max(now, previous_arrival + timedelta(minutes=transfer)) if previous_arrival is not None and transfer is not None else (now if i == 0 and not frozen_task else None)
        if i == 0 and previous_arrival is None and request.planned_origin_arrival_at is not None and reference_departure is not None:
            reference_departure = max(reference_departure, request.planned_origin_arrival_at)
        if i == 0 and shipment.earliest_handover_at is not None and reference_departure is not None:
            reference_departure = max(reference_departure, shipment.earliest_handover_at)
        if reference_departure:
            round_up = bool(reference_departure.second or reference_departure.microsecond)
            reference_departure = reference_departure.replace(second=0, microsecond=0) + (timedelta(minutes=1) if round_up else timedelta())
        if trip_segments is not None:
            departure = datetime.fromisoformat(trip_segments[i]['planned_departure_at'])
            arrival = datetime.fromisoformat(trip_segments[i]['planned_arrival_at'])
        elif request.legs is not None:
            departure, arrival = request.legs[i].planned_departure_at, request.legs[i].planned_arrival_at
        else:
            departure = request.first_departure_at if i == 0 and request.first_departure_at is not None else reference_departure
            arrival = departure + timedelta(minutes=travel_reference) if departure is not None and travel_reference else None
        shared = None
        if scheduled_trip is not None and departure is not None and arrival is not None:
            shared = shared_candidate(session, route, departure, arrival, shipment.id, now,
                scheduled_trip_id=scheduled_trip.id)
        elif windowed and request.legs is None and departure is not None:
            not_before = max(departure, reference_departure or now)
            if i == 0 and shipment.earliest_handover_at is not None:
                not_before = max(not_before, shipment.earliest_handover_at)
            if i == 0 and request.planned_origin_arrival_at is not None:
                not_before = max(not_before, request.planned_origin_arrival_at)
            shared = shared_candidate(session, route, departure, arrival, shipment.id, now,
                not_before=not_before, arrive_by=shipment.latest_delivery_at, use_window=True)
            if shared is not None:
                departure, arrival = shared.planned_departure_at, shared.expected_arrival_at
        if departure is not None and (departure < now or (previous_arrival is not None and departure < previous_arrival)):
            error('INVALID_SCHEDULE_TIMES', '计划出发不能早于服务器时间或前段到达', 422)
        if i == 0 and shipment.earliest_handover_at is not None and departure is not None and departure < shipment.earliest_handover_at:
            error('INVALID_DELIVERY_WINDOW', '首段计划发车早于订单最早可交运时间', 422)
        if arrival is not None and departure is not None and arrival <= departure:
            error('INVALID_SCHEDULE_TIMES', '到达必须晚于本段出发', 422)
        if departure is not None and arrival is not None:
            travel = int((arrival-departure).total_seconds()/60)
            gap = int((departure-previous_arrival).total_seconds()/60) if previous_arrival else 0
            if travel > 525600 or gap > 525600:
                error('INVALID_SCHEDULE_TIMES', '已审核耗时不能超过一年', 422)
            if travel_reference and travel < travel_reference:
                warnings.append({'code': f'TRAVEL_BELOW_REFERENCE:{i}', 'message': f'{route.code} 运输时间短于参考值'})
            if previous_arrival is not None and transfer is not None and gap < transfer:
                warnings.append({'code': f'TRANSFER_BELOW_REFERENCE:{i}', 'message': f'{station.code} 中转时间短于参考值'})
        else:
            missing.append({'position': i, 'message': '请补全本段出发和到达时间'})
            travel, gap = None, 0
        if shared is None and departure and arrival and scheduled_trip is None:
            shared = shared_candidate(session, route, departure, arrival, shipment.id, now)
        if i == len(routes) - 1 and shipment.latest_delivery_at is not None and arrival is not None and arrival > shipment.latest_delivery_at:
            missing.append({'position': i, 'message': '预计到达晚于订单最晚送达时间'})
        rows.append({'position': i, 'route_id': str(route.id), 'route_code': route.code,
            'origin_station_id': str(route.origin_station_id), 'destination_station_id': str(route.destination_station_id),
            'planned_departure_at': departure.isoformat() if departure else None,
            'planned_arrival_at': arrival.isoformat() if arrival else None,
            'travel_reference_minutes': travel_reference, 'transfer_reference_minutes': transfer,
            'approved_transfer_minutes': gap, 'planned_travel_minutes': travel,
            'shared_task_id': str(shared.id) if shared else None,
            'shared_task_revision': shared.schedule_revision if shared else None,
            'shared_members': [str(e.shipment_id) for e in members(session, shared.id)] if shared else []})
        previous_arrival = arrival
    if request.planned_origin_arrival_at is not None and rows and rows[0]['planned_departure_at'] and request.planned_origin_arrival_at > datetime.fromisoformat(rows[0]['planned_departure_at']):
        error('INVALID_SCHEDULE_TIMES', '计划首站入站不能晚于首段出发', 422)
    replacements = []
    for entry in session.scalars(select(TaskShipment).where(TaskShipment.shipment_id == shipment.id, TaskShipment.association_state != 'RELEASED')):
        task = session.get(TransportTask, entry.task_id)
        if task.status in UNSTARTED:
            replacements.append({'id': entry.id, 'task_id': task.id, 'revision': task.schedule_revision,
                                 'shared': len(members(session, task.id)) > 1})
    return {'shipment_id': str(shipment.id), 'path_version': shipment.path_version,
        'schedule_version': shipment.schedule_version, 'destination_station_id': str(shipment.destination_station_id),
        'anchor_station_id': str(anchor), 'stage': shipment.stage,
        'frozen_task_id': str(frozen_task.id) if frozen_task else None,
        'frozen_task_revision': frozen_task.schedule_revision if frozen_task else None,
        'anchor_arrival_at': anchor_arrival.isoformat() if anchor_arrival else None,
        'line_id': str(plan.id) if plan else None,
        'line_version': scheduled_trip.line_version if scheduled_trip else (plan.version if plan else None),
        'scheduled_trip_id': str(scheduled_trip.id) if scheduled_trip else None,
        'source_plan_id': str(plan.id) if plan else None,
        'source_plan_version': scheduled_trip.line_version if scheduled_trip else (plan.version if plan else None),
        'configuration': configuration, 'legs': rows, 'warnings': warnings, 'missing': missing,
        'replacements': replacements, 'planned_origin_arrival_at': request.planned_origin_arrival_at.isoformat() if request.planned_origin_arrival_at else None,
        'can_confirm': not missing and not any(e['shared'] for e in replacements)}


def preview_schedule(session, shipment_id, request):
    shipment = parcel(session, shipment_id)
    evaluated_at = business_time.server_now()
    value = make_preview(session, shipment, request, evaluated_at)
    normalized = request.model_dump(mode='json')
    normalized.update(origin_station_id=int(value['anchor_station_id']),
        line_id=int(value['line_id']) if value['line_id'] and not value['scheduled_trip_id'] else None,
        expected_line_version=value['line_version'] if not value['scheduled_trip_id'] else None,
        scheduled_trip_id=int(value['scheduled_trip_id']) if value['scheduled_trip_id'] else None,
        route_ids=None if value['line_id'] else [int(r['route_id']) for r in value['legs']],
        legs=None if value['scheduled_trip_id'] else [
            {'route_id': int(r['route_id']), 'planned_departure_at': r['planned_departure_at'],
             'planned_arrival_at': r['planned_arrival_at']} for r in value['legs']])
    value['preview_token'] = sign('schedule', {'shipment_id': shipment_id, 'request': normalized,
        'evaluated_at': evaluated_at.isoformat(), 'fingerprint': digest(value)})
    return value


def release_entry(session, entry, now, reason):
    entry.association_state = 'RELEASED'
    entry.released_at = now
    entry.release_reason = reason


def cancel_empty(session, task, now, reason):
    if not members(session, task.id):
        task.status = 'CANCELLED'
        task.cancelled_at = now
        task.cancel_reason = reason
    else:
        recompute_task_state(session, task)


def confirm_schedule(session, shipment_id, request, key):
    request_hash = digest({'action': 'CONFIRM_SCHEDULE', 'shipment_id': shipment_id, 'body': request.model_dump(mode='json')})
    with session.begin():
        clock, replay = time_and_replay(session, key, request_hash)
        if replay:
            return business_time.normalize_cached_response(replay.response_body)
        token = unsign(request.preview_token, 'schedule')
        if token['shipment_id'] != shipment_id:
            error('PREVIEW_STALE', '预览不属于该运单')
        shipment = parcel(session, shipment_id)
        normalized = SchedulePreviewRequest(**token['request'])
        value = make_preview(session, shipment, normalized, datetime.fromisoformat(token['evaluated_at']))
        if digest(value) != token['fingerprint']:
            error('PREVIEW_STALE', '路线、计划或共享任务已变化，请重新预览')
        if any(row['planned_departure_at'] and datetime.fromisoformat(row['planned_departure_at']) < clock
               for row in value['legs']):
            error('PREVIEW_EXPIRED', '计划出发时间已过去，请重新预览并确认')
        if value['missing']:
            error('INVALID_SCHEDULE_TIMES', '请补全全部时间', 422)
        if any(entry['shared'] for entry in value['replacements']):
            error('SHARED_TASK_REVIEW_REQUIRED', '未来任务由多运单共享，请先审核取消整趟任务')
        if not {w['code'] for w in value['warnings']}.issubset(request.acknowledged_warning_codes):
            error('SCHEDULE_WARNING_NOT_ACKNOWLEDGED', '请确认短于参考耗时的提醒')
        parent = OperationLog(idempotency_key=key, request_hash=request_hash, action='CONFIRM_SCHEDULE',
            resource_type='SHIPMENT', resource_id=shipment_id, before_data={'schedule_version': shipment.schedule_version},
            after_data=None, response_body={}, response_status=200, occurred_at=clock)
        session.add(parent); session.flush()
        for replacement in value['replacements']:
            entry = session.get(TaskShipment, replacement['id'])
            task = session.get(TransportTask, entry.task_id)
            release_entry(session, entry, clock, request.reason)
            task.schedule_revision += 1
            session.flush(); cancel_empty(session, task, clock, request.reason)
            sub_log(session, parent, 'REPLACE_PLANNED_TASK', entry, {'association_id': str(entry.id), 'state': 'RELEASED'})
        session.flush()
        routes = routes_for_ids(session, [int(r['route_id']) for r in value['legs']])
        plan = session.get(PathPlan, int(value['source_plan_id'])) if value['source_plan_id'] else None
        scheduled_trip = session.get(ScheduledTrip, int(value['scheduled_trip_id'])) if value['scheduled_trip_id'] else None
        if shipment.last_scanned_station_id is None:
            shipment.planned_origin_station_id = int(value['anchor_station_id'])
        save_path(session, shipment, routes, clock, request.reason, plan)
        shipment.scheduling_mode = 'REVIEWED'
        shipment.schedule_version += 1
        shipment.schedule_status = 'CONFIRMED'
        shipment.schedule_reason = None
        all_legs = current_legs(session, shipment_id)
        prefix = [leg for leg in all_legs if leg_state(session, leg)[0] in ('ARRIVED', 'IN_TRANSIT')]
        future = [leg for leg in all_legs if leg not in prefix]
        predecessor = None
        if prefix:
            predecessor = session.scalar(select(TaskShipment).where(TaskShipment.path_leg_id == prefix[-1].id).order_by(TaskShipment.id.desc()).limit(1))
        for leg, row in zip(future, value['legs']):
            leg.travel_reference_minutes = row['travel_reference_minutes']
            leg.origin_transfer_reference_minutes = row['transfer_reference_minutes']
            departure, arrival = datetime.fromisoformat(row['planned_departure_at']), datetime.fromisoformat(row['planned_arrival_at'])
            route = session.get(TransportRoute, leg.route_id)
            task = session.get(TransportTask, int(row['shared_task_id'])) if row['shared_task_id'] else None
            if task is None:
                task = TransportTask(route_id=leg.route_id, scheduling_source='PLAN', planned_departure_at=departure,
                    expected_arrival_at=arrival, planned_travel_minutes=row['planned_travel_minutes'],
                    delay_monitoring_enabled=route.delay_monitoring_enabled,
                    scheduled_trip_id=scheduled_trip.id if scheduled_trip else None,
                    status='WAITING_PREDECESSOR' if predecessor else 'WAITING_CARGO')
                session.add(task); session.flush(); session.refresh(task)
            else:
                task.schedule_revision += 1
            entry = TaskShipment(task_id=task.id, shipment_id=shipment_id, path_leg_id=leg.id, association_state='PLANNED',
                schedule_version=shipment.schedule_version, predecessor_association_id=predecessor.id if predecessor else None,
                approved_transfer_minutes=row['approved_transfer_minutes'], planned_origin_arrival_at=normalized.planned_origin_arrival_at if predecessor is None else None)
            if shipment.stage == 'AT_STATION' and predecessor is None and route.origin_station_id == shipment.last_scanned_station_id:
                entry.association_state = 'ACTIVE'; entry.ready_at = clock
            elif predecessor is not None and session.get(TransportTask, predecessor.task_id).status == 'ARRIVED' and route.origin_station_id == shipment.last_scanned_station_id:
                entry.association_state = 'ACTIVE'
                entry.ready_at = session.get(TransportTask, predecessor.task_id).arrived_at + timedelta(minutes=entry.approved_transfer_minutes)
            session.add(entry); session.flush()
            recompute_task_state(session, task)
            sub_log(session, parent, 'GENERATE_PLANNED_TASK', entry, {'association_id': str(entry.id), 'task_id': str(task.id), 'state': entry.association_state})
            predecessor = entry
        session.flush()
        snapshot = []
        for leg in all_legs:
            entry = session.scalar(select(TaskShipment).where(TaskShipment.path_leg_id == leg.id).order_by(TaskShipment.id.desc()).limit(1))
            task = session.get(TransportTask, entry.task_id) if entry else None
            route = session.get(TransportRoute, leg.route_id)
            snapshot.append({'path_leg_id': str(leg.id), 'position': leg.position, 'route_id': str(leg.route_id), 'route_code': route.code,
                'origin_station_id': str(route.origin_station_id), 'destination_station_id': str(route.destination_station_id),
                'task_id': str(task.id) if task else None, 'association_id': str(entry.id) if entry else None,
                'planned_departure_at': task.planned_departure_at.isoformat() if task and task.planned_departure_at else None,
                'planned_arrival_at': task.expected_arrival_at.isoformat() if task else None,
                'planned_travel_minutes': task.planned_travel_minutes if task else None,
                'approved_transfer_minutes': entry.approved_transfer_minutes if entry else None,
                'planned_origin_arrival_at': entry.planned_origin_arrival_at.isoformat() if entry and entry.planned_origin_arrival_at else None,
                'travel_reference_minutes': leg.travel_reference_minutes, 'transfer_reference_minutes': leg.origin_transfer_reference_minutes})
        origin = int(snapshot[0]['origin_station_id']) if snapshot else int(value['anchor_station_id'])
        session.add(ShipmentScheduleVersion(shipment_id=shipment_id, version=shipment.schedule_version, path_version=shipment.path_version,
            origin_station_id=origin, destination_station_id=shipment.destination_station_id,
            scheduled_trip_id=scheduled_trip.id if scheduled_trip else None, source_plan_id=plan.id if plan else None,
            source_plan_version=plan.version if plan else None, reason=request.reason, legs=snapshot, operation_id=parent.id, occurred_at=clock))
        if not future and shipment.stage == 'AT_STATION' and shipment.last_scanned_station_id == shipment.destination_station_id:
            shipment.schedule_status = 'COMPLETED'
        session.flush()
        body = schedule_body(session, shipment)
        parent.after_data = {'schedule_version': shipment.schedule_version, 'task_ids': [r['task_id'] for r in body['legs']]}
        parent.response_body = body
        return body


def schedule_body(session, shipment):
    version = session.scalar(select(ShipmentScheduleVersion).where(ShipmentScheduleVersion.shipment_id == shipment.id,
        ShipmentScheduleVersion.version == shipment.schedule_version))
    now = business_time.server_now()
    rows, cache, configuration_risks = [], {}, []
    for saved in version.legs if version else []:
        row = dict(saved)
        task = session.get(TransportTask, int(row['task_id'])) if row.get('task_id') else None
        entry = session.get(TaskShipment, int(row['association_id'])) if row.get('association_id') else None
        route = session.get(TransportRoute, int(row['route_id']))
        if route and (not route.enabled or not session.get(Station, route.origin_station_id).enabled
                      or not session.get(Station, route.destination_station_id).enabled):
            configuration_risks.append({'route_id': row['route_id'], 'message': '线路或站点已停用，已有任务可继续；新安排需要重新审核'})
        if task:
            row.update(task_extra(session, task, now, cache))
            row.update(task_no=task.task_no, task_status=task.status,
                actual_departure_at=task.departed_at.isoformat() if task.departed_at else None,
                actual_arrival_at=task.arrived_at.isoformat() if task.arrived_at else None,
                association_state=entry.association_state if entry else None,
                ready_at=entry.ready_at.isoformat() if entry and entry.ready_at else None)
        rows.append(row)
    return {'shipment_id': str(shipment.id), 'scheduling_mode': shipment.scheduling_mode,
        'status': shipment.schedule_status, 'reason': shipment.schedule_reason,
        'path_version': shipment.path_version, 'version': shipment.schedule_version,
        'line_id': str(version.source_plan_id) if version and version.source_plan_id else None,
        'line_version': version.source_plan_version if version else None,
        'scheduled_trip_id': str(version.scheduled_trip_id) if version and version.scheduled_trip_id else None,
        'source_plan_id': str(version.source_plan_id) if version and version.source_plan_id else None,
        'source_plan_version': version.source_plan_version if version else None,
        'origin_station_id': str(version.origin_station_id) if version else (str(planned_origin(session, shipment)) if planned_origin(session, shipment) else None),
        'destination_station_id': str(shipment.destination_station_id), 'legs': rows,
        'configuration_risks': configuration_risks, 'server_time': now.isoformat()}


def schedule_history(session, shipment_id, page, page_size):
    parcel(session, shipment_id)
    total = session.scalar(select(func.count()).select_from(ShipmentScheduleVersion).where(ShipmentScheduleVersion.shipment_id == shipment_id)) or 0
    versions = session.scalars(select(ShipmentScheduleVersion).where(ShipmentScheduleVersion.shipment_id == shipment_id)
        .order_by(ShipmentScheduleVersion.version.desc()).offset((page-1)*page_size).limit(page_size))
    return {'items': [{'version': v.version, 'path_version': v.path_version, 'reason': v.reason,
        'occurred_at': v.occurred_at.isoformat(), 'legs': v.legs,
        'line_id': str(v.source_plan_id) if v.source_plan_id else None,
        'line_version': v.source_plan_version,
        'scheduled_trip_id': str(v.scheduled_trip_id) if v.scheduled_trip_id else None,
        'source_plan_id': str(v.source_plan_id) if v.source_plan_id else None,
        'source_plan_version': v.source_plan_version} for v in versions], 'total': total, 'page': page, 'page_size': page_size}


def cancel_impact(session, task):
    affected = {e.id: e for e in members(session, task.id)}
    frontier = list(affected)
    while frontier:
        next_entries = list(session.scalars(select(TaskShipment).where(TaskShipment.predecessor_association_id.in_(frontier),
            TaskShipment.association_state != 'RELEASED')))
        frontier = []
        for entry in next_entries:
            if entry.id not in affected:
                affected[entry.id] = entry; frontier.append(entry.id)
    return [{'association_id': str(e.id), 'shipment_id': str(e.shipment_id), 'task_id': str(e.task_id),
        'task_revision': session.get(TransportTask, e.task_id).schedule_revision,
        'task_status': session.get(TransportTask, e.task_id).status, 'association_state': e.association_state}
        for e in sorted(affected.values(), key=lambda a:a.id)]


def preview_cancel(session, task_id, reason):
    task = session.get(TransportTask, task_id)
    if not task:
        error('TASK_NOT_FOUND', '任务不存在', 404)
    if task.status not in UNSTARTED:
        error('INVALID_TASK_STATE', '只能取消未发车的任务')
    impact = cancel_impact(session, task)
    body = {'task_id': str(task_id), 'schedule_revision': task.schedule_revision, 'reason': reason, 'impact': impact}
    return {**body, 'cancel_token': sign('cancel', body)}


def cancel_planned(session, task, request, clock, key, request_hash):
    if task.status == 'CANCELLED':
        if task.cancel_reason != request.reason:
            error('INVALID_TASK_STATE', '取消原因与原记录不同')
        from transport.service import get_transport_task
        body = get_transport_task(session, task.id)
        session.add(OperationLog(idempotency_key=key, request_hash=request_hash, action='CANCEL_TRANSPORT_TASK',
            resource_type='TRANSPORT_TASK', resource_id=task.id, before_data=None, after_data=None,
            response_body=body, response_status=200, occurred_at=clock))
        return body
    if task.status not in UNSTARTED:
        error('INVALID_TASK_STATE', '只能取消未发车任务')
    if request.expected_schedule_revision != task.schedule_revision or not request.cancel_token:
        error('TASK_MEMBERSHIP_CONFLICT', '请预览最新的整批取消影响')
    proposed = unsign(request.cancel_token, 'cancel')
    current = {'task_id': str(task.id), 'schedule_revision': task.schedule_revision, 'reason': request.reason,
               'impact': cancel_impact(session, task)}
    if proposed != current:
        error('TASK_MEMBERSHIP_CONFLICT', '成员或下游安排已变化，请重新审核')
    parent = OperationLog(idempotency_key=key, request_hash=request_hash, action='CANCEL_TRANSPORT_TASK',
        resource_type='TRANSPORT_TASK', resource_id=task.id, before_data=current, after_data=None,
        response_body={}, response_status=200, occurred_at=clock)
    session.add(parent); session.flush()
    touched = set()
    for item in current['impact']:
        entry = session.get(TaskShipment, int(item['association_id']))
        other_task = session.get(TransportTask, entry.task_id)
        if other_task.status not in UNSTARTED:
            error('INVALID_TASK_DEPENDENCY', '下游已有执行记录，不能取消此链路')
        release_entry(session, entry, clock, request.reason)
        shipment = parcel(session, entry.shipment_id)
        shipment.schedule_status = 'NEEDS_RECONFIRMATION'; shipment.schedule_reason = request.reason
        touched.add(other_task.id)
        sub_log(session, parent, 'INVALIDATE_DOWNSTREAM_TASK', entry, {'association_id': str(entry.id), 'state': 'RELEASED'})
    session.flush()
    for task_id in sorted(touched):
        other = session.get(TransportTask, task_id)
        other.schedule_revision += 1
        cancel_empty(session, other, clock, request.reason)
    session.flush()
    from transport.service import get_transport_task
    body = get_transport_task(session, task.id)
    body['cancel_impact'] = current['impact']
    parent.after_data = {'task_ids': [str(i) for i in sorted(touched)]}; parent.response_body = body
    return body
