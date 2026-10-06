from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Path
from sqlalchemy.orm import Session
from db import get_db
from errors import IdempotencyKeyReusedError, ShipmentNotFoundError
from scheduling.schemas import SchedulePreviewRequest, ScheduleConfirmRequest, CancelPreviewRequest
from scheduling.schemas import SchedulePreviewResponse, ScheduleResponse, ScheduleHistoryResponse, CancelPreviewResponse
from scheduling.service import preview_schedule, confirm_schedule, schedule_body, schedule_history, parcel, preview_cancel

router = APIRouter(prefix='/api/v1', tags=['scheduling'])
DB = Annotated[Session, Depends(get_db)]
Key = Annotated[UUID, Header(alias='Idempotency-Key')]
ResourceId = Annotated[int, Path(gt=0)]


def call(fn, *args):
    try:
        return fn(*args)
    except ShipmentNotFoundError:
        raise HTTPException(404, 'Shipment not found')
    except IdempotencyKeyReusedError:
        raise HTTPException(409, 'Idempotency-Key reused with different content')


@router.post('/shipments/{shipment_id}/schedule/preview', response_model=SchedulePreviewResponse)
def preview(shipment_id: ResourceId, request: SchedulePreviewRequest, session: DB):
    return call(preview_schedule, session, shipment_id, request)


@router.post('/shipments/{shipment_id}/schedule/confirm', response_model=ScheduleResponse)
def confirm(shipment_id: ResourceId, request: ScheduleConfirmRequest, idempotency_key: Key, session: DB):
    return call(confirm_schedule, session, shipment_id, request, idempotency_key)


@router.get('/shipments/{shipment_id}/schedule', response_model=ScheduleResponse)
def schedule(shipment_id: ResourceId, session: DB):
    return call(lambda: schedule_body(session, parcel(session, shipment_id)))


@router.get('/shipments/{shipment_id}/schedule-history', response_model=ScheduleHistoryResponse)
def history(shipment_id: ResourceId, session: DB, page: Annotated[int, Query(ge=1)]=1,
            page_size: Annotated[int, Query(ge=1, le=100)]=20):
    return call(schedule_history, session, shipment_id, page, page_size)


@router.post('/transport-tasks/{task_id}/cancel-preview', response_model=CancelPreviewResponse)
def cancellation_preview(task_id: ResourceId, request: CancelPreviewRequest, session: DB):
    return call(preview_cancel, session, task_id, request.reason)


@router.get('/shipments/{shipment_id}/schedule/initial-preview', response_model=SchedulePreviewResponse)
def initial_preview(shipment_id: ResourceId, session: DB):
    return call(preview_schedule, session, shipment_id, SchedulePreviewRequest())
