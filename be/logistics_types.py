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


class TaskStatus(StrEnum):
    PENDING_DEPARTURE = "PENDING_DEPARTURE"
    IN_TRANSIT = "IN_TRANSIT"
    ARRIVED = "ARRIVED"
    CANCELLED = "CANCELLED"


def sql_enum_values(enum_type: type[StrEnum]) -> str:
    """Render trusted application enum values for model CHECK constraints."""
    return ", ".join(f"'{item.value}'" for item in enum_type)


class PathLegState(StrEnum):
    PENDING = "PENDING"
    RESERVED = "RESERVED"
    IN_TRANSIT = "IN_TRANSIT"
    ARRIVED = "ARRIVED"


class TransportPathStatus(StrEnum):
    WAITING_FIRST_ARRIVAL = "WAITING_FIRST_ARRIVAL"
    NEEDS_PLANNING = "NEEDS_PLANNING"
    READY = "READY"
    RESERVED = "RESERVED"
    IN_TRANSIT = "IN_TRANSIT"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
