from regions.addresses import AddressSelection, AddressResponse, REGION_FIELDS, validate_patch
from typing import Annotated, Self

from datetime import datetime

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


class OrderResponse(AddressResponse):
    id: str
    order_no: str
    product_name: str
    quantity: int
    sender_name: str
    sender_address: str
    recipient_name: str
    recipient_address: str
    earliest_handover_at: AwareDatetime | None = None
    latest_delivery_at: AwareDatetime | None = None
    region_code: str
    status: str
    created_at: datetime
    updated_at: datetime


class OrderListResponse(BaseModel):
    items: list[OrderResponse]
    total: int
    page: int
    page_size: int

NameText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
    ),
]

AddressText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=500,
    ),
]

class OrderCreateRequest(AddressSelection):
    model_config = ConfigDict(extra="forbid")

    product_name: NameText
    quantity: int = Field(gt=0)
    sender_name: NameText
    sender_address: AddressText
    recipient_name: NameText
    recipient_address: AddressText
    earliest_handover_at: AwareDatetime | None = None
    latest_delivery_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_delivery_window(self) -> Self:
        if self.earliest_handover_at and self.latest_delivery_at and self.earliest_handover_at > self.latest_delivery_at:
            raise ValueError("最早可交运时间不能晚于最晚送达时间")
        return self

class OrderUpdateRequest(AddressSelection):
    model_config = ConfigDict(extra="forbid")

    product_name: NameText | None = None
    quantity: int | None = Field(default=None, gt=0)
    sender_name: NameText | None = None
    sender_address: AddressText | None = None
    recipient_name: NameText | None = None
    recipient_address: AddressText | None = None
    earliest_handover_at: AwareDatetime | None = None
    latest_delivery_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        changes = self.model_dump(exclude_unset=True)

        if not changes:
            raise ValueError("At least one field must be provided")

        validate_patch(changes)

        window_fields = {"earliest_handover_at", "latest_delivery_at"}
        if any(value is None for key, value in changes.items() if key not in REGION_FIELDS and key not in window_fields):
            raise ValueError("Fields cannot be null")

        earliest = changes.get("earliest_handover_at")
        latest = changes.get("latest_delivery_at")
        if earliest and latest and earliest > latest:
            raise ValueError("最早可交运时间不能晚于最晚送达时间")

        return self

class OrderShipmentSummary(BaseModel):
    id: str
    shipment_no: str
    stage: str


class OrderDetailResponse(OrderResponse):
    shipment: OrderShipmentSummary | None
