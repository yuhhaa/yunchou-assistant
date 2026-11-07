"""Durable domain records; JSON payloads are replaced, never edited in place."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .db import UTCDateTime as DateTime


def utcnow():
    return datetime.now(UTC)


def new_id():
    return uuid4().hex


class Entity:
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class User(Entity, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "username"),)
    username: Mapped[str] = mapped_column(String(128), index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32))
    vehicle_id: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Node(Entity, Base):
    __tablename__ = "nodes"
    name: Mapped[str] = mapped_column(String(128))
    node_type: Mapped[str] = mapped_column(String(32), default="CUSTOMER")
    source: Mapped[str] = mapped_column(String(32), default="SIMULATION")
    longitude: Mapped[float | None] = mapped_column(Float)
    latitude: Mapped[float | None] = mapped_column(Float)
    coordinate_system: Mapped[str | None] = mapped_column(String(16))
    address: Mapped[str | None] = mapped_column(String(200))
    coordinate_source: Mapped[str | None] = mapped_column(String(32))
    coordinate_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Vehicle(Entity, Base):
    __tablename__ = "vehicles"
    vehicle_no: Mapped[str] = mapped_column(String(64))
    driver_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="ON_TASK")
    location_node_id: Mapped[str] = mapped_column(String(64))
    location_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    capacity_kg: Mapped[float] = mapped_column(Float)
    capacity_m3: Mapped[float] = mapped_column(Float)
    route_revision: Mapped[int] = mapped_column(Integer, default=1)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)


class Order(Entity, Base):
    __tablename__ = "orders"
    assigned_vehicle_id: Mapped[str] = mapped_column(String(64), index=True)
    cargo_status: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    delivery_node_id: Mapped[str] = mapped_column(String(64))
    earliest_arrival: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    latest_arrival: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_type: Mapped[str] = mapped_column(String(16))
    service_minutes: Mapped[int] = mapped_column(Integer)
    weight_kg: Mapped[float] = mapped_column(Float)
    volume_m3: Mapped[float] = mapped_column(Float)
    priority: Mapped[str] = mapped_column(String(16))
    facts: Mapped[dict] = mapped_column(JSON, default=dict)


class RouteStop(Entity, Base):
    __tablename__ = "route_stops"
    __table_args__ = (UniqueConstraint("tenant_id", "vehicle_id", "sequence_no"),)
    vehicle_id: Mapped[str] = mapped_column(String(64), index=True)
    order_no: Mapped[str] = mapped_column(String(64))
    sequence_no: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str] = mapped_column(String(64))
    stop_type: Mapped[str] = mapped_column(String(32), default="DELIVERY")
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoutingGraph(Entity, Base):
    __tablename__ = "routing_graphs"
    routing_version: Mapped[str] = mapped_column(String(64))
    matrix: Mapped[dict] = mapped_column(JSON)
    ruleset_version: Mapped[str] = mapped_column(String(64), default="MIN_CHANGE_V1")
    fleet_set_version: Mapped[str] = mapped_column(String(64), default="1")
    source: Mapped[str] = mapped_column(String(32), default="SIMULATION")
    ready: Mapped[bool] = mapped_column(Boolean, default=True)
    node_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    simulation_matrix: Mapped[dict | None] = mapped_column(JSON)
    simulation_routing_version: Mapped[str | None] = mapped_column(String(64))


class MapRouteCache(Entity, Base):
    __tablename__ = "map_route_cache"
    __table_args__ = (UniqueConstraint("tenant_id", "cache_key"),)
    cache_key: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Incident(Entity, Base):
    __tablename__ = "incidents"
    # Manual coordination fences workers independently of calculation-fact version.
    coordination_version: Mapped[int] = mapped_column(Integer, default=1)
    # Explicit aggregate owners; historical jobs/candidates remain queryable.
    current_calculation_job_id: Mapped[str | None] = mapped_column(String(64))
    current_plan_group_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    vehicle_id: Mapped[str] = mapped_column(String(64), index=True)
    order_no: Mapped[str | None] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text)
    delay_minutes: Mapped[int | None] = mapped_column(Integer)
    reported_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    facts: Mapped[dict] = mapped_column(JSON, default=dict)


class Snapshot(Entity, Base):
    __tablename__ = "snapshots"
    user_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Analysis(Entity, Base):
    __tablename__ = "analyses"
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    incident_version: Mapped[int] = mapped_column(Integer)
    snapshot_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="VALID")
    result: Mapped[dict] = mapped_column(JSON)


class Plan(Entity, Base):
    __tablename__ = "plans"
    plan_group_id: Mapped[str] = mapped_column(String(64), index=True)
    analysis_id: Mapped[str] = mapped_column(String(64))
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    incident_version: Mapped[int] = mapped_column(Integer)
    snapshot_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="PENDING_APPROVAL")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict] = mapped_column(JSON)
    changes: Mapped[list] = mapped_column(JSON)
    constraints: Mapped[dict] = mapped_column(JSON)
    dependencies: Mapped[list] = mapped_column(JSON)
    target_routes: Mapped[list] = mapped_column(JSON)
    required_driver_ids: Mapped[list] = mapped_column(JSON, default=list)
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Reservation(Entity, Base):
    __tablename__ = "reservations"
    __table_args__ = (UniqueConstraint("tenant_id", "vehicle_id"),)
    plan_id: Mapped[str] = mapped_column(String(64), index=True)
    vehicle_id: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DriverAcknowledgement(Entity, Base):
    __tablename__ = "driver_acknowledgements"
    __table_args__ = (UniqueConstraint("tenant_id", "plan_id", "driver_id"),)
    plan_id: Mapped[str] = mapped_column(String(64), index=True)
    driver_id: Mapped[str] = mapped_column(String(64))
    accepted: Mapped[bool] = mapped_column(Boolean)


class Operation(Entity, Base):
    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "idempotency_key"),)
    user_id: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(256))
    action: Mapped[str] = mapped_column(String(128))
    # Durable authority target also survives a failed response with data=None.
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    body_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="SUCCEEDED")
    response_status: Mapped[int] = mapped_column(Integer, default=200)
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OperationAlias(Entity, Base):
    __tablename__ = "operation_aliases"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "idempotency_key"),)
    user_id: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(256))
    operation_id: Mapped[str] = mapped_column(ForeignKey("operations.id"))


class Job(Entity, Base):
    __tablename__ = "jobs"
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    operation_id: Mapped[str] = mapped_column(String(64))
    job_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    incident_id: Mapped[str | None] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[dict | None] = mapped_column(JSON)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Entity, Base):
    __tablename__ = "audit_events"
    user_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    operation_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class OutboxEvent(Entity, Base):
    __tablename__ = "outbox_events"
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Entity, Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "event_id"),)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    event_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginSession(Entity, Base):
    __tablename__ = "login_sessions"
    user_id: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Conversation(Entity, Base):
    __tablename__ = "conversations"
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    platform_session_id: Mapped[str | None] = mapped_column(String(256))
    incident_id: Mapped[str | None] = mapped_column(String(64))
    active_turn_id: Mapped[str | None] = mapped_column(String(64))
    context: Mapped[dict] = mapped_column(JSON, default=dict)


class ChatTurn(Entity, Base):
    __tablename__ = "chat_turns"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="QUEUED")
    user_message: Mapped[str] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSON)
    trace_id: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str] = mapped_column(String(64), unique=True)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    # Immutable logical call keys/results survive process and platform retries.
    tool_state: Mapped[dict] = mapped_column(JSON, default=dict)
