from datetime import datetime
from typing import Literal, Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, StrictStr, model_validator
from network.schemas import NetworkCode
from logistics_types import TaskStatus


class CandidateShipmentResponse(BaseModel):
    id: str
    shipment_no: str
    order_id: str
    sender_address: str
    recipient_address: str
    region_code: str
    stage: str
    destination_station_id: str
    last_scanned_station_id: str
    created_at: datetime
    updated_at: datetime


class CandidateShipmentListResponse(BaseModel):
    items: list[CandidateShipmentResponse]
    total: int
    page: int
    page_size: int

class TransportTaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route_code: NetworkCode | None = None
    expected_path_versions: dict[str, Annotated[int, Field(gt=0, strict=True)]] | None = None
    expected_arrival_at: datetime
    shipment_ids: list[int] = Field(
        min_length=1,
        max_length=100,
    )

    @field_validator("expected_arrival_at")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError(
                "expected_arrival_at must include timezone"
            )
        return value

    @field_validator("shipment_ids")
    @classmethod
    def validate_unique_shipments(
        cls,
        value: list[int],
    ) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("shipment_ids cannot contain duplicates")
        return value

    @model_validator(mode="after")
    def validate_path_versions(self):
        if self.route_code is None and self.expected_path_versions is None:
            raise ValueError("Automatic next-leg creation requires expected_path_versions")
        if self.expected_path_versions is not None:
            if set(self.expected_path_versions) != {str(i) for i in self.shipment_ids}:
                raise ValueError("expected_path_versions must cover precisely the shipment_ids")
            if any(type(v) is not int or v < 1 for v in self.expected_path_versions.values()):
                raise ValueError("expected_path_versions values must be positive integers")
        return self


class TaskShipmentResponse(BaseModel):
    id: str
    shipment_no: str
    stage: str


class TransportTaskResponse(BaseModel):
    scheduling_source: str = "LEGACY"
    planned_departure_at: datetime | None = None
    forecast_arrival_at: datetime | None = None
    forecast_departure_at: datetime | None = None
    forecast_stale: bool = False
    schedule_revision: int = 1
    waiting_members: list[dict] = Field(default_factory=list)
    cancel_impact: list[dict] = Field(default_factory=list)
    delay_monitoring_enabled: bool
    id: str
    task_no: str
    route_code: str
    origin_station_id: str
    destination_station_id: str
    status: TaskStatus
    expected_arrival_at: datetime
    departed_at: datetime | None
    arrived_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    created_at: datetime
    shipments: list[TaskShipmentResponse]

class TaskAllowedActionResponse(BaseModel):
    action: str
    enabled: bool
    reason_code: str | None = None
    reason: str | None = None

class TransportTaskDetailResponse(TransportTaskResponse):
    server_time: datetime
    delay_status: Literal[
        "NOT_APPLICABLE",
        "NONE",
        "OVERDUE",
        "LATE_ARRIVAL",
    ]
    allowed_actions: list[TaskAllowedActionResponse] = Field(
        default_factory=list
    )
    delay_minutes: int | None


class TransportTaskListItemResponse(BaseModel):
    scheduling_source: str = "LEGACY"
    planned_departure_at: datetime | None = None
    forecast_arrival_at: datetime | None = None
    forecast_stale: bool = False
    schedule_revision: int = 1
    delay_monitoring_enabled: bool
    id: str
    task_no: str
    route_code: str
    status: TaskStatus
    expected_arrival_at: datetime
    departed_at: datetime | None
    arrived_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    created_at: datetime
    delay_status: str
    delay_minutes: int | None


class TransportTaskListResponse(BaseModel):
    items: list[TransportTaskListItemResponse]
    total: int
    page: int
    page_size: int
    server_time: datetime

class TransportTaskDepartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_schedule_revision: int | None = Field(default=None, gt=0, strict=True)

class TransportTaskCancelRequest(BaseModel):
    expected_schedule_revision: int | None = Field(default=None, gt=0, strict=True)
    cancel_token: str | None = Field(default=None, max_length=8000000)
    model_config = ConfigDict(extra="forbid")
    reason: StrictStr

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 500:
            raise ValueError("reason must contain 1–500 characters after trimming")
        return value


class ShipmentTaskHistoryItem(BaseModel):
    association_state: str = 'RELEASED'
    schedule_version: int | None = None
    release_reason: str | None = None
    id: str
    task_no: str
    route_code: str
    status: TaskStatus
    origin_station_id: str
    destination_station_id: str
    cancelled_at: datetime | None
    cancel_reason: str | None
    released_at: datetime | None


class ShipmentTaskHistoryResponse(BaseModel):
    items: list[ShipmentTaskHistoryItem]
    total: int
    page: int
    page_size: int
