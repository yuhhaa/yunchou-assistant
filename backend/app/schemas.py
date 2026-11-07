from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    model_validator,
)
from pydantic_core import PydanticCustomError

Identifier = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Version = Annotated[StrictInt, Field(ge=1)]
Minutes = Annotated[StrictInt, Field(ge=0, le=1440)]
Quantity = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
Capacity = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]


def _iso_timestamp(value):
    if not isinstance(value, (str, datetime)):
        raise ValueError("Timestamp must be ISO8601 with timezone")  # noqa: TRY004 (Pydantic validator)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Timestamp must be ISO8601 with timezone")
    return value


Timestamp = Annotated[AwareDatetime, BeforeValidator(_iso_timestamp)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Longitude = Annotated[float, Field(strict=True, ge=-180, le=180, allow_inf_nan=False)]
Latitude = Annotated[float, Field(strict=True, ge=-90, le=90, allow_inf_nan=False)]


class MapNodeInput(StrictModel):
    expected_node_version: Version
    longitude: Longitude
    latitude: Latitude
    coordinate_system: Literal["GCJ02"]
    name: (
        Annotated[
            str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=128)
        ]
        | None
    ) = None
    address: (
        Annotated[
            str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)
        ]
        | None
    ) = None


class MapRefreshInput(StrictModel):
    expected_routing_version: Identifier
    node_ids: Annotated[list[Identifier], Field(min_length=1, max_length=21)] | None = None


class IncidentInput(StrictModel):
    mode: Literal["CREATE", "UPDATE"]
    incident_id: Identifier | None = None
    expected_incident_version: Version | None = None
    event_type: Literal["ROAD_DELAY", "RECEIVING_WINDOW_CHANGE", "VEHICLE_BREAKDOWN"] | None = None
    vehicle_id: Identifier | None = None
    order_no: Identifier | None = None
    description: (
        Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1000)] | None
    ) = None
    delay_minutes: Minutes | None = None
    reported_window_start: Timestamp | None = None
    reported_window_end: Timestamp | None = None
    occurred_at: Timestamp | None = None

    @model_validator(mode="after")
    def conditions(self):
        if self.mode == "CREATE":
            if self.incident_id is not None or self.expected_incident_version is not None:
                raise ValueError("CREATE forbids incident ID and expected version")
            if not self.event_type or not self.vehicle_id or not self.description:
                raise PydanticCustomError(
                    "business_missing_fields",
                    "Missing incident details",
                    {
                        "missing_fields": [
                            name
                            for name in ("event_type", "vehicle_id", "description")
                            if not getattr(self, name)
                        ]
                    },
                )
            if self.event_type == "ROAD_DELAY" and self.delay_minutes is None:
                raise PydanticCustomError(
                    "business_missing_fields",
                    "Road delay requires explicit estimate",
                    {"missing_fields": ["delay_minutes"]},
                )
            if self.event_type == "RECEIVING_WINDOW_CHANGE" and (
                not self.order_no or not self.reported_window_start or not self.reported_window_end
            ):
                raise PydanticCustomError(
                    "business_missing_fields",
                    "Window change requires order and windows",
                    {
                        "missing_fields": [
                            name
                            for name in ("order_no", "reported_window_start", "reported_window_end")
                            if getattr(self, name) is None
                        ]
                    },
                )
            if self.event_type != "ROAD_DELAY" and self.delay_minutes is not None:
                raise ValueError("delay_minutes only applies to ROAD_DELAY")
            if self.event_type != "RECEIVING_WINDOW_CHANGE" and (
                self.reported_window_start or self.reported_window_end
            ):
                raise ValueError("Reported windows only apply to window change")
        else:
            if not self.incident_id or not self.expected_incident_version:
                raise PydanticCustomError(
                    "business_missing_fields",
                    "UPDATE requires ID and version",
                    {
                        "missing_fields": [
                            name
                            for name in ("incident_id", "expected_incident_version")
                            if getattr(self, name) is None
                        ]
                    },
                )
            if (
                self.event_type is not None
                or self.vehicle_id is not None
                or self.order_no is not None
            ):
                raise ValueError("UPDATE cannot change incident identity")
            mutable = {
                "description",
                "delay_minutes",
                "reported_window_start",
                "reported_window_end",
                "occurred_at",
            }
            if not any(getattr(self, f) is not None for f in mutable):
                raise ValueError("UPDATE requires a mutable business field")
        if (self.reported_window_start is None) != (self.reported_window_end is None):
            raise ValueError("Reported window values must be paired")
        if self.reported_window_start and self.reported_window_start > self.reported_window_end:
            raise ValueError("Reported window start must not exceed end")
        return self


