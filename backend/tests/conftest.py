import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.schema import CreateSchema, DropSchema

from app.config import Settings
from app.db import Base, make_engine, make_session_factory
from app.models import Vehicle
from app.seed import seed_demo


@pytest.fixture
def flow():
    try:
        from app.main import create_app
    except ModuleNotFoundError:
        pytest.fail("Transactional business API is not implemented")
    engine = make_engine(os.environ.get("TEST_DATABASE_URL", "sqlite://"))
    schema = "test_" + uuid4().hex if engine.dialect.name == "postgresql" else None
    if schema:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
    bound = engine.execution_options(schema_translate_map={None: schema}) if schema else engine
    Base.metadata.create_all(bound)
    factory = make_session_factory(bound)
    with factory() as session:
        seed_demo(session, dispatcher_password="dispatch-test", driver_password="driver-test")
    settings = Settings(
        login_signing_key="l" * 40, task_token_signing_key="t" * 40, tool_service_key="service-test"
    )
    app = create_app(settings, factory)
    with TestClient(app) as client:
        yield Flow(client, app, factory)
    if schema:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
    engine.dispose()


class Flow:
    def __init__(self, client, app, factory):
        self.client, self.app, self.factory = client, app, factory
        self.tokens = {}

    def headers(self, actor="driver01", key=None):
        if actor not in self.tokens:
            password = "dispatch-test" if actor == "dispatcher" else "driver-test"
            response = self.client.post(
                "/api/v1/auth/login", json={"username": actor, "password": password}
            )
            assert response.status_code == 200, response.text
            self.tokens[actor] = response.json()["data"]["access_token"]
        return {
            "Authorization": f"Bearer {self.tokens[actor]}",
            "Idempotency-Key": key or uuid4().hex,
        }

    def post(self, path, body, actor="driver01", key=None):
        return self.client.post("/api/v1" + path, json=body, headers=self.headers(actor, key))

    def get(self, path, actor="driver01", **kwargs):
        return self.client.get("/api/v1" + path, headers=self.headers(actor), **kwargs)

    def context(self, actor="driver01", **params):
        response = self.get("/delivery-context", actor, params=params)
        assert response.status_code == 200, response.text
        return response.json()["data"]

    def incident(self, delay=20):
        response = self.post(
            "/incidents",
            {
                "mode": "CREATE",
                "event_type": "ROAD_DELAY",
                "vehicle_id": "V01",
                "description": "SIMULATION traffic",
                "delay_minutes": delay,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["data"]

    def generate(self, delay_minutes=20):
        snapshot, incident = self.context(), self.incident(delay_minutes)
        body = {
            "snapshot_id": snapshot["snapshot_id"],
            "incident_id": incident["incident_id"],
            "expected_incident_version": incident["incident_version"],
        }
        response = self.post("/impact-analyses", body)
        assert response.status_code == 202, response.text
        self.app.state.business.run_job(response.json()["data"]["job_id"])
        job = self.get("/jobs/" + response.json()["data"]["job_id"]).json()["data"]
        assert job["job_status"] == "SUCCEEDED", job
        body["analysis_id"] = job["result"]["analysis_id"]
        response = self.post("/recovery-plans", body)
        assert response.status_code == 202, response.text
        self.app.state.business.run_job(response.json()["data"]["job_id"])
        job = self.get("/jobs/" + response.json()["data"]["job_id"]).json()["data"]
        assert job["job_status"] == "SUCCEEDED", job
        plans = job["result"]["plans"]
        return next(
            p
            for p in plans
            if p["metrics"]["changed_order_count"] == 2 and p["metrics"]["total_delay_minutes"] == 0
        )

    def approve(self, plan):
        return self.post(
            "/plans/" + plan["plan_id"] + "/approve",
            {
                "expected_plan_version": plan["plan_version"],
                "expected_incident_version": plan["incident_version"],
                "snapshot_id": plan["snapshot_id"],
            },
            "dispatcher",
        )

    def route_revision(self):
        with self.factory() as session:
            return session.scalar(select(Vehicle.route_revision).where(Vehicle.id == "V01"))
