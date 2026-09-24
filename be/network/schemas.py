from pydantic import BaseModel


class StationResponse(BaseModel):
    id: str
    code: str
    name: str


class TransportRouteResponse(BaseModel):
    id: str
    code: str
    origin: StationResponse
    destination: StationResponse