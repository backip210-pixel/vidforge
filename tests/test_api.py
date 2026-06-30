import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Use an isolated data dir so tests never touch real data.
_DATA = tempfile.mkdtemp(prefix="vidforge-test-")
os.environ["APP_DATA_DIR"] = _DATA

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _client() -> TestClient:
    return TestClient(app)


def test_health():
    with _client() as c:
        assert c.get("/health").json() == {"ok": True}


def test_create_job_requires_media():
    with _client() as c:
        r = c.post("/api/jobs", data={"name": "Empty"})
        assert r.status_code == 400


def test_create_job_with_sides_media_and_counts():
    with _client() as c:
        files = [
            ("sides", ("a.jpg", b"\xff\xd8\xff\xe0fakejpeg", "image/jpeg")),
            ("center", ("c.png", b"\x89PNGfake", "image/png")),
        ]
        r = c.post("/api/jobs", data={"name": "Mixed", "caption_style": "neon"}, files=files)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["input_counts"]["sides"] == 1
        assert body["input_counts"]["center"] == 1
        # Captions default to neon.
        assert body["options"]["caption_style"] == "neon"


def test_legacy_image_video_fields_map_to_sides_and_center():
    with _client() as c:
        files = [
            ("images", ("legacy.jpg", b"\xff\xd8fake", "image/jpeg")),
            ("videos", ("legacy.mp4", b"\x00\x00\x00\x18ftyp", "video/mp4")),
        ]
        r = c.post("/api/jobs", data={"name": "Legacy"}, files=files)
        assert r.status_code == 200, r.text
        counts = r.json()["input_counts"]
        assert counts["sides"] == 1  # image -> sides
        assert counts["center"] == 1  # video -> center


def test_cancel_only_running():
    with _client() as c:
        files = [("sides", ("a.jpg", b"\xff\xd8fake", "image/jpeg"))]
        job = c.post("/api/jobs", data={"name": "ToCancel"}, files=files).json()
        # Freshly queued (not running) -> cannot cancel.
        r = c.post(f"/api/jobs/{job['id']}/cancel")
        assert r.status_code in (400, 404)


def test_preset_crud():
    with _client() as c:
        created = c.post(
            "/api/presets",
            json={"name": "Ultrawide neon", "values": {"resolution": "ultrawide", "caption_style": "neon", "bogus": 1}},
        ).json()
        assert created["name"] == "Ultrawide neon"
        # Unknown fields are stripped.
        assert "bogus" not in created["values"]
        assert created["values"]["resolution"] == "ultrawide"

        listed = c.get("/api/presets").json()["presets"]
        assert any(p["id"] == created["id"] for p in listed)

        assert c.delete(f"/api/presets/{created['id']}").status_code == 200
        assert c.delete(f"/api/presets/{created['id']}").status_code == 404
