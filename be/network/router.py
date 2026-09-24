from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db import get_db
from models import Station
from network.schemas import (
    StationResponse,
    TransportRouteResponse,
)
from network.service import (
    list_routes as list_routes_service,
    list_stations as list_stations_service,
)


router = APIRouter(
    prefix="/api/v1",
    tags=["network"],
)


def to_station_response(station: Station) -> StationResponse:
    return StationResponse(
        id=str(station.id),
        code=station.code,
        name=station.name,
    )


@router.get(
    "/stations",
    response_model=list[StationResponse],
)
def read_stations(
    session: Annotated[Session, Depends(get_db)],
) -> list[StationResponse]:
    stations = list_stations_service(session)

    return [
        to_station_response(station)
        for station in stations
    ]


@router.get(
    "/routes",
    response_model=list[TransportRouteResponse],
)
def read_routes(
    session: Annotated[Session, Depends(get_db)],
) -> list[TransportRouteResponse]:
    routes = list_routes_service(session)

    return [
        TransportRouteResponse(
            id=str(route.id),
            code=route.code,
            origin=to_station_response(origin),
            destination=to_station_response(destination),
        )
        for route, origin, destination in routes
    ]