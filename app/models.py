from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4


JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
Resolution = Literal["1440p", "ultrawide"]
Encoder = Literal["software", "vaapi", "nvenc", "qsv"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RenderOptions:
    resolution: Resolution = "1440p"
    encoder: Encoder = "software"
    duration_per_image: int = 8
    caption_duration: int = 8
    captions: list[str] = field(default_factory=lambda: ["Test", "Test2"])
    keep_video_audio: bool = False
    video_bitrate: str = "15000k"


@dataclass
class Job:
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    name: str = "Untitled render"
    status: JobStatus = "queued"
    progress: int = 0
    stage: str = "Queued"
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    output_file: str | None = None
    log_file: str | None = None
    options: RenderOptions = field(default_factory=RenderOptions)
    input_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        opts = data.get("options", {}) or {}
        data = dict(data)
        data["options"] = RenderOptions(**opts)
        return cls(**data)


def safe_name(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in " ._-" else "-" for c in name).strip()
    return cleaned[:80] or "render"
