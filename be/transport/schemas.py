from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from network.schemas import NetworkCode


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
    route_code: NetworkCode
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


class TaskShipmentResponse(BaseModel):
    id: str
    shipment_no: str
    stage: str


class TransportTaskResponse(BaseModel):
    delay_monitoring_enabled: bool
    id: str
    task_no: str
    route_code: str
    origin_station_id: str
    destination_station_id: str
    status: str
    expected_arrival_at: datetime
    departed_at: datetime | None
    arrived_at: datetime | None
    created_at: datetime
    shipments: list[TaskShipmentResponse]

class TaskAllowedActionResponse(BaseModel):
    action: str
    enabled: bool
    reason_code: str | None = None
    reason: str | None = None

class TransportTaskDetailResponse(TransportTaskResponse):
    simulation_time: datetime
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
    delay_monitoring_enabled: bool
    id: str
    task_no: str
    route_code: str
    status: str
    expected_arrival_at: datetime
    departed_at: datetime | None
    arrived_at: datetime | None
    created_at: datetime
    delay_status: str
    delay_minutes: int | None


class TransportTaskListResponse(BaseModel):
    items: list[TransportTaskListItemResponse]
    total: int
    page: int
    page_size: int
    simulation_time: datetime