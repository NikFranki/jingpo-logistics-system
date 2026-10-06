from typing import Annotated, Self
from pydantic import BaseModel, ConfigDict, Field, model_validator
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
