"""Common leases and terminal states for the PostgreSQL task queue."""

import hashlib
import socket
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import select

from study_platform.core.config import get_settings
from study_platform.persistence.models.operations import AiJob, AiJobAttempt

ACTIVE = ("queued", "running")


class LostLease(Exception):
    pass


def now():
    return datetime.now(UTC)


def claim(db, job_types):
    current = now()
    job = db.scalar(
        select(AiJob)
        .where(
            AiJob.job_type.in_(job_types),
            AiJob.cancel_requested_at.is_(None),
            sa.or_(
                sa.and_(AiJob.status == "queued", AiJob.next_run_at <= current),
                sa.and_(AiJob.status == "running", AiJob.lease_expires_at < current),
            ),
        )
        .order_by(AiJob.created_at, AiJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not job:
        return None
    if job.attempt_count >= job.max_attempts:
        previous = db.scalar(
            select(AiJobAttempt).where(
                AiJobAttempt.job_id == job.id, AiJobAttempt.attempt_no == job.attempt_count
            )
        )
        if previous and previous.finished_at is None:
            previous.finished_at = current
            previous.outcome = "failed"
            previous.failure_code = "lease_expired"
        job.status = "failed"
        job.failure_code = "lease_expired"
        job.finished_at = current
        db.commit()
        return None
    if job.status == "running":
        previous = db.scalar(
            select(AiJobAttempt).where(
                AiJobAttempt.job_id == job.id, AiJobAttempt.attempt_no == job.attempt_count
            )
        )
        if previous and previous.finished_at is None:
            previous.finished_at = current
            previous.outcome = "retry"
            previous.failure_code = "lease_expired"
    job.status = "running"
    job.failure_code = None
    job.attempt_count += 1
    job.fencing += 1
    job.lease_expires_at = current + timedelta(seconds=get_settings().job_lease_seconds)
    job.lease_token_hash = hashlib.sha256(uuid4().bytes).hexdigest()
    phase = "parsing" if job.job_type == "ingest" else (
        "reindexing" if job.job_type == "reindex" else "generating"
    )
    job.result = {**(job.result if job.job_type == "reindex" and job.result else {}), "phase": phase}
    db.add(
        AiJobAttempt(
            job_id=job.id,
            attempt_no=job.attempt_count,
            worker_id=socket.gethostname()[:128],
            fencing=job.fencing,
            started_at=current,
        )
    )
    result = (job.id, job.resource_id, job.fencing, job.attempt_count)
    db.commit()
    return result


def require_lease(job, fence):
    if (
        job is None or job.status != "running" or job.fencing != fence
        or job.cancel_requested_at is not None
        or job.lease_expires_at is None or job.lease_expires_at <= now()
    ):
        raise LostLease()
    return job


def finish_attempt(db, job, outcome, failure_code=None):
    attempt = db.scalar(select(AiJobAttempt).where(
        AiJobAttempt.job_id == job.id,
        AiJobAttempt.attempt_no == job.attempt_count,
        AiJobAttempt.finished_at.is_(None),
    ))
    if attempt:
        attempt.outcome = outcome
        attempt.failure_code = failure_code
        attempt.finished_at = now()


def cancel(db, job):
    if job.status not in ACTIVE:
        return
    job.status = "cancelled"
    job.cancel_requested_at = job.finished_at = now()
    job.lease_expires_at = None
    job.fencing += 1
    finish_attempt(db, job, "cancelled")
