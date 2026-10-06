from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session
from db import get_db
from models import Shipment
from errors import IdempotencyKeyReusedError, ShipmentNotFoundError
from planning.schemas import (PathPlanCreateRequest,PathPlanUpdateRequest,PathPlanResponse,
    ShipmentPathUpdateRequest,ShipmentTransportPathResponse,PathOptionsResponse,PathHistoryResponse)
from planning.service import (list_plans,write_plan,shipment_path_body,matching_plans,
    write_shipment_path,path_history,path_options_body)

router = APIRouter(prefix="/api/v1",tags=["transport-paths"])
DB = Annotated[Session,Depends(get_db)]
Key = Annotated[UUID,Header(alias="Idempotency-Key")]


def write(fn,*args):
    try: return fn(*args)
    except IdempotencyKeyReusedError: raise HTTPException(409,"Idempotency-Key was reused with different content")
    except ShipmentNotFoundError: raise HTTPException(404,"Shipment not found")


def require_shipment(session,shipment_id):
    shipment = session.get(Shipment,shipment_id)
    if shipment is None: raise HTTPException(404,"Shipment not found")
    return shipment

@router.get('/path-plans',response_model=list[PathPlanResponse],deprecated=True)
def read_plans(session:DB,enabled:bool|None=None,origin_station_id:int|None=None,destination_station_id:int|None=None):
    return list_plans(session,enabled,origin_station_id,destination_station_id)

@router.post('/path-plans',response_model=PathPlanResponse,status_code=201,deprecated=True)
def create_plan(request:PathPlanCreateRequest,idempotency_key:Key,session:DB):
    return write(write_plan,session,request,idempotency_key)

@router.patch('/path-plans/{plan_id}',response_model=PathPlanResponse,deprecated=True)
def update_plan(plan_id:int,request:PathPlanUpdateRequest,idempotency_key:Key,session:DB):
    return write(write_plan,session,request,idempotency_key,plan_id)

@router.get('/shipments/{shipment_id}/path',response_model=ShipmentTransportPathResponse)
def read_path(shipment_id:int,session:DB):
    return shipment_path_body(session,require_shipment(session,shipment_id))

@router.get('/shipments/{shipment_id}/path-options',response_model=PathOptionsResponse)
def read_options(shipment_id:int,session:DB):
    shipment = require_shipment(session,shipment_id)
    return path_options_body(session, shipment)

@router.put('/shipments/{shipment_id}/path',response_model=ShipmentTransportPathResponse)
def replace_future_path(shipment_id:int,request:ShipmentPathUpdateRequest,idempotency_key:Key,session:DB):
    return write(write_shipment_path,session,shipment_id,request,idempotency_key)

@router.get('/shipments/{shipment_id}/path-history',response_model=PathHistoryResponse)
def read_history(shipment_id:int,session:DB,page:Annotated[int,Query(ge=1)]=1,
                 page_size:Annotated[int,Query(ge=1,le=100)]=20):
    require_shipment(session,shipment_id)
    items,total = path_history(session,shipment_id,page,page_size)
    return {"items":items,"total":total,"page":page,"page_size":page_size}
