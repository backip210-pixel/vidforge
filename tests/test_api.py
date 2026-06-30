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


# --- Output folder cleanup -------------------------------------------------

from app.main import store  # noqa: E402
from app.models import Job  # noqa: E402


def _make_output(name: str) -> Path:
    out_dir = Path(_DATA) / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / name
    p.write_bytes(b"x" * 2048)
    return p


def _register_job(status: str, output: Path | None) -> Job:
    job = Job(name=f"job-{status}")
    job.status = status
    job.output_file = str(output) if output else None
    store.jobs[job.id] = job
    return job


def test_outputs_info_counts_files():
    with _client() as c:
        _make_output("info_a.mp4")
        _make_output("info_b.mp4")
        info = c.get("/api/outputs").json()
        assert info["count"] >= 2
        assert info["bytes"] >= 4096


def test_delete_job_removes_its_output_file():
    with _client() as c:
        out = _make_output("to_delete.mp4")
        job = _register_job("completed", out)
        assert out.exists()
        assert c.delete(f"/api/jobs/{job.id}").status_code == 200
        # The rendered video should be gone too, not just the queue entry.
        assert not out.exists()


def test_clear_outputs_orphans_keeps_referenced_files():
    with _client() as c:
        referenced = _make_output("keep_me.mp4")
        orphan = _make_output("orphan_me.mp4")
        _register_job("completed", referenced)  # referenced -> must survive
        result = c.post("/api/outputs/clear?mode=orphans").json()
        assert result["removed"] >= 1
        assert referenced.exists()      # still linked to a job
        assert not orphan.exists()      # not linked -> removed


def test_clear_outputs_all_keeps_running_and_queued():
    with _client() as c:
        done = _make_output("done.mp4")
        queued = _make_output("queued.mp4")
        _register_job("completed", done)
        _register_job("queued", queued)     # queued jobs must be protected
        c.post("/api/outputs/clear?mode=all")
        assert not done.exists()        # completed output removed
        assert queued.exists()          # queued output preserved


def test_clear_outputs_rejects_bad_mode():
    with _client() as c:
        assert c.post("/api/outputs/clear?mode=nope").status_code == 400
