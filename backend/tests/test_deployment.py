import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect

from alembic import command
from app.db import Base, make_engine


def legacy_seed_records(engine):
    """Copy the deterministic fixture through reflected pre-geography columns.

    Never run the current ORM against a historical schema or modify its migrations.
    """
    from sqlalchemy import MetaData, Table, select

    from app.db import make_session_factory
    from app.seed import seed_demo

    fixture = make_engine("sqlite://")
    try:
        Base.metadata.create_all(fixture)
        with make_session_factory(fixture)() as session:
            seed_demo(session, dispatcher_password="dispatch-test", driver_password="driver-test")
        metadata = MetaData()
        with fixture.connect() as source, engine.begin() as target:
            for name in ("users", "nodes", "vehicles", "orders", "route_stops", "routing_graphs"):
                old = Table(name, metadata, autoload_with=engine)
                values = [
                    {c.name: row[c.name] for c in old.columns}
                    for row in source.execute(select(Base.metadata.tables[name])).mappings()
                ]
                target.execute(old.insert(), values)
    finally:
        fixture.dispose()


def legacy_workflow_records(engine):
    """Insert frozen-0002 rows, before ORM workflow columns exist."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import MetaData, Table

    metadata = MetaData()
    tables = {
        name: Table(name, metadata, autoload_with=engine)
        for name in ("incidents", "plans", "jobs", "reservations")
    }
    now = datetime.now(UTC)
    common = {"tenant_id": "DEMO", "version": 1, "created_at": now, "updated_at": now}
    with engine.begin() as connection:
        for name, status in (
            ("pending", "AWAITING_APPROVAL"),
            ("approved", "AWAITING_ACK"),
            ("computing", "ANALYZING"),
            ("ambiguous", "ANALYZING"),
            ("many-families", "AWAITING_APPROVAL"),
        ):
            connection.execute(
                tables["incidents"]
                .insert()
                .values(
                    **common,
                    id=name,
                    coordination_version=1,
                    user_id="DRIVER01",
                    vehicle_id="V01",
                    event_type="ROAD_DELAY",
                    status=status,
                    description="SIMULATION legacy workflow",
                    delay_minutes=20,
                    reported_at=now,
                    facts={},
                )
            )
        for name, incident_id, group_id, status in (
            ("pending-plan", "pending", "pending-family", "PENDING_APPROVAL"),
            ("selected-plan", "approved", "approved-family", "APPROVED_PENDING_ACK"),
            ("alternative", "approved", "approved-family", "PENDING_APPROVAL"),
            ("family-one", "many-families", "one", "PENDING_APPROVAL"),
            ("family-two", "many-families", "two", "PENDING_APPROVAL"),
        ):
            connection.execute(
                tables["plans"]
                .insert()
                .values(
                    **common,
                    id=name,
                    plan_group_id=group_id,
                    analysis_id="analysis",
                    incident_id=incident_id,
                    incident_version=1,
                    snapshot_id="snapshot",
                    status=status,
                    expires_at=now + timedelta(seconds=300),
                    metrics={},
                    changes=[],
                    constraints={},
                    dependencies=[],
                    target_routes=[],
                    required_driver_ids=["DRIVER01"],
                )
            )
        for name, incident_id in (
            ("current-job", "computing"),
            ("ambiguous-one", "ambiguous"),
            ("ambiguous-two", "ambiguous"),
        ):
            connection.execute(
                tables["jobs"]
                .insert()
                .values(
                    **common,
                    id=name,
                    user_id="DRIVER01",
                    operation_id="operation",
                    job_type="IMPACT_ANALYSIS",
                    status="QUEUED",
                    incident_id=incident_id,
                    input={
                        "expected_incident_version": 1,
                        "incident_coordination_version": 1,
                        "snapshot_id": "snapshot",
                    },
                    attempts=0,
                )
            )
        connection.execute(
            tables["reservations"]
            .insert()
            .values(
                **common,
                id="selected-lease",
                plan_id="selected-plan",
                vehicle_id="V01",
                expires_at=now + timedelta(seconds=60),
            )
        )


def assert_legacy_workflows_preserved(engine):
    from sqlalchemy import select, text

    from app import models as m
    from app.db import make_session_factory
    from app.services.lifecycle import release_for_review

    factory = make_session_factory(engine)
    with factory() as session, session.begin():
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
        pending = session.get(m.Incident, "pending")
        assert pending.status == "AWAITING_APPROVAL"
        assert pending.current_plan_group_id == "pending-family"
        release_for_review(session, session.get(m.Plan, "pending-plan"))
        assert pending.status == "MANUAL_REVIEW" and pending.coordination_version == 1
        approved = session.get(m.Incident, "approved")
        assert approved.current_plan_group_id == "approved-family"
        release_for_review(session, session.get(m.Plan, "alternative"))
        assert approved.status == "AWAITING_ACK" and approved.coordination_version == 1
        assert session.get(m.Plan, "selected-plan").status == "APPROVED_PENDING_ACK"
        assert session.get(m.Reservation, "selected-lease").plan_id == "selected-plan"
        computing = session.get(m.Incident, "computing")
        assert (
            computing.status == "ANALYZING"
            and computing.current_calculation_job_id == "current-job"
        )
        for name in ("ambiguous", "many-families"):
            incident = session.get(m.Incident, name)
            assert incident.status == "MANUAL_REVIEW" and incident.coordination_version == 2
            assert (
                incident.current_calculation_job_id is None
                and incident.current_plan_group_id is None
            )
        assert all(incident.version == 1 for incident in session.scalars(select(m.Incident)))


def test_upgrade_0002_preserves_workflows_and_fences_ambiguous_legacy_ownership(
    tmp_path, monkeypatch
):
    url = "sqlite:///" + str(tmp_path / "workflow-upgrade.db")
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "0002")
    engine = make_engine(url)
    legacy_workflow_records(engine)
    command.upgrade(config, "head")
    command.check(config)
    assert_legacy_workflows_preserved(engine)
    engine.dispose()


def test_frozen_migration_creates_current_schema_without_app_types(tmp_path, monkeypatch):
    url = "sqlite:///" + str(tmp_path / "migration.db")
    monkeypatch.setenv("DATABASE_URL", url)
    for path in Path("alembic/versions").glob("*.py"):
        source = path.read_text()
        assert "app.db" not in source and "create_all" not in source
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    engine = make_engine(url)
    inspector = inspect(engine)
    for name, table in Base.metadata.tables.items():
        columns = {item["name"] for item in inspector.get_columns(name)}
        assert columns == set(table.columns.keys())
    engine.dispose()


def test_upgrade_0001_preserves_seed_and_accepted_operations(tmp_path, monkeypatch):
    from sqlalchemy import func, select

    from app import models as m
    from app.db import make_session_factory
    from app.services.common import expires

    url = "sqlite:///" + str(tmp_path / "upgrade.db")
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "0001")
    engine = make_engine(url)
    factory = make_session_factory(engine)
    legacy_seed_records(engine)
    with factory() as session:
        session.add(
            m.Operation(
                id="accepted",
                tenant_id="DEMO",
                user_id="DRIVER01",
                action="upsert_incident",
                idempotency_key="original-key",
                body_hash="0" * 64,
                expires_at=expires(86400),
                response={"data": {"incident_id": "preserved"}},
            )
        )
        session.commit()
    command.upgrade(config, "head")
    command.check(config)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(m.Vehicle)) == 5
        assert session.scalar(select(func.count()).select_from(m.Order)) == 20
        assert session.get(m.Operation, "accepted").response == {
            "data": {"incident_id": "preserved"}
        }
        assert session.scalar(select(func.count()).select_from(m.OperationAlias)) == 0
        assert all(
            n.longitude is None and n.latitude is None and n.coordinate_source is None
            for n in session.scalars(select(m.Node))
        )
        graph = session.scalar(select(m.RoutingGraph))
        assert graph.matrix == graph.simulation_matrix
        assert graph.routing_version == graph.simulation_routing_version == "R1"
    engine.dispose()


def test_liveness_and_readiness_are_separate(flow):
    assert flow.client.get("/api/v1/health").status_code == 200
    # Unit fixture deliberately has neither migration version nor Redis readiness.
    assert flow.client.get("/api/v1/ready").status_code == 503


def test_readiness_requires_geography_migration(flow, monkeypatch):
    from pydantic import SecretStr
    from redis import Redis
    from sqlalchemy import text

    from app.db import make_session_factory

    class ReadyRedis:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ping(self):
            return True

    monkeypatch.setattr(Redis, "from_url", lambda *args, **kwargs: ReadyRedis())
    # This dependency gate unit test owns its DB; never create a version table in
    # PostgreSQL's default schema (raw readiness SQL has no schema translation).
    engine = make_engine("sqlite://")
    factory = make_session_factory(engine)
    monkeypatch.setattr(flow.app.state, "session_factory", factory)
    monkeypatch.setattr(flow.app.state.settings, "tool_service_key", SecretStr("s" * 40))
    try:
        with factory() as session, session.begin():
            session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            session.execute(text("INSERT INTO alembic_version VALUES ('0002')"))
        assert flow.client.get("/api/v1/ready").status_code == 503
        with factory() as session, session.begin():
            session.execute(text("UPDATE alembic_version SET version_num = '0003'"))
        assert flow.client.get("/api/v1/ready").status_code == 503
        with factory() as session, session.begin():
            session.execute(text("UPDATE alembic_version SET version_num = '0004'"))
        assert flow.client.get("/api/v1/ready").status_code == 200
    finally:
        engine.dispose()


def test_compose_json_override_and_private_service_boundaries(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI not available")
    envfile = tmp_path / "compose.env"
    envfile.write_text(
        "\n".join(
            f"{name}=" + "x" * 40
            for name in (
                "POSTGRES_PASSWORD",
                "REDIS_PASSWORD",
                "LOGIN_SIGNING_KEY",
                "TASK_TOKEN_SIGNING_KEY",
                "TOOL_SERVICE_KEY",
                "DEMO_DISPATCHER_PASSWORD",
                "DEMO_DRIVER_PASSWORD",
            )
        )
        + '\nDEEPSEEK_MODEL=deepseek-flash\nCORS_ALLOW_ORIGINS=["https://ui.test"]\n'
    )
    isolated = {k: v for k, v in os.environ.items() if not k.startswith(("DEEPSEEK_", "CORS_"))}
    command_line = [
        "docker",
        "compose",
        "-f",
        str(Path("../compose.yaml").resolve()),
        "--env-file",
        str(envfile),
        "config",
        "--format",
        "json",
    ]
    result = subprocess.run(
        command_line, capture_output=True, text=True, encoding="utf-8", env=isolated, check=False
    )
    assert result.returncode == 0, "Compose configuration rejected"
    config = json.loads(result.stdout)
    services = config["services"]
    assert services["api"]["environment"]["DEEPSEEK_MODEL"] == "deepseek-flash"
    assert json.loads(services["api"]["environment"]["CORS_ALLOW_ORIGINS"]) == ["https://ui.test"]
    assert not any(name.startswith("AGENTARTS_") for name in services["api"]["environment"])
    assert not services["postgres"].get("ports") and not services["redis"].get("ports")
    assert services["api"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert all("container_name" not in service for service in services.values())
