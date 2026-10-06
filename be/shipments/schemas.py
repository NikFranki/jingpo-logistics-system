from regions.addresses import AddressSelection, AddressResponse, REGION_FIELDS, validate_patch
from typing import Annotated, Literal, Self
from datetime import datetime
import re

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    Field,
    model_validator,
)
from planning.schemas import ShipmentTransportPathResponse
from logistics_types import ShipmentStage, TrackingEventType, TaskStatus

class AllowedActionResponse(BaseModel):
    action: str
    enabled: bool
    reason_code: str | None = None
    reason: str | None = None


class TrackingEventResponse(BaseModel):
    id: str
    event_type: TrackingEventType
    occurred_at: datetime
    station_id: str | None
    task_id: str | None


class ShipmentCreateRequest(BaseModel):
    scheduling_mode: Literal["LEGACY", "REVIEWED"] = "REVIEWED"
    model_config = ConfigDict(extra="forbid")
    destination_station_id: int | None = Field(default=None, gt=0, strict=True)


class ShipmentResponse(AddressResponse):
    planned_origin_station_id: str | None = None
    earliest_handover_at: datetime | None = None
    latest_delivery_at: datetime | None = None
    id: str
    shipment_no: str
    order_id: str
    sender_address: str
    recipient_address: str
    region_code: str
    stage: ShipmentStage
    destination_station_id: str
    last_scanned_station_id: str | None
    created_at: datetime
    updated_at: datetime


class ShipmentDetailResponse(ShipmentResponse):
    scheduling_mode: str = "LEGACY"
    schedule: dict | None = None
    path_version: int = 0
    transport_path: ShipmentTransportPathResponse | None = None
    active_transport_task: "ActiveTransportTaskResponse | None"
    tracking_events: list[TrackingEventResponse]
    allowed_actions: list[AllowedActionResponse] = Field(
        default_factory=list
    )


class ActiveTransportTaskResponse(BaseModel):
    id: str
    route_code: str
    origin_station_id: str
    destination_station_id: str
    status: TaskStatus

AddressText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=500,
    ),
]


class ShipmentAddressUpdateRequest(AddressSelection):
    model_config = ConfigDict(extra="forbid")

    sender_address: AddressText | None = None
    recipient_address: AddressText | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        changes = self.model_dump(exclude_unset=True)

        if not changes:
            raise ValueError("At least one address must be provided")

        validate_patch(changes)

        if any(value is None for key, value in changes.items() if key not in REGION_FIELDS):
            raise ValueError("Address cannot be null")

        return self


class ShipmentDestinationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_destination_station_id: int = Field(gt=0, strict=True)
    destination_station_id: int = Field(gt=0, strict=True)
    reason: Annotated[str, StringConstraints(
        strict=True, strip_whitespace=True, min_length=1, max_length=500,
    )]


class DestinationChangeResponse(BaseModel):
    id: str
    previous_destination_station_id: str
    destination_station_id: str
    reason: str
    occurred_at: datetime


class DestinationChangeListResponse(BaseModel):
    items: list[DestinationChangeResponse]
    total: int
    page: int
    page_size: int

class ShipmentEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: Literal[
        TrackingEventType.PICKUP,
        TrackingEventType.ARRIVE,
        TrackingEventType.START_DELIVERY,
        TrackingEventType.SIGN,
    ]
    station_id: str | None = None

    @model_validator(mode="after")
    def validate_station(self) -> Self:
        if self.event_type == TrackingEventType.ARRIVE:
            if self.station_id is not None and re.fullmatch(r"[1-9][0-9]*", self.station_id) is None:
                raise ValueError("station_id must be positive when supplied")
        elif self.station_id is not None:
            raise ValueError("station_id is only allowed for ARRIVE")
        return self

class ShipmentListResponse(BaseModel):
    items: list[ShipmentResponse]
    total: int
    page: int
    page_size: int
