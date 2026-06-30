from __future__ import annotations

import asyncio
import secrets
import shutil
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from .models import RenderOptions
from .presets import PresetStore
from .queue_manager import JobQueue, JobStore, cleanup_temp, periodic_cleanup
from .renderer import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS
from .settings import get_settings

settings = get_settings()
app = FastAPI(title="VidForge", version="1.1.0")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
security = HTTPBasic(auto_error=False)
store = JobStore(settings.app_data_dir, settings.state_file)
queue = JobQueue(store)
presets = PresetStore(settings.presets_file)
cleanup_task: asyncio.Task | None = None

# Accept any supported media in either column.
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS


def require_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    if not settings.app_username and not settings.app_password:
        return
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"})
    ok_user = secrets.compare_digest(credentials.username, settings.app_username or "")
    ok_pass = secrets.compare_digest(credentials.password, settings.app_password or "")
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"})


@app.on_event("startup")
async def startup() -> None:
    await store.load()
    await presets.load()
    queue.start()
    global cleanup_task
    cleanup_task = asyncio.create_task(periodic_cleanup(settings.app_data_dir, settings.temp_max_age_hours, store))


@app.on_event("shutdown")
async def shutdown() -> None:
    if cleanup_task:
        cleanup_task.cancel()
    await queue.stop()


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def index() -> str:
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/jobs", dependencies=[Depends(require_auth)])
async def list_jobs() -> dict:
    return {"jobs": [j.to_dict() for j in store.list()]}


@app.get("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
async def get_job(job_id: str) -> dict:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


def parse_captions(raw: str) -> list[str]:
    lines = [line.strip() for line in raw.replace("\r", "").split("\n")]
    return [line for line in lines if line]


@app.post("/api/jobs", dependencies=[Depends(require_auth)])
async def create_job(
    name: str = Form("Untitled render"),
    resolution: str = Form("1440p"),
    encoder: str = Form("software"),
    duration_per_image: int = Form(8),
    caption_duration: int = Form(8),
    caption_order: str = Form("random"),
    caption_style: str = Form("neon"),
    caption_loop: bool = Form(True),
    caption_animate: bool = Form(False),
    captions: str = Form("Test\nTest2"),
    keep_video_audio: bool = Form(False),
    captions_file: UploadFile | None = File(None),
    music: UploadFile | None = File(None),
    center: list[UploadFile] = File(default=[]),
    sides: list[UploadFile] = File(default=[]),
    # Legacy fields kept for backwards compatibility: images -> sides, videos -> center.
    images: list[UploadFile] = File(default=[]),
    videos: list[UploadFile] = File(default=[]),
) -> dict:
    if resolution not in ("1440p", "ultrawide"):
        raise HTTPException(400, "Invalid resolution")
    if encoder not in ("software", "vaapi", "nvenc", "qsv"):
        raise HTTPException(400, "Invalid encoder")
    if caption_order not in ("random", "sequential"):
        raise HTTPException(400, "Invalid caption order")
    if caption_style not in ("neon", "classic"):
        raise HTTPException(400, "Invalid caption style")

    parsed_captions = parse_captions(captions)
    uploaded_caption_text = ""
    if captions_file and captions_file.filename:
        raw = await captions_file.read()
        try:
            uploaded_caption_text = raw.decode("utf-8")
            parsed_captions.extend(parse_captions(uploaded_caption_text))
        except UnicodeDecodeError:
            raise HTTPException(400, "Caption file must be UTF-8 text")

    opts = RenderOptions(
        resolution=resolution,  # type: ignore[arg-type]
        encoder=encoder,  # type: ignore[arg-type]
        duration_per_image=max(1, min(duration_per_image, 120)),
        caption_duration=max(1, min(caption_duration, 120)),
        caption_order=caption_order,  # type: ignore[arg-type]
        caption_style=caption_style,  # type: ignore[arg-type]
        caption_loop=caption_loop,
        caption_animate=caption_animate,
        captions=parsed_captions or [""],
        keep_video_audio=keep_video_audio,
    )
    job = await store.add(name, opts)
    job_dir = settings.jobs_dir / job.id / "input"
    (job_dir / "captions.txt").write_text("\n".join(opts.captions) + "\n", encoding="utf-8")

    async def save_upload(upload: UploadFile, folder: Path, allowed: set[str]) -> bool:
        if not upload or not upload.filename:
            return False
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in allowed:
            return False
        folder.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "._- " else "-" for c in Path(upload.filename).name)[:120]
        dest = folder / safe
        with dest.open("wb") as fh:
            while chunk := await upload.read(1024 * 1024):
                fh.write(chunk)
        return True

    center_count = 0
    sides_count = 0
    # New model: center column + shared sides pool, each accepting images & videos.
    for item in center:
        center_count += 1 if await save_upload(item, job_dir / "center", MEDIA_EXTS) else 0
    for item in sides:
        sides_count += 1 if await save_upload(item, job_dir / "sides", MEDIA_EXTS) else 0
    # Legacy uploads: videos go to the center, images go to the sides pool.
    for item in videos:
        center_count += 1 if await save_upload(item, job_dir / "center", VIDEO_EXTS) else 0
    for item in images:
        sides_count += 1 if await save_upload(item, job_dir / "sides", IMAGE_EXTS) else 0

    music_count = 0
    if music and music.filename:
        music_count = 1 if await save_upload(music, job_dir / "music", AUDIO_EXTS) else 0

    job.input_counts = {"center": center_count, "sides": sides_count, "music": music_count}
    if center_count == 0 and sides_count == 0:
        # Nothing usable was uploaded; remove the empty job so the queue stays clean.
        await store.delete(job.id)
        raise HTTPException(400, "Upload at least one center or side image/video.")
    await store.update(job)
    return job.to_dict()


