from collections import defaultdict, deque
from threading import Event, Thread
from uuid import uuid4

from study_platform.core.config import Settings
from study_platform.services import worker_runner


def test_generation_runs_during_ingestion_and_shutdown_drains(monkeypatch):
    heavy_started, generation_finished, release, stop = (Event() for _ in range(4))
    last_lease = Event()
    task = (uuid4(), uuid4(), 1, 1)
    queued = defaultdict(deque, {
        worker_runner.HEAVY: deque([("ingest", task), ("reindex", task)]),
        worker_runner.GENERATION: deque([("quiz", (uuid4(), uuid4(), 1, 1))]),
    })
    executed = []
    errors = []

    def claim(types):
        return queued[types].popleft() if queued[types] else None

    def execute(kind, job):
        executed.append(kind)
        if kind == "ingest":
            heavy_started.set()
            assert release.wait(5)
        elif kind == "quiz":
            assert heavy_started.wait(5)
            generation_finished.set()

    def heartbeat(jobs, lease):
        if task in jobs and stop.is_set():
            last_lease.set()

    def start():
        try:
            worker_runner.run(stop, Settings(_env_file=None, worker_heavy_concurrency=1,
                worker_generation_concurrency=1, worker_poll_seconds=0.01, job_lease_seconds=1))
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(worker_runner, "_claim", claim)
    monkeypatch.setattr(worker_runner, "_execute", execute)
    monkeypatch.setattr(worker_runner, "_heartbeat", heartbeat)
    worker = Thread(target=start)
    worker.start()
    try:
        assert generation_finished.wait(5)
        stop.set()
        assert last_lease.wait(5)
        assert worker.is_alive()
    finally:
        stop.set()
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert not errors
    assert executed == ["ingest", "quiz"]
    assert len(queued[worker_runner.HEAVY]) == 1
