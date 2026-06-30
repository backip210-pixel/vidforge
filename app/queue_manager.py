from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .models import Job, RenderOptions, utc_now
from .renderer import CancelledError, CancelToken, render_job


class JobStore:
    def __init__(self, data_dir: Path, state_file: Path):
        self.data_dir = data_dir
        self.state_file = state_file
        self.jobs: dict[str, Job] = {}
        self.lock = asyncio.Lock()

    async def load(self) -> None:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.jobs = {item["id"]: Job.from_dict(item) for item in data.get("jobs", [])}
                for job in self.jobs.values():
                    if job.status == "running":
                        job.status = "failed"
                        job.error = "Server restarted while this job was running. Please requeue it."
                        job.updated_at = utc_now()
            except Exception:
                self.jobs = {}
        await self.save()

    async def save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"jobs": [job.to_dict() for job in sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)]}
        self.state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    async def add(self, name: str, options: RenderOptions) -> Job:
        async with self.lock:
            job = Job(name=name or "Untitled render", options=options)
            job_dir = self.data_dir / "jobs" / job.id
            for sub in ("input/center", "input/sides", "input/music"):
                (job_dir / sub).mkdir(parents=True, exist_ok=True)
            job.log_file = str(job_dir / "render.log")
            self.jobs[job.id] = job
            await self.save()
            return job

    async def update(self, job: Job) -> None:
        async with self.lock:
            job.updated_at = utc_now()
            self.jobs[job.id] = job
            await self.save()

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    async def delete(self, job_id: str) -> bool:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job or job.status == "running":
                return False
            self.jobs.pop(job_id, None)
            shutil.rmtree(self.data_dir / "jobs" / job_id, ignore_errors=True)
            await self.save()
            return True

    async def requeue(self, job_id: str) -> bool:
        async with self.lock:
            job = self.jobs.get(job_id)
            if not job or job.status == "running":
                return False
            job.status = "queued"
            job.progress = 0
            job.stage = "Queued"
            job.error = None
            job.started_at = None
            job.finished_at = None
            job.output_file = None
            job.updated_at = utc_now()
            await self.save()
            return True


class JobQueue:
    def __init__(self, store: JobStore):
        self.store = store
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._current_id: str | None = None
        self._cancel: CancelToken | None = None

    def cancel(self, job_id: str) -> bool:
        """Request cancellation of the job if it is the one currently running."""
        if self._current_id == job_id and self._cancel is not None:
            self._cancel.cancel()
            return True
        return False

    def start(self) -> None:
        if not self._task:
            self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task

    async def _worker(self) -> None:
        while not self._stop.is_set():
            job = next((j for j in reversed(self.store.list()) if j.status == "queued"), None)
            if not job:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass
                continue
            await self._run(job)

    async def _run(self, job: Job) -> None:
        job.status = "running"
        job.started_at = utc_now()
        job.progress = 1
        job.stage = "Starting"
        await self.store.update(job)

        cancel = CancelToken()
        self._current_id = job.id
        self._cancel = cancel

        def progress(percent: int, stage: str) -> None:
            job.progress = max(0, min(100, percent))
            job.stage = stage
            # Called from executor thread; schedule safe save on event loop.
            asyncio.run_coroutine_threadsafe(self.store.update(job), self._loop)

        self._loop = asyncio.get_running_loop()
        try:
            output = await asyncio.to_thread(render_job, job, self.store.data_dir, progress, cancel)
            job.status = "completed"
            job.output_file = str(output)
            job.progress = 100
            job.stage = "Completed"
            job.error = None
        except CancelledError:
            job.status = "cancelled"
            job.error = "Render cancelled by user."
            job.stage = "Cancelled"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            job.stage = "Failed"
        finally:
            self._current_id = None
            self._cancel = None
            shutil.rmtree(self.store.data_dir / "tmp" / job.id, ignore_errors=True)
            job.finished_at = utc_now()
            await self.store.update(job)


async def cleanup_temp(data_dir: Path, max_age_hours: int, store: JobStore) -> int:
    tmp_dir = data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    running_ids = {j.id for j in store.jobs.values() if j.status == "running"}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    removed = 0
    for path in tmp_dir.iterdir():
        if path.name in running_ids:
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if mtime < cutoff:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
                removed += 1
        except FileNotFoundError:
            continue
    return removed


async def periodic_cleanup(data_dir: Path, max_age_hours: int, store: JobStore) -> None:
    while True:
        await cleanup_temp(data_dir, max_age_hours, store)
        await asyncio.sleep(60 * 60)
