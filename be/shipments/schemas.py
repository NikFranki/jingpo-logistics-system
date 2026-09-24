from typing import Annotated, Literal, Self
from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    Field,
    model_validator,
)

class AllowedActionResponse(BaseModel):
    action: str
    enabled: bool
    reason_code: str | None = None
    reason: str | None = None


class TrackingEventResponse(BaseModel):
    id: str
    event_type: str
    occurred_at: datetime
    station_id: str | None
    task_id: str | None


class ShipmentResponse(BaseModel):
    id: str
    shipment_no: str
    order_id: str
    sender_address: str
    recipient_address: str
    region_code: str
    stage: str
    last_scanned_station_id: str | None
    created_at: datetime
    updated_at: datetime


class ShipmentDetailResponse(ShipmentResponse):
    tracking_events: list[TrackingEventResponse]
    allowed_actions: list[AllowedActionResponse] = Field(
        default_factory=list
    )

AddressText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=500,
    ),
]


class ShipmentAddressUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_address: AddressText | None = None
    recipient_address: AddressText | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        changes = self.model_dump(exclude_unset=True)

        if not changes:
            raise ValueError("At least one address must be provided")

        if any(value is None for value in changes.values()):
            raise ValueError("Address cannot be null")

        return self

class ShipmentEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: Literal[
        "PICKUP",
        "ENTER_A",
        "START_DELIVERY",
        "SIGN",
    ]

class ShipmentListResponse(BaseModel):
    items: list[ShipmentResponse]
    total: int
    page: int
    page_size: int