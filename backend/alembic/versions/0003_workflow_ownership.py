"""Persist current calculation and candidate-family ownership.

Legacy records have no reliable ordering token. Backfill only unambiguous owners;
ambiguous unapproved work requires a new dispatcher decision, without deleting
jobs/plans or changing calculation-fact versions. Do not infer ownership by age.
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "incidents", sa.Column("current_calculation_job_id", sa.String(64), nullable=True)
    )
    op.add_column("incidents", sa.Column("current_plan_group_id", sa.String(64), nullable=True))
    incidents = sa.table(
        "incidents",
        sa.column("id"),
        sa.column("tenant_id"),
        sa.column("version"),
        sa.column("coordination_version"),
        sa.column("status"),
        sa.column("current_calculation_job_id"),
        sa.column("current_plan_group_id"),
    )
    plans = sa.table(
        "plans",
        sa.column("id"),
        sa.column("tenant_id"),
        sa.column("incident_id"),
        sa.column("incident_version"),
        sa.column("plan_group_id"),
        sa.column("snapshot_id"),
        sa.column("status"),
    )
    jobs = sa.table(
        "jobs",
        sa.column("id"),
        sa.column("tenant_id"),
        sa.column("incident_id"),
        sa.column("job_type"),
        sa.column("status"),
        sa.column("input", sa.JSON()),
    )
    connection = op.get_bind()
    for incident in connection.execute(sa.select(incidents)).mappings().all():
        candidates = (
            connection.execute(
                sa.select(plans).where(
                    plans.c.incident_id == incident["id"],
                    plans.c.tenant_id == incident["tenant_id"],
                    plans.c.status.in_(["PENDING_APPROVAL", "APPROVED_PENDING_ACK"]),
                )
            )
            .mappings()
            .all()
        )
        selected = [p for p in candidates if p["status"] == "APPROVED_PENDING_ACK"]
        if len(selected) > 1:
            raise RuntimeError(
                "Multiple selected plans require manual repair before migration 0003"
            )
        values = {}
        if selected:
            # A selected approval, including its unchanged lease/ACKs, is authority.
            values = {
                "current_plan_group_id": selected[0]["plan_group_id"],
                "status": "AWAITING_ACK",
            }
        elif incident["status"] == "ANALYZING":
            pending = (
                connection.execute(
                    sa.select(jobs).where(
                        jobs.c.incident_id == incident["id"],
                        jobs.c.tenant_id == incident["tenant_id"],
                        jobs.c.status.in_(["QUEUED", "RUNNING"]),
                        jobs.c.job_type.in_(["IMPACT_ANALYSIS", "PLAN_GENERATION"]),
                    )
                )
                .mappings()
                .all()
            )
            valid = [
                j
                for j in pending
                if j["input"].get("expected_incident_version") == incident["version"]
                and j["input"].get("incident_coordination_version")
                == incident["coordination_version"]
            ]
            if len(valid) == 1:
                values["current_calculation_job_id"] = valid[0]["id"]
                families = {
                    p["plan_group_id"]
                    for p in candidates
                    if p["incident_version"] == incident["version"]
                    and p["snapshot_id"] == valid[0]["input"].get("snapshot_id")
                }
                if len(families) == 1:
                    values["current_plan_group_id"] = families.pop()
        elif incident["status"] == "AWAITING_APPROVAL":
            families = {
                p["plan_group_id"]
                for p in candidates
                if p["incident_version"] == incident["version"]
            }
            if len(families) == 1:
                values["current_plan_group_id"] = families.pop()
        if not values and incident["status"] in {"ANALYZING", "AWAITING_APPROVAL"}:
            values = {
                "status": "MANUAL_REVIEW",
                "coordination_version": incident["coordination_version"] + 1,
            }
        if values:
            connection.execute(
                sa.update(incidents).where(incidents.c.id == incident["id"]).values(**values)
            )


def downgrade():
    op.drop_column("incidents", "current_plan_group_id")
    op.drop_column("incidents", "current_calculation_job_id")
