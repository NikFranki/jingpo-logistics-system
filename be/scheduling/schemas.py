from datetime import datetime
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Id = Annotated[int, Field(gt=0, strict=True)]
Reason = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500)]


def minute_time(value):
    if value is not None and (value.utcoffset() is None or value.second or value.microsecond):
        raise ValueError('Time must include timezone and be an exact minute')
    return value


class LegTime(BaseModel):
    model_config = ConfigDict(extra='forbid')
    route_id: Id
    planned_departure_at: datetime | None = None
    planned_arrival_at: datetime | None = None
    _minute = field_validator('planned_departure_at', 'planned_arrival_at')(minute_time)


class SchedulePreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_path_version: int | None = Field(default=None, ge=0, strict=True)
    expected_schedule_version: int | None = Field(default=None, ge=0, strict=True)
    expected_destination_station_id: Id | None = None
    origin_station_id: Id | None = None
    line_id: Id | None = None
    expected_line_version: int | None = Field(default=None, gt=0, strict=True)
    scheduled_trip_id: Id | None = None
    plan_id: Id | None = Field(default=None, gt=0, strict=True, deprecated=True)
    expected_plan_version: int | None = Field(default=None, gt=0, strict=True, deprecated=True)
    route_ids: list[Id] | None = Field(default=None, max_length=100, deprecated=True)
    first_departure_at: datetime | None = None
    planned_origin_arrival_at: datetime | None = None
    legs: list[LegTime] | None = Field(default=None, max_length=100)
    _minute = field_validator('first_departure_at', 'planned_origin_arrival_at')(minute_time)

    @model_validator(mode='after')
    def source(self):
        if self.line_id is not None:
            if self.plan_id is not None and self.plan_id != self.line_id:
                raise ValueError('line_id and plan_id refer to different lines')
            self.plan_id = self.line_id
            self.line_id = None
        if self.expected_line_version is not None:
            if self.expected_plan_version is not None and self.expected_plan_version != self.expected_line_version:
                raise ValueError('Line version fields disagree')
            self.expected_plan_version = self.expected_line_version
            self.expected_line_version = None
        if self.scheduled_trip_id is not None and (self.plan_id is not None or self.route_ids is not None):
            raise ValueError('Choose a scheduled trip instead of a line or route_ids')
        if self.scheduled_trip_id is not None and self.first_departure_at is not None:
            raise ValueError('The selected scheduled trip determines departure times')
        if self.plan_id is not None and self.route_ids is not None:
            raise ValueError('Choose a transport line or deprecated route_ids')
        if self.plan_id is None and self.expected_plan_version is not None:
            raise ValueError('expected_plan_version requires line_id')
        return self


class ScheduleConfirmRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_token: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200000)]
    reason: Reason
    acknowledged_warning_codes: list[Annotated[str, StringConstraints(strict=True)]] = Field(default_factory=list, max_length=200)


class CancelPreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: Reason


class PreviewLeg(BaseModel):
    position: int
    route_id: str
    route_code: str
    origin_station_id: str
    destination_station_id: str
    planned_departure_at: datetime | None
    planned_arrival_at: datetime | None
    travel_reference_minutes: int | None
    transfer_reference_minutes: int | None
    approved_transfer_minutes: int
    planned_travel_minutes: int | None
    shared_task_id: str | None
    shared_task_revision: int | None
    shared_members: list[str]


class PreviewNotice(BaseModel):
    code: str
    message: str


class SchedulePreviewResponse(BaseModel):
    line_id: str | None = None
    line_version: int | None = None
    scheduled_trip_id: str | None = None
    shipment_id: str
    path_version: int
    schedule_version: int
    destination_station_id: str
    anchor_station_id: str
    stage: str
    frozen_task_id: str | None
    frozen_task_revision: int | None
    anchor_arrival_at: datetime | None
    source_plan_id: str | None = Field(deprecated=True)
    source_plan_version: int | None = Field(deprecated=True)
    legs: list[PreviewLeg]
    warnings: list[PreviewNotice]
    missing: list[dict]
    replacements: list[dict]
    planned_origin_arrival_at: datetime | None
    can_confirm: bool
    preview_token: str


class ScheduleLeg(BaseModel):
    planned_travel_minutes: int | None = None
    approved_transfer_minutes: int | None = None
    planned_origin_arrival_at: datetime | None = None
    path_leg_id: str
    position: int
    route_id: str
    route_code: str
    origin_station_id: str
    destination_station_id: str
    task_id: str | None
    association_id: str | None
    planned_departure_at: datetime | None
    planned_arrival_at: datetime | None
    travel_reference_minutes: int | None
    transfer_reference_minutes: int | None
    scheduling_source: str | None = None
    schedule_revision: int | None = None
    forecast_departure_at: datetime | None = None
    forecast_arrival_at: datetime | None = None
    forecast_stale: bool = False
    waiting_members: list[dict] = Field(default_factory=list)
    task_no: str | None = None
    task_status: str | None = None
    actual_departure_at: datetime | None = None
    actual_arrival_at: datetime | None = None
    association_state: str | None = None
    ready_at: datetime | None = None


class ScheduleResponse(BaseModel):
    configuration_risks: list[dict] = Field(default_factory=list)
    shipment_id: str
    scheduling_mode: str
    status: str
    reason: str | None
    path_version: int
    version: int
    line_id: str | None = None
    line_version: int | None = None
    scheduled_trip_id: str | None = None
    source_plan_id: str | None = Field(default=None, deprecated=True)
    source_plan_version: int | None = Field(default=None, deprecated=True)
    origin_station_id: str | None
    destination_station_id: str
    legs: list[ScheduleLeg]
    server_time: datetime


class ScheduleHistoryItem(BaseModel):
    version: int
    path_version: int
    reason: str
    occurred_at: datetime
    legs: list[ScheduleLeg]
    line_id: str | None = None
    line_version: int | None = None
    scheduled_trip_id: str | None = None
    source_plan_id: str | None = Field(default=None, deprecated=True)
    source_plan_version: int | None = Field(default=None, deprecated=True)


class ScheduleHistoryResponse(BaseModel):
    items: list[ScheduleHistoryItem]
    total: int
    page: int
    page_size: int


class CancelImpact(BaseModel):
    association_id: str
    shipment_id: str
    task_id: str
    task_revision: int
    task_status: str
    association_state: str


class CancelPreviewResponse(BaseModel):
    task_id: str
    schedule_revision: int
    reason: str
    impact: list[CancelImpact]
    cancel_token: str
