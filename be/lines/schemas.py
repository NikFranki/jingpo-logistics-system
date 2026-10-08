from datetime import date, time
from typing import Annotated, Self
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from network.schemas import NetworkCode, NetworkName, StationResponse

Id = Annotated[int, Field(gt=0, strict=True)]


class LineLegInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    travel_minutes: int | None = Field(default=None, gt=0, le=525600, strict=True)


class LineTransferInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    station_id: Id
    minutes: int = Field(ge=0, le=525600, strict=True)


class LineCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: NetworkCode
    name: NetworkName
    station_ids: list[Id] = Field(min_length=2, max_length=101)
    legs: list[LineLegInput] = Field(min_length=1, max_length=100)
    transfer_overrides: list[LineTransferInput] = Field(default_factory=list, max_length=99)
    enabled: bool = Field(default=True, strict=True)

    @model_validator(mode='after')
    def chain(self) -> Self:
        if len(self.station_ids) != len(set(self.station_ids)) or len(self.legs) != len(self.station_ids) - 1:
            raise ValueError('Provide unique ordered stations and one duration per adjacent pair')
        ids = [v.station_id for v in self.transfer_overrides]
        if len(ids) != len(set(ids)) or any(i not in self.station_ids[1:-1] for i in ids):
            raise ValueError('Transfer overrides must reference unique intermediate stations')
        return self


class LineUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(gt=0, strict=True)
    name: NetworkName | None = None
    enabled: bool | None = Field(default=None, strict=True)
    station_ids: list[Id] | None = Field(default=None, min_length=2, max_length=101)
    legs: list[LineLegInput] | None = Field(default=None, min_length=1, max_length=100)
    transfer_overrides: list[LineTransferInput] | None = Field(default=None, max_length=99)

    @model_validator(mode='after')
    def changes(self) -> Self:
        changes = self.model_dump(exclude_unset=True)
        changes.pop('expected_version')
        if not changes or any(v is None for v in changes.values()):
            raise ValueError('Provide at least one non-null change')
        if self.station_ids is not None and self.legs is None:
            raise ValueError('Changing stations requires complete leg durations')
        return self


class LineLegResponse(BaseModel):
    position: int
    segment_id: str
    origin_station_id: str
    destination_station_id: str
    travel_minutes: int | None


class LineTransferResponse(BaseModel):
    station_id: str
    minutes: int


class LineResponse(BaseModel):
    id: str
    code: str
    name: str
    version: int
    enabled: bool
    usable: bool
    reason: str | None
    origin_station_id: str
    destination_station_id: str
    station_ids: list[str]
    stations: list[StationResponse]
    legs: list[LineLegResponse]
    transfer_overrides: list[LineTransferResponse]
    total_reference_minutes: int | None


class LineOptionsResponse(BaseModel):
    origin_station_id: str | None
    destination_station_id: str
    lines: list[LineResponse]
    recommended_line_id: str | None


class LineListResponse(BaseModel):
    items: list[LineResponse]
    total: int
    page: int
    page_size: int


class LineServiceStopInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    station_id: Id
    arrival_day_offset: int | None = Field(default=None, ge=0, le=30, strict=True)
    arrival_time: time | None = None
    departure_day_offset: int | None = Field(default=None, ge=0, le=30, strict=True)
    departure_time: time | None = None


class LineServiceCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: NetworkCode
    name: NetworkName
    valid_from: date
    valid_until: date | None = None
    weekdays: list[StrictInt] = Field(min_length=1, max_length=7)
    timezone: str = 'Asia/Shanghai'
    capacity_snapshot: dict[str, StrictInt] = Field(default_factory=dict)
    enabled: bool = True
    stops: list[LineServiceStopInput] = Field(min_length=2, max_length=101)

    @field_validator('capacity_snapshot')
    @classmethod
    def nonnegative_capacity(cls, value):
        if any(amount < 0 for amount in value.values()):
            raise ValueError('Capacity snapshot values must be nonnegative')
        return value


class LineServiceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(gt=0, strict=True)
    name: NetworkName | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    weekdays: list[StrictInt] | None = Field(default=None, min_length=1, max_length=7)
    timezone: str | None = None
    capacity_snapshot: dict[str, StrictInt] | None = None
    enabled: bool | None = None
    stops: list[LineServiceStopInput] | None = Field(default=None, min_length=2, max_length=101)

    @field_validator('capacity_snapshot')
    @classmethod
    def nonnegative_capacity(cls, value):
        if value is not None and any(amount < 0 for amount in value.values()):
            raise ValueError('Capacity snapshot values must be nonnegative')
        return value

    @model_validator(mode='after')
    def require_changes(self) -> Self:
        changes = self.model_dump(exclude_unset=True)
        changes.pop('expected_version')
        if not changes or any(value is None for key, value in changes.items() if key != 'valid_until'):
            raise ValueError('Provide at least one non-null change')
        return self


class LineServiceStopResponse(BaseModel):
    position: int
    station_id: str
    arrival_day_offset: int | None
    arrival_time: str | None
    departure_day_offset: int | None
    departure_time: str | None


class LineServiceResponse(BaseModel):
    id: str
    line_id: str
    code: str
    name: str
    valid_from: date
    valid_until: date | None
    weekdays: list[int]
    timezone: str
    capacity_snapshot: dict[str, int]
    enabled: bool
    version: int
    stops: list[LineServiceStopResponse]


class GenerateTripsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    from_date: date
    to_date: date


class ScheduledTripResponse(BaseModel):
    id: str
    line_id: str
    line_version: int
    service_id: str
    service_code: str | None
    service_name: str | None
    service_date: date
    service_version: int
    status: str
    stops: list[dict]
    capacity_snapshot: dict[str, int]


class ScheduledTripListResponse(BaseModel):
    items: list[ScheduledTripResponse]
    created: int
    from_date: date
    to_date: date


class ServiceOptionResponse(BaseModel):
    trip_id: str
    line_id: str
    line_version: int
    service_id: str
    service_code: str
    service_name: str
    service_date: date
    origin_station_id: str
    destination_station_id: str
    departure_at: str
    arrival_at: str
    capacity_snapshot: dict[str, int]
    legs: list[dict]


class ServiceOptionsResponse(BaseModel):
    origin_station_id: str
    destination_station_id: str
    items: list[ServiceOptionResponse]
