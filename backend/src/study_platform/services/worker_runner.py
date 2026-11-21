"""Dispatch PostgreSQL jobs to separate bounded ingestion and generation pools."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from study_platform.persistence.database import connection_failure, session_factory
from study_platform.persistence.models.operations import AiJob
from study_platform.services import ai_jobs, ingestion, job_queue

logger = logging.getLogger(__name__)
HEAVY = ("ingest", "reindex")
GENERATION = (*ai_jobs.GENERATION_TYPES, "classification")


def _claim(types):
    with session_factory()() as db:
        result = job_queue.claim(db, types)
        if result is not None:
            kind = db.scalar(sa.select(AiJob.job_type).where(AiJob.id == result[0]))
            return kind, result
    return None


def _execute(kind, job):
    if kind in ai_jobs.GENERATION_TYPES:
        ai_jobs.run(*job[:3])
    else:
        ingestion.run_once(job)


def _heartbeat(claims, lease_seconds):
    with session_factory()() as db:
        current = job_queue.now()
        rows = db.scalars(sa.select(AiJob).where(
            sa.tuple_(AiJob.id, AiJob.fencing).in_([(job[0], job[2]) for job in claims]),
            AiJob.status == "running", AiJob.cancel_requested_at.is_(None),
            AiJob.lease_expires_at > current,
        ).order_by(AiJob.id).with_for_update(skip_locked=True)).all()
        for job in rows:
            job.lease_expires_at = current + timedelta(seconds=lease_seconds)
        db.commit()


def run(stop, settings):
    """Stop taking jobs on shutdown and keep renewing leases while current jobs settle."""
    heavy_size = settings.worker_heavy_concurrency
    generation_size = settings.worker_generation_concurrency
    heartbeat_interval = min(10.0, settings.job_lease_seconds / 3)
    next_heartbeat = 0.0
    backoff, retry_at = 1, 0.0
    with (ThreadPoolExecutor(heavy_size, thread_name_prefix="study-heavy") as heavy,
          ThreadPoolExecutor(generation_size, thread_name_prefix="study-generation") as generation):
        pools = ((heavy, HEAVY, heavy_size, {}), (generation, GENERATION, generation_size, {}))
        while True:
            current = time.monotonic()
            try:
                for _, _, _, running in pools:
                    for future in list(running):
                        if future.done():
                            del running[future]
                            future.result()
                claims = [job for _, _, _, running in pools for job in running.values()]
                if stop.is_set() and not claims:
                    return
                if claims and current >= next_heartbeat and current >= retry_at:
                    _heartbeat(claims, settings.job_lease_seconds)
                    next_heartbeat = current + heartbeat_interval
                if not stop.is_set() and current >= retry_at:
                    for executor, types, size, running in pools:
                        while len(running) < size and not stop.is_set():
                            claimed = _claim(types)
                            if claimed is None:
                                break
                            kind, job = claimed
                            running[executor.submit(_execute, kind, job)] = job
                if retry_at and current >= retry_at:
                    logger.info("Database connection recovered")
                    backoff, retry_at = 1, 0.0
            except DBAPIError as error:
                if not connection_failure(error):
                    raise
                logger.warning("Database unavailable; retrying in %s seconds", backoff)
                retry_at = current + backoff
                backoff = min(backoff * 2, 30)
            delay = min(settings.worker_poll_seconds, heartbeat_interval)
            remaining_backoff = retry_at - time.monotonic()
            if remaining_backoff > 0 and not any(running for _, _, _, running in pools):
                delay = remaining_backoff
            if stop.is_set():
                time.sleep(delay)
            else:
                stop.wait(delay)
