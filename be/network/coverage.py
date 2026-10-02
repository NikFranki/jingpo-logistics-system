"""Delivery coverage configuration and deterministic destination resolution."""
import hashlib
import json
from typing import Literal

from sqlalchemy import select
from pydantic import BaseModel, ConfigDict, Field

import business_time
from errors import IdempotencyKeyReusedError, NetworkError
from models import City, District, Province, Station, StationServiceArea, OperationLog, Order, Shipment
from network.service import require_station, station_body
from network.schemas import StationResponse


class DestinationMatchResponse(BaseModel):
    status: Literal['MATCHED', 'NOT_FOUND', 'CONFLICT', 'ADDRESS_REQUIRED', 'INVALID_ADDRESS', 'EXISTING_SHIPMENT']
    reason: str | None
    destination_station: StationResponse | None
    matched_level: Literal['PROVINCE', 'CITY', 'DISTRICT'] | None
    service_area_id: str | None


class ServiceAreaCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    province_id: int = Field(gt=0, strict=True)
    city_id: int | None = Field(default=None, gt=0, strict=True)
    district_id: int | None = Field(default=None, gt=0, strict=True)
    enabled: bool = Field(default=True, strict=True)


class ServiceAreaUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = Field(strict=True)


class ServiceAreaResponse(BaseModel):
    id: str
    station_id: str
    province_id: str
    city_id: str | None
    district_id: str | None
    province_name: str
    city_name: str | None
    district_name: str | None
    level: str
    enabled: bool


def validate_scope(session, province_id, city_id, district_id):
    rows = [session.get(model, key) if key is not None else None
            for model, key in zip((Province, City, District), (province_id, city_id, district_id))]
    if any(key is not None and (row is None or not row.enabled)
           for key, row in zip((province_id, city_id, district_id), rows)):
        raise NetworkError('INVALID_ADDRESS_REGION', '服务区域不存在或已停用', 422)
    province, city, district = rows
    if city and city.province_id != province.id:
        raise NetworkError('REGION_PARENT_MISMATCH', '城市不属于所选省级区域', 422)
    if district and (district.province_id != province.id or district.city_id != city_id):
        raise NetworkError('REGION_PARENT_MISMATCH', '区县不属于所选省级区域或城市', 422)


def area_body(session, area):
    return dict(id=str(area.id), station_id=str(area.station_id),
                province_id=str(area.province_id), city_id=str(area.city_id) if area.city_id else None,
                district_id=str(area.district_id) if area.district_id else None,
                province_name=session.get(Province, area.province_id).name,
                city_name=session.get(City, area.city_id).name if area.city_id else None,
                district_name=session.get(District, area.district_id).name if area.district_id else None,
                level='DISTRICT' if area.district_id else 'CITY' if area.city_id else 'PROVINCE',
                enabled=area.enabled)


def list_areas(session, station_id, enabled=None):
    require_station(session, station_id)
    query = select(StationServiceArea).where(StationServiceArea.station_id == station_id)
    if enabled is not None:
        query = query.where(StationServiceArea.enabled == enabled)
    return [area_body(session, row) for row in session.scalars(query.order_by(StationServiceArea.id))]


def ensure_scope_available(session, area):
    validate_scope(session, area.province_id, area.city_id, area.district_id)
    station = require_station(session, area.station_id)
    if not station.enabled or not station.allows_delivery:
        raise NetworkError('INVALID_NETWORK_CONFIGURATION', '服务范围需要启用且允许派送的站点')
    conflict = session.scalar(select(StationServiceArea.id).where(
        StationServiceArea.enabled.is_(True), StationServiceArea.id != (area.id or 0),
        StationServiceArea.province_id == area.province_id,
        StationServiceArea.city_id.is_not_distinct_from(area.city_id),
        StationServiceArea.district_id.is_not_distinct_from(area.district_id)).limit(1))
    if conflict:
        raise NetworkError('SERVICE_AREA_CONFLICT', '此区域已有启用的服务范围，请先停用原配置')


