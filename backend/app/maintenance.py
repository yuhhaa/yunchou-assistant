"""Database-backed redelivery; Redis is transport, never business authority."""

import logging
import time
from datetime import timedelta

from sqlalchemy import or_, select

from app import models as m
from app.services.common import aware
from app.services.lifecycle import expire_reservations

logger = logging.getLogger(__name__)


def dispatch_outbox(session_factory, publish, limit=50):
    counts = {"delivered": 0, "failed": 0}
    # Each publish is bounded by the broker's three-second connection timeout.
    # Lock just one event at a time; crash after publish => safe duplicate delivery.
    for _ in range(limit):
        with session_factory() as session, session.begin():
            event = session.scalar(
                select(m.OutboxEvent)
                .where(
                    m.OutboxEvent.status == "PENDING",
                    m.OutboxEvent.available_at <= m.utcnow(),
                )
                .order_by(m.OutboxEvent.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not event:
                break
            event.attempts += 1
            try:
                if event.event_type == "JOB_SUBMITTED":
                    publish(event.payload["job_type"], event.payload["job_id"])
                elif event.event_type != "NOTIFICATION_CREATED":
                    raise ValueError("Unsupported outbox event")
                # Notifications are already atomically persisted by activation.
                event.status, event.delivered_at = "DELIVERED", m.utcnow()
                counts["delivered"] += 1
            except Exception:  # noqa: BLE001 - sanitized transport boundary, no credential logs
                event.available_at = m.utcnow() + timedelta(seconds=min(2**event.attempts, 60))
                counts["failed"] += 1
                logger.warning("outbox_delivery_failed event_id=%s status=PENDING", event.id)
    return counts


def recover_jobs(session_factory):
    recovered = []
    with session_factory() as session, session.begin():
        jobs = session.scalars(
            select(m.Job)
            .where(
                or_(
                    m.Job.status == "QUEUED",
                    (m.Job.status == "RUNNING") & (m.Job.lease_expires_at <= m.utcnow()),
                )
            )
            .order_by(m.Job.id)
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            if job.status == "RUNNING":
                job.attempts += 1  # fences the dead/late attempt before redispatch
                job.status, job.lease_owner, job.lease_expires_at = "QUEUED", None, None
                recovered.append(job.id)
                if job.job_type == "CHAT":
                    turn = session.get(m.ChatTurn, job.input["turn_id"])
                    turn.status = "QUEUED"
            latest = session.scalar(
                select(m.OutboxEvent)
                .where(
                    m.OutboxEvent.event_type == "JOB_SUBMITTED",
                    m.OutboxEvent.payload["job_id"].as_string() == job.id,
                )
                .order_by(m.OutboxEvent.created_at.desc())
                .limit(1)
            )
            # Recover even a broker that acknowledged publish then lost its queue.
            if not latest or (
                latest.status == "DELIVERED"
                and aware(latest.delivered_at) < m.utcnow() - timedelta(seconds=15)
            ):
                session.add(
                    m.OutboxEvent(
                        tenant_id=job.tenant_id,
                        event_type="JOB_SUBMITTED",
                        payload={"job_id": job.id, "job_type": job.job_type},
                    )
                )
            elif job.id in recovered and latest.status == "PENDING":
                latest.available_at = m.utcnow()
    return recovered


def maintenance_once(session_factory, publish):
    expired = expire_reservations(session_factory=session_factory)
    recovered = recover_jobs(session_factory)
    return {**dispatch_outbox(session_factory, publish), "expired": expired, "recovered": recovered}


def main():
    from app.config import Settings
    from app.db import make_engine, make_session_factory
    from app.worker import publish

    settings = Settings()
    factory = make_session_factory(make_engine(settings.database_url))
    while True:
        try:
            maintenance_once(factory, publish)
        except Exception:  # noqa: BLE001 - daemon retries sanitized dependency failure
            logger.error("maintenance_unavailable status=RETRY")
        time.sleep(settings.maintenance_interval_seconds)


if __name__ == "__main__":
    main()
