# 此模块负责映射数据库表

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
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

# 全局表 保存整个系统共享的一份配置或状态（全局表保存“系统现在处于什么环境或时间”）
# SimulationSettings 是一个 ORM 模型，作用是把 Python 类映射到 PostgreSQL 的 simulation_settings 表
class SimulationSettings(Base):
    __tablename__ = "simulation_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_simulation_singleton"),
        CheckConstraint(
            "\"current_time\" >= TIMESTAMPTZ '2026-09-20 08:00:00+08:00'",
            name="ck_simulation_start",
        ),
    )

    # Mapped 表示 Python 中的数据类型
    # mapped_column(...) 描述数据库列
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
    )
    current_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("TIMESTAMPTZ '2026-09-20 08:00:00+08:00'"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

class OperationLog(Base):
    __tablename__ = "operation_logs"
    __table_args__ = (
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_logs_hash",
        ),
        CheckConstraint(
            "resource_type IN "
            "('ORDER', 'SHIPMENT', 'TRANSPORT_TASK', 'CLOCK', 'STATION', 'ROUTE')",
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

class Order(Base):
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

class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (
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
    __tablename__ = "transport_tasks"
    __table_args__ = (
        CheckConstraint(
            "(status = 'PENDING_DEPARTURE' "
            "AND departed_at IS NULL "
            "AND arrived_at IS NULL) "
            "OR "
            "(status = 'IN_TRANSIT' "
            "AND departed_at IS NOT NULL "
            "AND arrived_at IS NULL) "
            "OR "
            "(status = 'ARRIVED' "
            "AND departed_at IS NOT NULL "
            "AND arrived_at IS NOT NULL "
            "AND arrived_at >= departed_at)",
            name="ck_tasks_status_times",
        ),
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

class TaskShipment(Base):
    __tablename__ = "task_shipments"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "shipment_id",
            name="uq_task_shipments",
        ),
        Index(
            "uq_task_shipments_active",
            "shipment_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
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
