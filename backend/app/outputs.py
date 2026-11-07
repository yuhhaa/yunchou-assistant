"""Typed public response contracts, also used to validate actual responses in tests."""

from typing import Literal

from pydantic import JsonValue

from app.platform import NextAction, RunResult
from app.schemas import (
    Identifier,
    OrderInput,
    RouteStopInput,
    StrictModel,
    Timestamp,
    VehicleInput,
)

JobStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "UNKNOWN"]


class ErrorDetail(StrictModel):
    code: str
    reason: str
    missing_fields: list[str]
    retryable: bool
    next_action: NextAction
    details: dict[str, JsonValue] | None


class Meta(StrictModel):
    contract_version: Literal["1.0"]
    data_time: Timestamp
    replayed: bool


class Envelope[T](StrictModel):
    code: str
    message: str
    data: T | None
    error: ErrorDetail | None
    request_id: Identifier
    operation_id: Identifier | None
    meta: Meta


class VehicleOutput(VehicleInput):
    driver_id: Identifier | None
    facts: dict[str, JsonValue]


class OrderOutput(OrderInput):
    facts: dict[str, JsonValue]


class ContextOutput(StrictModel):
    snapshot_id: Identifier
    snapshot_version: int
    valid_until: Timestamp
    clock_mode: Literal["REALTIME", "SIMULATION"]
    clock_time: Timestamp
    source: Literal["SIMULATION", "BUSINESS"]
    vehicles: list[VehicleOutput]
    orders: list[OrderOutput]
    route_stops: list[RouteStopInput]
    routing_version: str | None
    routing_source: Literal["SIMULATION", "BUSINESS", "AMAP"] | None = None
    routing_ready: bool | None = None
    routing_id: Identifier | None = None
    ruleset_version: str | None = None
    fleet_set_version: str | None = None
    warehouse_node_id: Identifier | None = None
    missing_fields: list[str]
    selected_order_no: Identifier | None


class IncidentOutput(StrictModel):
    incident_id: Identifier
    incident_version: int
    event_type: Literal["ROAD_DELAY", "RECEIVING_WINDOW_CHANGE", "VEHICLE_BREAKDOWN"]
    vehicle_id: Identifier
    order_no: Identifier | None
    status: str
    description: str
    delay_minutes: int | None
    reported_window_start: Timestamp | None
    reported_window_end: Timestamp | None
    reported_at: Timestamp
    occurred_at: Timestamp | None
    can_analyze: bool
    superseded_plan_ids: list[Identifier]
    next_action: NextAction


class QueuedOutput(StrictModel):
    job_id: Identifier
    job_type: Literal["IMPACT_ANALYSIS", "PLAN_GENERATION"]
    job_status: Literal["QUEUED"]
    incident_id: Identifier
    poll_after_seconds: Literal[2]


class ChatQueuedOutput(StrictModel):
    conversation_id: Identifier
    turn_id: Identifier
    job_id: Identifier
    job_type: Literal["CHAT"]
    job_status: Literal["QUEUED"]
    poll_after_seconds: Literal[2]


class ConversationOutput(StrictModel):
    conversation_id: Identifier
    created_at: Timestamp
    active_turn_id: Identifier | None
    selected_order_no: Identifier | None
    incident_id: Identifier | None


class ConversationListOutput(StrictModel):
    items: list[ConversationOutput]
    next_offset: int | None


class ConversationTurnOutput(StrictModel):
    turn_id: Identifier
    job_id: Identifier
    status: JobStatus
    created_at: Timestamp
    user_message: str
    assistant_reply: str | None


class ConversationMessagesOutput(StrictModel):
    conversation_id: Identifier
    items: list[ConversationTurnOutput]
    next_offset: int | None


class Violation(StrictModel):
    code: str
    entity_ids: list[str]
    message: str


class Constraints(StrictModel):
    passed: bool
    violations: list[Violation]
    checked_at: Timestamp


class OrderImpact(StrictModel):
    order_no: Identifier
    original_eta: Timestamp | None
    updated_eta: Timestamp
    latest_arrival: Timestamp
    delay_minutes: int
    risk_type: Literal["NONE", "SOFT_WINDOW_LATE", "HARD_CONSTRAINT_VIOLATION"]
    evidence_refs: list[str]


