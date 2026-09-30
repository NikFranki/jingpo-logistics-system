"""Stable shipment stages and tracking event types.

Stations and routes are data, not variants of these enums.
"""

from enum import StrEnum


class ShipmentStage(StrEnum):
    PENDING_PICKUP = "PENDING_PICKUP"
    PICKED_UP = "PICKED_UP"
    AT_STATION = "AT_STATION"
    IN_TRANSIT = "IN_TRANSIT"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    SIGNED = "SIGNED"


class TrackingEventType(StrEnum):
    SHIPMENT_CREATED = "SHIPMENT_CREATED"
    PICKUP = "PICKUP"
    ARRIVE = "ARRIVE"
    DEPART = "DEPART"
    START_DELIVERY = "START_DELIVERY"
    SIGN = "SIGN"


def sql_enum_values(enum_type: type[StrEnum]) -> str:
    """Render trusted application enum values for model CHECK constraints."""
    return ", ".join(f"'{item.value}'" for item in enum_type)
