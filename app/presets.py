from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

# Fields a preset may store. Uploaded media is never part of a preset; only the
# render settings from the "Create render job" form are saved.
PRESET_FIELDS = {
    "resolution",
    "encoder",
    "duration_per_image",
    "caption_duration",
    "caption_order",
    "caption_style",
    "caption_loop",
    "caption_animate",
    "captions",
    "keep_video_audio",
}


def sanitize_preset_values(values: dict) -> dict:
    """Keep only known, JSON-serialisable preset fields."""
    cleaned: dict = {}
    for key in PRESET_FIELDS:
        if key in values and values[key] is not None:
            cleaned[key] = values[key]
    return cleaned


class PresetStore:
    """Tiny JSON-backed store for reusable render presets."""

    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.presets: dict[str, dict] = {}
        self.lock = asyncio.Lock()

    async def load(self) -> None:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.presets = {p["id"]: p for p in data.get("presets", []) if "id" in p}
            except Exception:
                self.presets = {}

    async def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"presets": sorted(self.presets.values(), key=lambda p: p.get("name", "").lower())}
        self.state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list(self) -> list[dict]:
        return sorted(self.presets.values(), key=lambda p: p.get("name", "").lower())

    async def add(self, name: str, values: dict) -> dict:
        async with self.lock:
            name = (name or "Untitled preset").strip()[:80] or "Untitled preset"
            preset = {"id": uuid4().hex[:12], "name": name, "values": sanitize_preset_values(values)}
            self.presets[preset["id"]] = preset
            await self._save()
            return preset

    async def delete(self, preset_id: str) -> bool:
        async with self.lock:
            if preset_id not in self.presets:
                return False
            self.presets.pop(preset_id, None)
            await self._save()
            return True