class AnalysisOutput(StrictModel):
    analysis_id: Identifier
    incident_id: Identifier
    incident_version: int
    snapshot_id: Identifier
    analysis_status: Literal["VALID", "STALE"]
    risk_order_count: int
    order_impacts: list[OrderImpact]
    missing_fields: list[str]


class Metrics(StrictModel):
    unresolved_order_count: int
    late_order_count: int
    high_priority_delay_minutes: int
    total_delay_minutes: int
    changed_order_count: int
    reassigned_order_count: int
    sequence_inversion_count: int
    total_distance_m: int
    extra_distance_m: int
    computation_ms: int


class Change(StrictModel):
    change_type: Literal["RESEQUENCE", "REASSIGN"]
    order_no: Identifier
    from_vehicle_id: Identifier
    to_vehicle_id: Identifier
    old_sequence_no: int
    new_sequence_no: int


class Dependency(StrictModel):
    entity_type: Literal["INCIDENT", "VEHICLE", "ORDER", "ROUTING", "RULESET", "FLEET_SET"]
    entity_id: Identifier
    version: str


class PlanOutput(StrictModel):
    plan_id: Identifier
    plan_group_id: Identifier
    plan_version: int
    incident_id: Identifier
    incident_version: int
    snapshot_id: Identifier
    analysis_id: Identifier
    status: Literal[
        "PENDING_APPROVAL", "APPROVED_PENDING_ACK", "ACTIVE", "STALE", "REJECTED", "SUPERSEDED"
    ]
    expires_at: Timestamp
    metrics: Metrics
    changes: list[Change]
    constraints: Constraints
    dependencies: list[Dependency]
    required_driver_ids: list[Identifier]
    target_routes: list[RouteStopInput]


class SearchScope(StrictModel):
    vehicle_ids: list[Identifier]
    order_nos: list[Identifier]
    budget_ms: int
    method: Literal["BOUNDED_SAME_VEHICLE_RESEQUENCE"]
    max_permutations: int


class IncidentWorkspaceOutput(StrictModel):
    incident: IncidentOutput
    analyses: list[AnalysisOutput]
    plans: list[PlanOutput]


class GenerationOutput(StrictModel):
    solution_status: Literal["FEASIBLE", "NO_FEASIBLE_PLAN_IN_SEARCH"]
    search_scope: SearchScope
    plans: list[PlanOutput]
    infeasible_reasons: list[Violation]
    computed_at: Timestamp
    plan_group_id: Identifier
    analysis_id: Identifier


class OperationOutput(StrictModel):
    operation_id: Identifier
    operation_type: str
    operation_status: str
    resource_type: str | None
    resource_id: Identifier | None
    job_id: Identifier | None


class MapRoutingOutput(StrictModel):
    routing_version: str
    source: Literal["AMAP"]
    coordinate_system: Literal["GCJ02"]
    routing_profile: Literal["DRIVING_CAR"]
    node_versions: dict[str, int]
    node_count: int
    published_at: Timestamp


class MapQueuedOutput(StrictModel):
    job_id: Identifier
    job_type: Literal["MAP_ROUTING"]
    job_status: Literal["QUEUED"]
    poll_after_seconds: Literal[2]


class MapNodeOutput(StrictModel):
    node_id: Identifier
    name: str
    node_type: str
    longitude: float | None
    latitude: float | None
    coordinate_system: Literal["GCJ02"] | None
    address: str | None
    source: Literal["DISPATCHER_CONFIRMED"] | None
    updated_at: Timestamp | None
    version: int


class MapVehicleOutput(StrictModel):
    vehicle_id: Identifier
    route_revision: int
    location_node_id: Identifier
    location_basis: Literal["NODE"]


class MapOutput(StrictModel):
    provider: Literal["AMAP"]
    configuration_status: Literal["CONFIGURED", "UNCONFIGURED"]
    coordinate_system: Literal["GCJ02"]
    clock_mode: Literal["SIMULATION", "REALTIME"]
    clock_time: Timestamp
    routing_source: Literal["SIMULATION", "BUSINESS", "AMAP"]
    routing_version: str
    routing_ready: bool
    missing_fields: list[str]
    points: list[MapNodeOutput]
    vehicles: list[MapVehicleOutput]


