import importlib

import pytest
from pydantic import ValidationError


def schemas():
    try:
        return importlib.import_module("app.schemas")
    except ModuleNotFoundError:
        pytest.fail("Strict schemas not implemented")


def create(**kwargs):
    return schemas().IncidentInput(
        mode="CREATE", event_type="ROAD_DELAY", vehicle_id="V01", description="delay", **kwargs
    )


@pytest.mark.parametrize("value", ["20", True, -1, 1441, 1.5])
def test_delay_is_strict_integer(value):
    with pytest.raises(ValidationError):
        create(delay_minutes=value)


def test_unknown_identity_fields_and_naive_timestamps_rejected():
    for kwargs in (
        {"role": "DISPATCHER"},
        {"tenant_id": "T1"},
        {"occurred_at": "2026-10-26T10:00:00"},
    ):
        with pytest.raises(ValidationError):
            create(delay_minutes=20, **kwargs)
    assert create(delay_minutes=0).delay_minutes == 0


def test_conditional_incident_and_schema_constraints():
    s = schemas()
    with pytest.raises(ValidationError):
        create()
    with pytest.raises(ValidationError):
        s.IncidentInput(mode="UPDATE", incident_id="E1", expected_incident_version=1)
    with pytest.raises(ValidationError):
        s.IncidentInput(
            mode="CREATE",
            event_type="RECEIVING_WINDOW_CHANGE",
            vehicle_id="V01",
            description="change",
            order_no="O1",
            reported_window_start="2026-10-26T10:00:00+08:00",
        )
    assert s.IncidentInput(
        mode="UPDATE", incident_id="E1", expected_incident_version=1, delay_minutes=30
    )
    with pytest.raises(ValidationError):
        s.OrderInput(
            order_no="O1",
            version=1,
            assigned_vehicle_id="V01",
            cargo_status="ON_VEHICLE",
            status="IN_TRANSIT",
            delivery_node_id="A",
            earliest_arrival="2026-10-26T11:00:00+08:00",
            latest_arrival="2026-10-26T10:00:00+08:00",
            window_type="SOFT",
            service_minutes=5,
            weight_kg=float("inf"),
            volume_m3=1,
            priority="NORMAL",
        )


@pytest.mark.parametrize("value", [1792972800, "1792972800", "1792972800.5", "-1792972800"])
def test_epoch_timestamp_is_not_iso8601_input(value):
    with pytest.raises(ValidationError):
        create(delay_minutes=20, occurred_at=value)


def test_shared_timestamp_accepts_explicit_timezone_and_rejects_epoch_strings():
    from pydantic import TypeAdapter

    adapter = TypeAdapter(schemas().Timestamp)
    for value in ("2026-10-26T10:00:00+08:00", "2026-10-26T02:00:00Z"):
        assert adapter.validate_python(value).utcoffset() is not None
    with pytest.raises(ValidationError):
        adapter.validate_python("1792972800.5")


def test_status_and_human_contract_fields():
    s = schemas()
    assert s.StatusInput(object_type="REQUEST", object_id="KEY001")
    with pytest.raises(ValidationError):
        s.StatusInput(object_type="PLAN_GROUP", object_id="G001")
    assert s.ReceiptInput(decision="ACCEPTED", expected_plan_version=1)
    with pytest.raises(ValidationError):
        s.ReceiptInput(decision="REJECTED", expected_plan_version=1)