def write_area(session, station_id, request, key, area_id=None):
    action = 'CREATE_STATION_SERVICE_AREA' if area_id is None else 'UPDATE_STATION_SERVICE_AREA'
    changes = request.model_dump()
    digest = hashlib.sha256(json.dumps(dict(action=action, station_id=station_id,
        area_id=area_id, body=changes), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with session.begin():
        now = business_time.begin_business_write(session)
        replay = session.scalar(select(OperationLog).where(OperationLog.idempotency_key == key))
        if replay:
            if replay.request_hash != digest:
                raise IdempotencyKeyReusedError
            return replay.response_body
        require_station(session, station_id)
        before = None
        if area_id is None:
            area = StationServiceArea(station_id=station_id, **changes)
        else:
            area = session.scalar(select(StationServiceArea).where(
                StationServiceArea.id == area_id, StationServiceArea.station_id == station_id).with_for_update())
            if area is None:
                raise NetworkError('SERVICE_AREA_NOT_FOUND', '服务范围不存在', 404)
            before = area_body(session, area)
            area.enabled = request.enabled
        with session.no_autoflush:
            if area.enabled:
                ensure_scope_available(session, area)
            elif area_id is None:
                validate_scope(session, area.province_id, area.city_id, area.district_id)
        session.add(area)
        area.updated_at = now
        session.flush()
        body = area_body(session, area)
        session.add(OperationLog(idempotency_key=key, request_hash=digest, action=action,
            resource_type='STATION', resource_id=station_id, before_data=before,
            after_data=body, response_body=body, response_status=201 if area_id is None else 200,
            occurred_at=now))
        return body


def match_destination(session, order):
    p, c, d = (getattr(order, 'recipient_' + level + '_id') for level in ('province', 'city', 'district'))
    empty = dict(destination_station=None, matched_level=None, service_area_id=None)
    if p is None:
        return dict(status='ADDRESS_REQUIRED', reason='订单缺少收件省市区，请先完善地址或为旧订单手动选择目的站', **empty)
    try:
        validate_scope(session, p, c, d)
    except NetworkError as exc:
        return dict(status='INVALID_ADDRESS', reason=str(exc), **empty)
    rows = session.execute(select(StationServiceArea, Station).join(Station).where(
        StationServiceArea.enabled.is_(True), StationServiceArea.province_id == p,
        Station.enabled.is_(True), Station.allows_delivery.is_(True))).all()
    matches = []
    for area, station in rows:
        if area.district_id is not None:
            if area.district_id != d or area.city_id != c:
                continue
            rank = 3
        elif area.city_id is not None:
            if area.city_id != c:
                continue
            rank = 2
        else:
            rank = 1
        # Ignore a scope whose own region has been disabled.
        if area.city_id and not session.get(City, area.city_id).enabled:
            continue
        if area.district_id and not session.get(District, area.district_id).enabled:
            continue
        matches.append((rank, area, station))
    if not matches:
        return dict(status='NOT_FOUND', reason='收件区域尚未配置可派送的服务站点', **empty)
    rank = max(item[0] for item in matches)
    best = [item for item in matches if item[0] == rank]
    if len({item[2].id for item in best}) != 1:
        return dict(status='CONFLICT', reason='收件区域匹配到多个目的站，请修正服务范围配置', **empty)
    _, area, station = best[0]
    return dict(status='MATCHED', reason=None, destination_station=station_body(station),
                matched_level='DISTRICT' if rank == 3 else 'CITY' if rank == 2 else 'PROVINCE',
                service_area_id=str(area.id))


def require_matched_destination(session, order):
    result = match_destination(session, order)
    if result['status'] != 'MATCHED':
        raise NetworkError('DESTINATION_' + result['status'], result['reason'])
    return int(result['destination_station']['id'])


def order_destination_preview(session, order_id):
    order = session.get(Order, order_id)
    if order is None:
        raise NetworkError('ORDER_NOT_FOUND', '订单不存在', 404)
    existing = session.scalar(select(Shipment).where(Shipment.order_id == order_id))
    if existing:
        return dict(status='EXISTING_SHIPMENT', reason=None,
                    destination_station=station_body(session.get(Station, existing.destination_station_id)),
                    matched_level=None, service_area_id=None)
    return match_destination(session, order)