class GeocodeCandidate(StrictModel):
    longitude: float
    latitude: float
    formatted_address: str | None
    province: str | None
    city: str | None
    district: str | None
    adcode: str | None


class GeocodeOutput(StrictModel):
    source: Literal["AMAP"]
    coordinate_system: Literal["GCJ02"]
    candidates: list[GeocodeCandidate]
    retrieved_at: Timestamp


class GCJ02LineString(StrictModel):
    type: Literal["LineString"]
    coordinates: list[tuple[float, float]]


class MapRouteOutput(StrictModel):
    vehicle_id: Identifier
    vehicle_version: int
    route_revision: int
    plan_id: Identifier | None
    plan_version: int | None
    routing_version: str
    node_versions: dict[str, int]
    node_sequence: list[Identifier]
    distance_m: int
    duration_seconds: int
    geometry: GCJ02LineString | None
    source: Literal["AMAP"]
    coordinate_system: Literal["GCJ02"]
    geometry_standard: Literal["GCJ02_GEOJSON_SHAPED"]
    routing_profile: Literal["DRIVING_CAR"]
    route_kind: Literal["CURRENT", "CANDIDATE_PREVIEW"]
    retrieved_at: Timestamp
    cached: bool


class ToolStatusOutput(StrictModel):
    object_type: Literal["INCIDENT", "ANALYSIS", "PLAN", "JOB", "OPERATION", "REQUEST"]
    object_id: str
    job_status: JobStatus | None
    result_kind: Literal[
        "INCIDENT", "RECOVERY_PLAN", "IMPACT_ANALYSIS", "PLAN_GENERATION", "OPERATION"
    ]
    result: IncidentOutput | PlanOutput | AnalysisOutput | GenerationOutput | OperationOutput | None
    error: ErrorDetail | None


class StatusOutput(ToolStatusOutput):
    object_type: Literal["INCIDENT", "ANALYSIS", "PLAN", "JOB", "OPERATION", "REQUEST"]
    object_id: str
    job_status: JobStatus | None
    result_kind: Literal[
        "INCIDENT",
        "RECOVERY_PLAN",
        "IMPACT_ANALYSIS",
        "PLAN_GENERATION",
        "OPERATION",
        "MAP_ROUTING",
        "CHAT",
    ]
    result: (
        IncidentOutput
        | PlanOutput
        | AnalysisOutput
        | GenerationOutput
        | OperationOutput
        | MapRoutingOutput
        | RunResult
        | None
    )
    error: ErrorDetail | None


class JobOutput(StrictModel):
    job_id: Identifier
    job_type: Literal["CHAT", "IMPACT_ANALYSIS", "PLAN_GENERATION", "MAP_ROUTING"]
    job_status: JobStatus
    result_kind: Literal["CHAT", "IMPACT_ANALYSIS", "PLAN_GENERATION", "MAP_ROUTING"]
    result: RunResult | AnalysisOutput | GenerationOutput | MapRoutingOutput | None
    error: ErrorDetail | None
    poll_after_seconds: Literal[2, 5]


class DemoMapPoint(StrictModel):
    node_id: Identifier
    name: str
    longitude: float
    latitude: float
    source: Literal["SIMULATION"]


class DemoMapRoute(StrictModel):
    vehicle_id: Identifier
    route_revision: int
    plan_id: Identifier | None
    plan_version: int | None
    route_kind: Literal["CURRENT", "CANDIDATE_PREVIEW"]
    node_sequence: list[Identifier]
    geometry_kind: Literal["SCHEMATIC"]
    geometry: GCJ02LineString | None
    distance_m: None
    duration_seconds: None


class DemoMapOutput(StrictModel):
    city: Literal["武汉市"]
    source: Literal["SIMULATION"]
    simulation: Literal[True]
    dataset_version: Literal["WUHAN_DEMO_V1"]
    coordinate_system: Literal["GCJ02"]
    road_service_used: Literal[False]
    center: tuple[float, float]
    clock_mode: Literal["SIMULATION", "REALTIME"]
    clock_time: Timestamp
    calculation_routing_source: Literal["SIMULATION", "BUSINESS", "AMAP"]
    calculation_routing_version: str
    retrieved_at: Timestamp
    notice: str
    points: list[DemoMapPoint]
    routes: list[DemoMapRoute]
