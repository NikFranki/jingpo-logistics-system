# 此模块负责映射数据库表

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ForeignKeyConstraint,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Identity,
    SmallInteger,
    String,
    Computed,
    Integer,
    func,
    text,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

from db import Base
from logistics_types import ShipmentStage, TrackingEventType, sql_enum_values

class OperationLog(Base):
    __tablename__ = "operation_logs"
    __table_args__ = (
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_logs_hash",
        ),
        CheckConstraint(
            "resource_type IN "
            "('ORDER', 'SHIPMENT', 'TRANSPORT_TASK', 'CLOCK', 'STATION', 'ROUTE', 'PATH_PLAN')",
            name="ck_logs_resource_type",
        ),
        CheckConstraint(
            "response_status IN (200, 201)",
            name="ck_logs_response_status",
        ),
        CheckConstraint(
            "length(btrim(action)) > 0",
            name="ck_logs_action",
        ),
        CheckConstraint(
            "resource_id > 0",
            name="ck_logs_resource_id",
        ),
    )

    parent_operation_id: Mapped[int | None] = mapped_column(ForeignKey("operation_logs.id"))
    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    # idempotency_key 唯一：同一次操作不能成功记录两次
    idempotency_key: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        unique=True,
    )
    # 判断相同 key 是否配了相同请求内容
    request_hash: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[int] = mapped_column(BigInteger)
    before_data: Mapped[dict | None] = mapped_column(JSONB)
    after_data: Mapped[dict | None] = mapped_column(JSONB)
    # 重复请求时可以返回第一次的成功结果
    response_body: Mapped[dict] = mapped_column(JSONB)
    response_status: Mapped[int] = mapped_column(SmallInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

class StructuredAddressFields:
    sender_province_id: Mapped[int | None] = mapped_column(ForeignKey("provinces.id", ondelete="RESTRICT"))
    sender_province_name: Mapped[str | None] = mapped_column(String(100))
    sender_city_id: Mapped[int | None] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"))
    sender_city_name: Mapped[str | None] = mapped_column(String(100))
    sender_district_id: Mapped[int | None] = mapped_column(ForeignKey("districts.id", ondelete="RESTRICT"))
    sender_district_name: Mapped[str | None] = mapped_column(String(100))
    recipient_province_id: Mapped[int | None] = mapped_column(ForeignKey("provinces.id", ondelete="RESTRICT"))
    recipient_province_name: Mapped[str | None] = mapped_column(String(100))
    recipient_city_id: Mapped[int | None] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"))
    recipient_city_name: Mapped[str | None] = mapped_column(String(100))
    recipient_district_id: Mapped[int | None] = mapped_column(ForeignKey("districts.id", ondelete="RESTRICT"))
    recipient_district_name: Mapped[str | None] = mapped_column(String(100))


class Order(StructuredAddressFields, Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="ck_orders_quantity",
        ),
        CheckConstraint(
            "region_code = 'Z'",
            name="ck_orders_region",
        ),
        CheckConstraint(
            "status IN "
            "('PENDING_SHIPMENT', 'SHIPMENT_CREATED', 'COMPLETED')",
            name="ck_orders_status",
        ),
        CheckConstraint(
            "length(btrim(product_name)) > 0 "
            "AND length(btrim(sender_name)) > 0 "
            "AND length(btrim(sender_address)) > 0 "
            "AND length(btrim(recipient_name)) > 0 "
            "AND length(btrim(recipient_address)) > 0",
            name="ck_orders_nonblank",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    order_no: Mapped[str] = mapped_column(
        String(32),
        Computed(
            "'ORD-' || lpad(id::text, greatest(6, length(id::text)), '0')"
        ),
        unique=True,
    )
    product_name: Mapped[str] = mapped_column(String(100))
    quantity: Mapped[int] = mapped_column(Integer)
    sender_name: Mapped[str] = mapped_column(String(100))
    sender_address: Mapped[str] = mapped_column(String(500))
    recipient_name: Mapped[str] = mapped_column(String(100))
    recipient_address: Mapped[str] = mapped_column(String(500))
    region_code: Mapped[str] = mapped_column(
        String(1),
        server_default="Z",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        server_default="PENDING_SHIPMENT",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

class Station(Base):
    transfer_minutes: Mapped[int | None] = mapped_column(Integer)
    __tablename__ = "stations"
    __table_args__ = (
        CheckConstraint(
            "code ~ '^[A-Z0-9][A-Z0-9_-]{0,31}$'",
            name="ck_stations_code",
        ),
        CheckConstraint(
            "length(btrim(name)) > 0",
            name="ck_stations_name",
        ),
        CheckConstraint("transfer_minutes BETWEEN 0 AND 525600", name="ck_stations_transfer"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    code: Mapped[str] = mapped_column(
        String(32),
        unique=True,
    )
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    allows_first_arrival: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    allows_delivery: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class TransportRoute(Base):
    travel_minutes: Mapped[int | None] = mapped_column(Integer)
    __tablename__ = "transport_routes"
    __table_args__ = (
        CheckConstraint(
            "code ~ '^[A-Z0-9][A-Z0-9_-]{0,31}$'",
            name="ck_routes_code",
        ),
        CheckConstraint(
            "origin_station_id <> destination_station_id",
            name="ck_routes_distinct_stations",
        ),
        CheckConstraint("travel_minutes BETWEEN 1 AND 525600", name="ck_routes_travel"),
        UniqueConstraint(
            "origin_station_id",
            "destination_station_id",
            name="uq_routes_endpoints",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    code: Mapped[str] = mapped_column(
        String(32),
        unique=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    delay_monitoring_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    origin_station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id"),
    )
    destination_station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id"),
    )

class Shipment(StructuredAddressFields, Base):
    scheduling_mode: Mapped[str] = mapped_column(String(16), server_default="LEGACY")
    schedule_version: Mapped[int] = mapped_column(Integer, server_default="0")
    schedule_status: Mapped[str] = mapped_column(String(32), server_default="NOT_CONFIRMED")
    schedule_reason: Mapped[str | None] = mapped_column(String(500))
    __tablename__ = "shipments"
    __table_args__ = (
        CheckConstraint("path_version >= 0", name="ck_shipments_path_version"),
        CheckConstraint("schedule_version >= 0", name="ck_shipments_schedule_version"),
        CheckConstraint("scheduling_mode IN ('LEGACY','REVIEWED')", name="ck_shipments_schedule_mode"),
        CheckConstraint("schedule_status IN ('NOT_CONFIRMED','CONFIRMED','NEEDS_RECONFIRMATION','BLOCKED','COMPLETED')", name="ck_shipments_schedule_status"),
        CheckConstraint(
            "region_code = 'Z'",
            name="ck_shipments_region",
        ),
        CheckConstraint(
            f"stage IN ({sql_enum_values(ShipmentStage)})",
            name="ck_shipments_stage",
        ),
        CheckConstraint(
            "(stage IN ('PENDING_PICKUP', 'PICKED_UP') "
            "AND last_scanned_station_id IS NULL) "
            "OR "
            "(stage NOT IN ('PENDING_PICKUP', 'PICKED_UP') "
            "AND last_scanned_station_id IS NOT NULL)",
            name="ck_shipments_scanned_station",
        ),
        CheckConstraint(
            "length(btrim(sender_address)) > 0 "
            "AND length(btrim(recipient_address)) > 0",
            name="ck_shipments_addresses",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    shipment_no: Mapped[str] = mapped_column(
        String(32),
        Computed(
            "'SHP-' || lpad(id::text, greatest(6, length(id::text)), '0')"
        ),
        unique=True,
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id"),
        unique=True,
    )
    sender_address: Mapped[str] = mapped_column(String(500))
    recipient_address: Mapped[str] = mapped_column(String(500))
    region_code: Mapped[str] = mapped_column(
        String(1),
        server_default="Z",
    )
    stage: Mapped[str] = mapped_column(
        String(32),
        server_default="PENDING_PICKUP",
    )
    path_version: Mapped[int] = mapped_column(Integer, server_default="0")
    destination_station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    last_scanned_station_id: Mapped[int | None] = mapped_column(
        ForeignKey("stations.id"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

class TransportTask(Base):
    scheduling_source: Mapped[str] = mapped_column(String(16), server_default="LEGACY")
    planned_departure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planned_travel_minutes: Mapped[int | None] = mapped_column(Integer)
    schedule_revision: Mapped[int] = mapped_column(Integer, server_default="1")
    __tablename__ = "transport_tasks"
    __table_args__ = (
        CheckConstraint(
            "((status IN ('PENDING_DEPARTURE','WAITING_CARGO','WAITING_PREDECESSOR') AND departed_at IS NULL AND arrived_at IS NULL) "
            "OR (status = 'IN_TRANSIT' AND departed_at IS NOT NULL AND arrived_at IS NULL) "
            "OR (status = 'ARRIVED' AND departed_at IS NOT NULL AND arrived_at IS NOT NULL "
            "AND arrived_at >= departed_at)) AND cancelled_at IS NULL AND cancel_reason IS NULL "
            "OR (status = 'CANCELLED' AND departed_at IS NULL AND arrived_at IS NULL "
            "AND cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL "
            "AND length(btrim(cancel_reason)) BETWEEN 1 AND 500)",
            name="ck_tasks_status_times",
        ),
        CheckConstraint("schedule_revision > 0", name="ck_tasks_schedule_revision"),
        CheckConstraint("scheduling_source IN ('LEGACY','PLAN')", name="ck_tasks_schedule_source"),
        CheckConstraint("planned_travel_minutes BETWEEN 1 AND 525600", name="ck_tasks_travel"),
        CheckConstraint("scheduling_source = 'LEGACY' OR (planned_departure_at IS NOT NULL AND planned_travel_minutes IS NOT NULL AND expected_arrival_at > planned_departure_at)", name="ck_tasks_plan_times"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    task_no: Mapped[str] = mapped_column(
        String(32),
        Computed(
            "'TRIP-' || lpad(id::text, greatest(6, length(id::text)), '0')"
        ),
        unique=True,
    )
    route_id: Mapped[int] = mapped_column(
        ForeignKey("transport_routes.id"),
    )
    delay_monitoring_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    status: Mapped[str] = mapped_column(
        String(32),
        server_default="PENDING_DEPARTURE",
    )
    expected_arrival_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )
    departed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    arrived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

class TaskShipment(Base):
    association_state: Mapped[str] = mapped_column(String(16), server_default="ACTIVE")
    schedule_version: Mapped[int | None] = mapped_column(Integer)
    predecessor_association_id: Mapped[int | None] = mapped_column(ForeignKey("task_shipments.id"))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_transfer_minutes: Mapped[int] = mapped_column(Integer, server_default="0")
    planned_origin_arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(500))
    __tablename__ = "task_shipments"
    __table_args__ = (
        CheckConstraint("(association_state IN ('PLANNED','ACTIVE') AND released_at IS NULL) OR (association_state = 'RELEASED' AND released_at IS NOT NULL)", name="ck_task_shipments_state"),
        CheckConstraint("approved_transfer_minutes BETWEEN 0 AND 525600", name="ck_task_shipments_transfer"),
        UniqueConstraint(
            "task_id",
            "shipment_id",
            name="uq_task_shipments",
        ),
        Index("uq_task_shipments_active_leg", "path_leg_id", unique=True,
              postgresql_where=text("association_state IN ('PLANNED','ACTIVE') AND path_leg_id IS NOT NULL")),
        Index(
            "uq_task_shipments_active",
            "shipment_id",
            unique=True,
            postgresql_where=text("association_state = 'ACTIVE'"),
        ),
        Index(
            "ix_task_shipments_shipment",
            "shipment_id",
            "task_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("transport_tasks.id"),
    )
    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("shipments.id"),
    )
    path_leg_id: Mapped[int | None] = mapped_column(ForeignKey("shipment_path_legs.id"))
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

class TrackingEvent(Base):
    __tablename__ = "tracking_events"
    __table_args__ = (
        UniqueConstraint(
            "operation_id",
            "shipment_id",
            "event_type",
            name="uq_events_operation",
        ),
        CheckConstraint(
            f"event_type IN ({sql_enum_values(TrackingEventType)})",
            name="ck_events_type",
        ),
        CheckConstraint(
            "(event_type = 'DEPART' AND task_id IS NOT NULL) "
            "OR (event_type = 'ARRIVE') "
            "OR (event_type IN ('SHIPMENT_CREATED', 'PICKUP', "
            "'START_DELIVERY', 'SIGN') AND task_id IS NULL)",
            name="ck_events_task",
        ),
        CheckConstraint(
            "(event_type IN "
            "('ARRIVE', 'DEPART') "
            "AND station_id IS NOT NULL) "
            "OR "
            "(event_type IN "
            "('SHIPMENT_CREATED', 'PICKUP', "
            "'START_DELIVERY', 'SIGN') "
            "AND station_id IS NULL)",
            name="ck_events_station",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("shipments.id"),
    )
    event_type: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )
    station_id: Mapped[int | None] = mapped_column(
        ForeignKey("stations.id"),
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("transport_tasks.id"),
    )
    operation_id: Mapped[int] = mapped_column(
        ForeignKey(
            "operation_logs.id",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class PathPlan(Base):
    __tablename__ = "path_plans"
    __table_args__ = (
        CheckConstraint("code ~ '^[A-Z0-9][A-Z0-9_-]{0,31}$'", name="ck_path_plans_code"),
        CheckConstraint("length(btrim(name)) > 0", name="ck_path_plans_name"),
        CheckConstraint("version > 0", name="ck_path_plans_version"),
        CheckConstraint("origin_station_id <> destination_station_id", name="ck_path_plans_endpoints"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    origin_station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    destination_station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))


class PathPlanLeg(Base):
    origin_transfer_override_minutes: Mapped[int | None] = mapped_column(Integer)
    __tablename__ = "path_plan_legs"
    __table_args__ = (
        UniqueConstraint("plan_id", "position", name="uq_path_plan_legs_position"),
        CheckConstraint("position >= 0", name="ck_path_plan_legs_position"),
        CheckConstraint("origin_transfer_override_minutes BETWEEN 0 AND 525600", name="ck_path_plan_legs_transfer"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("path_plans.id"))
    position: Mapped[int] = mapped_column(Integer)
    route_id: Mapped[int] = mapped_column(ForeignKey("transport_routes.id"))


class ShipmentPathLeg(Base):
    travel_reference_minutes: Mapped[int | None] = mapped_column(Integer)
    origin_transfer_reference_minutes: Mapped[int | None] = mapped_column(Integer)
    __tablename__ = "shipment_path_legs"
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_shipment_path_legs_position"),
        Index("uq_shipment_path_legs_current", "shipment_id", "position", unique=True,
              postgresql_where=text("superseded_at IS NULL")),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    position: Mapped[int] = mapped_column(Integer)
    route_id: Mapped[int] = mapped_column(ForeignKey("transport_routes.id"))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShipmentPathVersion(Base):
    __tablename__ = "shipment_path_versions"
    __table_args__ = (
        UniqueConstraint("shipment_id", "version", name="uq_shipment_path_versions"),
        CheckConstraint("version > 0", name="ck_shipment_path_versions_version"),
        CheckConstraint("jsonb_typeof(legs) = 'array'", name="ck_shipment_path_versions_legs"),
        CheckConstraint("length(btrim(reason)) BETWEEN 1 AND 500", name="ck_shipment_path_versions_reason"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    version: Mapped[int] = mapped_column(Integer)
    destination_station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    source_plan_id: Mapped[int | None] = mapped_column(ForeignKey("path_plans.id"))
    source_plan_version: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(500))
    legs: Mapped[list] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ShipmentScheduleVersion(Base):
    __tablename__ = "shipment_schedule_versions"
    __table_args__ = (
        UniqueConstraint("shipment_id", "version", name="uq_shipment_schedule_versions"),
        CheckConstraint("version > 0", name="ck_shipment_schedule_version"),
        CheckConstraint("jsonb_typeof(legs) = 'array'", name="ck_shipment_schedule_legs"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"))
    version: Mapped[int] = mapped_column(Integer)
    path_version: Mapped[int] = mapped_column(Integer)
    origin_station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    destination_station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    source_plan_id: Mapped[int | None] = mapped_column(ForeignKey("path_plans.id"))
    source_plan_version: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(500))
    legs: Mapped[list] = mapped_column(JSONB)
    operation_id: Mapped[int] = mapped_column(ForeignKey("operation_logs.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RegionFields:
    """Shared dictionary fields; IDs are local to each administrative level."""
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text('true'))
    sort_order: Mapped[int] = mapped_column(Integer, server_default='0')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Province(RegionFields, Base):
    __tablename__ = 'provinces'
    __table_args__ = (
        CheckConstraint("code ~ '^[0-9]{2,12}$'", name='ck_provinces_code'),
        CheckConstraint('length(btrim(name)) > 0', name='ck_provinces_name'),
        CheckConstraint('sort_order >= 0', name='ck_provinces_sort'),
        CheckConstraint("kind IN ('PROVINCE','AUTONOMOUS_REGION','MUNICIPALITY','SPECIAL_ADMINISTRATIVE_REGION')", name='ck_provinces_kind'),
    )
    kind: Mapped[str] = mapped_column(String(32), server_default='PROVINCE')


class City(RegionFields, Base):
    __tablename__ = 'cities'
    __table_args__ = (
        UniqueConstraint('id', 'province_id', name='uq_cities_id_province'),
        CheckConstraint("code ~ '^[0-9]{2,12}$'", name='ck_cities_code'),
        CheckConstraint('length(btrim(name)) > 0', name='ck_cities_name'),
        CheckConstraint('sort_order >= 0', name='ck_cities_sort'),
        Index('ix_cities_province', 'province_id'),
    )
    province_id: Mapped[int] = mapped_column(ForeignKey('provinces.id', ondelete='RESTRICT'))


class District(RegionFields, Base):
    __tablename__ = 'districts'
    __table_args__ = (
        ForeignKeyConstraint(['city_id', 'province_id'], ['cities.id', 'cities.province_id'],
                             name='fk_districts_city_province', ondelete='RESTRICT'),
        CheckConstraint("code ~ '^[0-9]{2,12}$'", name='ck_districts_code'),
        CheckConstraint('length(btrim(name)) > 0', name='ck_districts_name'),
        CheckConstraint('sort_order >= 0', name='ck_districts_sort'),
        Index('ix_districts_parent', 'province_id', 'city_id'),
    )
    province_id: Mapped[int] = mapped_column(ForeignKey('provinces.id', ondelete='RESTRICT'))
    city_id: Mapped[int | None] = mapped_column(BigInteger)


class StationServiceArea(Base):
    __tablename__ = 'station_service_areas'
    __table_args__ = (
        ForeignKeyConstraint(['city_id', 'province_id'], ['cities.id', 'cities.province_id'],
                             name='fk_service_areas_city_province', ondelete='RESTRICT'),
        Index('ix_service_areas_station', 'station_id'),
        Index('uq_service_areas_enabled_region', 'province_id',
              text('COALESCE(city_id, 0)'), text('COALESCE(district_id, 0)'),
              unique=True, postgresql_where=text('enabled')),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey('stations.id', ondelete='RESTRICT'))
    province_id: Mapped[int] = mapped_column(ForeignKey('provinces.id', ondelete='RESTRICT'))
    city_id: Mapped[int | None] = mapped_column(BigInteger)
    district_id: Mapped[int | None] = mapped_column(ForeignKey('districts.id', ondelete='RESTRICT'))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text('true'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
