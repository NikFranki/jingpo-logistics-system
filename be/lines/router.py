from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from pydantic import ValidationError
from db import get_db
from errors import IdempotencyKeyReusedError, NetworkError
from models import PathPlan
from planning.router import require_shipment
from lines.schemas import LineCreateRequest, LineUpdateRequest, LineResponse, LineOptionsResponse, LineListResponse
from lines.service import list_lines, line_body, write_line, line_options

router = APIRouter(prefix='/api/v1', tags=['transport-lines'])
DB = Annotated[Session, Depends(get_db)]
Key = Annotated[UUID, Header(alias='Idempotency-Key')]


@router.get('/transport-lines', response_model=LineListResponse)
def read_lines(session: DB, enabled: bool | None = None, origin_station_id: int | None = None,
               destination_station_id: int | None = None,
               page: Annotated[int, Query(ge=1)] = 1,
               page_size: Annotated[int, Query(ge=1, le=100)] = 20):
    query = select(PathPlan)
    for field, value in [(PathPlan.enabled, enabled), (PathPlan.origin_station_id, origin_station_id),
                         (PathPlan.destination_station_id, destination_station_id)]:
        if value is not None:
            query = query.where(field == value)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(query.order_by(PathPlan.id).offset((page - 1) * page_size).limit(page_size))
    return dict(items=[line_body(session, p) for p in rows], total=total, page=page, page_size=page_size)


@router.get('/transport-lines/{line_id}', response_model=LineResponse)
def read_line(line_id: int, session: DB):
    line = session.get(PathPlan, line_id)
    if line is None:
        raise NetworkError('TRANSPORT_LINE_NOT_FOUND', '运输线路不存在', 404)
    return line_body(session, line)


def write(session, request, key, line_id=None):
    try:
        return write_line(session, request, key, line_id)
    except ValidationError:
        raise NetworkError('INVALID_TRANSPORT_LINE', '线路站点、分段时间或中转配置不完整', 422)
    except IdempotencyKeyReusedError:
        raise HTTPException(409, 'Idempotency-Key was reused with different content')


@router.post('/transport-lines', response_model=LineResponse, status_code=201)
def create_line(request: LineCreateRequest, idempotency_key: Key, session: DB):
    return write(session, request, idempotency_key)


@router.patch('/transport-lines/{line_id}', response_model=LineResponse)
def update_line(line_id: int, request: LineUpdateRequest, idempotency_key: Key, session: DB):
    return write(session, request, idempotency_key, line_id)


@router.get('/shipments/{shipment_id}/line-options', response_model=LineOptionsResponse)
def read_line_options(shipment_id: int, session: DB):
    return line_options(session, require_shipment(session, shipment_id))
