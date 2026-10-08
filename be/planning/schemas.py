from datetime import datetime
from typing import Annotated, Self
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from network.schemas import NetworkCode, NetworkName
from logistics_types import PathLegState, TransportPathStatus

PositiveId = Annotated[int, Field(gt=0, strict=True)]
Reason = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500)]

class TransferOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    station_id: PositiveId
    minutes: int = Field(ge=0, le=525600, strict=True)

class PathPlanCreateRequest(BaseModel):
    transfer_overrides: list[TransferOverride] = Field(default_factory=list, max_length=99)
    model_config = ConfigDict(extra="forbid")
    code: NetworkCode
    name: NetworkName
    route_ids: list[PositiveId] = Field(min_length=1, max_length=100)
    enabled: bool = Field(default=True, strict=True)

class PathPlanUpdateRequest(BaseModel):
    transfer_overrides: list[TransferOverride] | None = Field(default=None, max_length=99)
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(gt=0, strict=True)
    name: NetworkName | None = None
    route_ids: list[PositiveId] | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def changes_required(self) -> Self:
        fields = self.model_dump(exclude_unset=True)
        fields.pop("expected_version")
        if not fields or any(value is None for value in fields.values()):
            raise ValueError("Provide at least one non-null change")
        return self

class ShipmentPathUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0, strict=True)
    expected_anchor_station_id: PositiveId
    reason: Reason
    line_id: PositiveId | None = None
    expected_line_version: int | None = Field(default=None, gt=0, strict=True)
    plan_id: PositiveId | None = Field(default=None, deprecated=True)
    expected_plan_version: int | None = Field(default=None, gt=0, strict=True, deprecated=True)
    route_ids: list[PositiveId] | None = Field(default=None, max_length=100, deprecated=True)

    @model_validator(mode="after")
    def exactly_one_path(self) -> Self:
        if self.line_id is not None:
            if self.plan_id is not None and self.plan_id != self.line_id:
                raise ValueError("line_id and plan_id refer to different lines")
            self.plan_id = self.line_id
            self.line_id = None
        if self.expected_line_version is not None:
            if self.expected_plan_version is not None and self.expected_plan_version != self.expected_line_version:
                raise ValueError("Line version fields disagree")
            self.expected_plan_version = self.expected_line_version
            self.expected_line_version = None
        if (self.plan_id is None) == (self.route_ids is None):
            raise ValueError("Provide either line_id or the complete future route_ids")
        if (self.plan_id is None) != (self.expected_plan_version is None):
            raise ValueError("line_id requires expected_line_version")
        return self

class PathLegResponse(BaseModel):
    id: str
    position: int
    route_id: str
    route_code: str
    origin_station_id: str
    destination_station_id: str
    state: PathLegState
    task_id: str | None

class ShipmentTransportPathResponse(BaseModel):
    version: int
    line_id: str | None = None
    line_version: int | None = None
    scheduled_trip_id: str | None = None
    status: TransportPathStatus
    anchor_station_id: str | None
    destination_station_id: str
    next_route_id: str | None
    next_route_code: str | None
    reason_code: str | None
    reason: str | None
    legs: list[PathLegResponse]

class PathPlanResponse(BaseModel):
    transfer_overrides: list[TransferOverride] = Field(default_factory=list)
    id: str
    code: str
    name: str
    enabled: bool
    version: int
    origin_station_id: str
    destination_station_id: str
    usable: bool
    reason: str | None
    route_ids: list[str]

class RouteCandidateResponse(BaseModel):
    route_ids: list[str]
    route_codes: list[str]
    station_ids: list[str]
    hop_count: int


class PathOptionsResponse(BaseModel):
    planning_origin_station_id: str | None = None
    origin_match_status: str | None = None
    origin_match_reason: str | None = None
    route_candidates: list[RouteCandidateResponse] = Field(default_factory=list)
    path: ShipmentTransportPathResponse
    plans: list[PathPlanResponse]

class PathVersionResponse(BaseModel):
    version: int
    destination_station_id: str
    line_id: str | None = None
    line_version: int | None = None
    source_plan_id: str | None = Field(default=None, deprecated=True)
    source_plan_version: int | None = Field(default=None, deprecated=True)
    reason: str
    occurred_at: datetime
    legs: list[dict]

class PathHistoryResponse(BaseModel):
    items: list[PathVersionResponse]
    total: int
    page: int
    page_size: int
