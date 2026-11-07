"""Separate Celery queues with durable database claims and fencing."""

import logging

from celery import Celery

from app.config import Settings
from app.db import make_engine, make_session_factory

settings = Settings()
celery = Celery("fleet", broker=settings.redis_url)
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_timeout=3,
    broker_transport_options={
        "visibility_timeout": 120,
        "socket_connect_timeout": 3,
        "socket_timeout": 3,
        "retry_on_timeout": False,
    },
    task_publish_retry=False,
    broker_connection_retry_on_startup=True,
    task_routes={"fleet.compute": {"queue": "compute"}, "fleet.chat": {"queue": "chat"}},
    task_soft_time_limit=65,
    task_time_limit=75,
)
logger = logging.getLogger(__name__)


def publish(kind, job_id):
    task = "fleet.chat" if kind == "CHAT" else "fleet.compute"
    if kind not in {"CHAT", "IMPACT_ANALYSIS", "PLAN_GENERATION", "MAP_ROUTING"}:
        raise ValueError("Unsupported job type")
    celery.send_task(task, args=[job_id], queue="chat" if kind == "CHAT" else "compute")


def execute(job_id, chat=False):
    from app.services.chat import run_chat
    from app.services.jobs import run_job

    engine = make_engine(settings.database_url)
    try:
        result = (run_chat if chat else run_job)(
            job_id,
            session_factory=make_session_factory(engine),
            settings=settings,
            worker_id="celery",
        )
        logger.info("job_id=%s status=%s", job_id, result["job_status"])
    finally:
        engine.dispose()


@celery.task(name="fleet.compute")
def compute(job_id):
    execute(job_id)


@celery.task(name="fleet.chat", soft_time_limit=50, time_limit=60)
def chat(job_id):
    execute(job_id, chat=True)
