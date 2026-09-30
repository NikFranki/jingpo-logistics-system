from typing import Annotated, Self
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NetworkCode = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,31}$", min_length=1, max_length=32)]
NetworkName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

class StationResponse(BaseModel):
    id: str
    code: str
    name: str
    enabled: bool
    allows_first_arrival: bool
    allows_delivery: bool

class TransportRouteResponse(BaseModel):
    id: str
    code: str
    origin: StationResponse
    destination: StationResponse
    enabled: bool
    delay_monitoring_enabled: bool

class StationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: NetworkCode
    name: NetworkName
    enabled: bool = Field(default=True, strict=True)
    allows_first_arrival: bool = Field(default=False, strict=True)
    allows_delivery: bool = Field(default=False, strict=True)

class PatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        values = self.model_dump(exclude_unset=True)
        if not values or any(value is None for value in values.values()):
            raise ValueError("Provide at least one non-null change")
        return self

class StationUpdateRequest(PatchRequest):
    name: NetworkName | None = None
    enabled: bool | None = Field(default=None, strict=True)
    allows_first_arrival: bool | None = Field(default=None, strict=True)
    allows_delivery: bool | None = Field(default=None, strict=True)

class RouteCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: NetworkCode
    origin_station_id: int = Field(gt=0, strict=True)
    destination_station_id: int = Field(gt=0, strict=True)
    enabled: bool = Field(default=True, strict=True)
    delay_monitoring_enabled: bool = Field(default=False, strict=True)

class RouteUpdateRequest(PatchRequest):
    enabled: bool | None = Field(default=None, strict=True)
    delay_monitoring_enabled: bool | None = Field(default=None, strict=True)
