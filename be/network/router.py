from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from db import get_db
from errors import IdempotencyKeyReusedError
from network.schemas import (StationResponse, TransportRouteResponse, StationCreateRequest,
    StationUpdateRequest, RouteCreateRequest, RouteUpdateRequest)
from network.service import list_stations, list_routes, station_body, route_body, write_network

router = APIRouter(prefix="/api/v1", tags=["network"])
DB = Annotated[Session, Depends(get_db)]
Key = Annotated[UUID, Header(alias="Idempotency-Key")]

@router.get("/stations", response_model=list[StationResponse])
def read_stations(session: DB, enabled: bool | None = None):
    return [station_body(station) for station in list_stations(session, enabled)]

@router.get("/routes", response_model=list[TransportRouteResponse])
def read_routes(session: DB, enabled: bool | None = None):
    return [route_body(session, route) for route, _, _ in list_routes(session, enabled)]

def write(session, kind, request, key, resource_id=None):
    try:
        return write_network(session, kind, request, key, resource_id)
    except IdempotencyKeyReusedError:
        raise HTTPException(409, "Idempotency-Key was reused with different content")

@router.post("/stations", response_model=StationResponse, status_code=201)
def create_station(request: StationCreateRequest, idempotency_key: Key, session: DB):
    return write(session, "STATION", request, idempotency_key)

@router.patch("/stations/{station_id}", response_model=StationResponse)
def update_station(station_id: int, request: StationUpdateRequest, idempotency_key: Key, session: DB):
    return write(session, "STATION", request, idempotency_key, station_id)

@router.post("/routes", response_model=TransportRouteResponse, status_code=201)
def create_route(request: RouteCreateRequest, idempotency_key: Key, session: DB):
    return write(session, "ROUTE", request, idempotency_key)

@router.patch("/routes/{route_id}", response_model=TransportRouteResponse)
def update_route(route_id: int, request: RouteUpdateRequest, idempotency_key: Key, session: DB):
    return write(session, "ROUTE", request, idempotency_key, route_id)