@app.post("/api/jobs/{job_id}/requeue", dependencies=[Depends(require_auth)])
async def requeue_job(job_id: str) -> dict:
    if not await store.requeue(job_id):
        raise HTTPException(400, "Cannot requeue this job")
    return {"ok": True}


@app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(require_auth)])
async def cancel_job(job_id: str) -> dict:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != "running":
        raise HTTPException(400, "Only a running job can be cancelled")
    if not queue.cancel(job_id):
        raise HTTPException(400, "This job is not currently running")
    return {"ok": True}


@app.delete("/api/jobs/{job_id}", dependencies=[Depends(require_auth)])
async def delete_job(job_id: str) -> dict:
    if not await store.delete(job_id):
        raise HTTPException(400, "Cannot delete this job")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/log", response_class=PlainTextResponse, dependencies=[Depends(require_auth)])
async def job_log(job_id: str) -> str:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    path = settings.jobs_dir / job_id / "render.log"
    if not path.exists():
        return "No render log yet."
    return path.read_text(encoding="utf-8", errors="replace")[-60000:]


@app.get("/api/jobs/{job_id}/download", dependencies=[Depends(require_auth)])
async def download(job_id: str):
    job = store.get(job_id)
    if not job or not job.output_file:
        raise HTTPException(404, "Output not found")
    path = Path(job.output_file)
    if not path.exists():
        raise HTTPException(404, "Output missing from disk")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


def _resolve_output(job_id: str) -> Path:
    job = store.get(job_id)
    if not job or not job.output_file:
        raise HTTPException(404, "Output not found")
    path = Path(job.output_file)
    if not path.exists():
        raise HTTPException(404, "Output missing from disk")
    return path


@app.get("/api/jobs/{job_id}/stream", dependencies=[Depends(require_auth)])
async def stream(job_id: str, request: Request):
    """Stream a finished render with HTTP Range support for in-browser preview."""
    path = _resolve_output(job_id)
    file_size = path.stat().st_size
    range_header = request.headers.get("range")
    chunk = 1024 * 1024

    if range_header is None:
        def full():
            with path.open("rb") as fh:
                while data := fh.read(chunk):
                    yield data

        return StreamingResponse(
            full(),
            media_type="video/mp4",
            headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
        )

    # Parse "bytes=start-end".
    try:
        units, rng = range_header.split("=", 1)
        start_s, end_s = rng.split("-", 1)
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
    except (ValueError, AttributeError):
        raise HTTPException(416, "Invalid range header")
    start = max(0, start)
    end = min(end, file_size - 1)
    if start > end:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
    length = end - start + 1

    def ranged():
        remaining = length
        with path.open("rb") as fh:
            fh.seek(start)
            while remaining > 0:
                data = fh.read(min(chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
    }
    return StreamingResponse(ranged(), status_code=206, media_type="video/mp4", headers=headers)


@app.get("/api/presets", dependencies=[Depends(require_auth)])
async def list_presets() -> dict:
    return {"presets": presets.list()}


@app.post("/api/presets", dependencies=[Depends(require_auth)])
async def create_preset(payload: dict) -> dict:
    name = payload.get("name") or "Untitled preset"
    values = payload.get("values") or {}
    if not isinstance(values, dict):
        raise HTTPException(400, "values must be an object")
    preset = await presets.add(name, values)
    return preset


@app.delete("/api/presets/{preset_id}", dependencies=[Depends(require_auth)])
async def delete_preset(preset_id: str) -> dict:
    if not await presets.delete(preset_id):
        raise HTTPException(404, "Preset not found")
    return {"ok": True}


@app.post("/api/cleanup", dependencies=[Depends(require_auth)])
async def cleanup_now() -> dict:
    removed = await cleanup_temp(settings.app_data_dir, settings.temp_max_age_hours, store)
    return {"removed": removed}


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=False)