class OrderInput(StrictModel):
    order_no: Identifier
    version: Version
    assigned_vehicle_id: Identifier
    cargo_status: Literal["NOT_LOADED", "ON_VEHICLE", "DELIVERED"]
    status: Literal["PENDING", "IN_TRANSIT", "COMPLETED", "CANCELLED"]
    delivery_node_id: Identifier
    earliest_arrival: Timestamp
    latest_arrival: Timestamp
    window_type: Literal["HARD", "SOFT"]
    service_minutes: Minutes
    weight_kg: Quantity
    volume_m3: Quantity
    priority: Literal["HIGH", "NORMAL", "LOW"]

    @model_validator(mode="after")
    def windows(self):
        if self.earliest_arrival > self.latest_arrival:
            raise ValueError("earliest_arrival must not exceed latest_arrival")
        return self


class VehicleInput(StrictModel):
    vehicle_id: Identifier
    vehicle_no: Identifier
    version: Version
    status: Literal["AVAILABLE", "ON_TASK", "UNAVAILABLE"]
    location_node_id: Identifier
    location_time: Timestamp
    capacity_kg: Capacity
    capacity_m3: Capacity
    route_revision: Version


class RouteStopInput(StrictModel):
    stop_id: Identifier
    vehicle_id: Identifier
    order_no: Identifier
    sequence_no: Version
    node_id: Identifier
    stop_type: Literal["DELIVERY"]
    status: Literal["PENDING", "ARRIVED", "COMPLETED", "CANCELLED"]


class ImpactInput(StrictModel):
    incident_id: Identifier
    expected_incident_version: Version
    snapshot_id: Identifier


class PlanGenerationInput(ImpactInput):
    analysis_id: Identifier
    strategy_profile: Literal["MIN_CHANGE_V1"] = "MIN_CHANGE_V1"


class StatusInput(StrictModel):
    object_type: Literal["INCIDENT", "ANALYSIS", "PLAN", "JOB", "OPERATION", "REQUEST"]
    object_id: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{1,256}$")]

    @model_validator(mode="after")
    def object_identifier(self):
        if self.object_type != "REQUEST" and len(self.object_id) > 64:
            raise ValueError("Object IDs are at most 64 characters")
        return self


class LoginInput(StrictModel):
    username: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=128)]
    password: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256)]


class ApprovalInput(StrictModel):
    expected_plan_version: Version
    expected_incident_version: Version
    snapshot_id: Identifier


class AcknowledgementInput(StrictModel):
    expected_plan_version: Version
    accepted: StrictBool


class ReceiptInput(StrictModel):
    expected_plan_version: Version
    decision: Literal["ACCEPTED", "REJECTED"]
    reason: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1000)] | None = (
        None
    )

    @model_validator(mode="after")
    def rejected_reason(self):
        if self.decision == "REJECTED" and not self.reason:
            raise PydanticCustomError(
                "business_missing_fields",
                "Rejection requires reason",
                {"missing_fields": ["reason"]},
            )
        return self


class TaskTokenInput(StrictModel):
    vehicle_id: Identifier | None = None
    order_no: Identifier | None = None


class ChatInput(StrictModel):
    message: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=4000)
    ]
    conversation_id: Identifier | None = None
    selected_order_no: Identifier | None = None
    incident_id: Identifier | None = None


class WindowVerificationInput(StrictModel):
    order_no: Identifier
    expected_order_version: Version
    expected_incident_version: Version
    window_start: Timestamp
    window_end: Timestamp

    @model_validator(mode="after")
    def windows(self):
        if self.window_start > self.window_end:
            raise ValueError("Window start must not exceed end")
        return self


class RejectionInput(StrictModel):
    expected_plan_version: Version
    reason: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1000)]


class HandoffInput(StrictModel):
    expected_incident_version: Version
    reason: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1000)]


class CompletionInput(StrictModel):
    event_type: Literal["ARRIVED", "COMPLETED"]
    expected_route_revision: Version
    occurred_at: Timestamp
