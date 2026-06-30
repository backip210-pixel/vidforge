from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.renderer import caption_sequence, drawtext_filter, resolution_values, concat_file_line


def test_resolution_values():
    # Side column widths must be even for libx264; the middle column absorbs any
    # remainder so the three columns still sum to the exact total width.
    total_w, total_h, left_w, right_w = resolution_values("1440p")
    assert (total_w, total_h) == (2560, 1440)
    assert left_w % 2 == 0 and right_w % 2 == 0
    assert (total_w - left_w - right_w) % 2 == 0
    assert resolution_values("ultrawide")[:2] == (3440, 1440)


def test_drawtext_escapes_caption_text():
    vf = drawtext_filter(["Bob's 100%: test"], 10, 8)
    assert "drawtext=" in vf
    assert "Bob\\'s" in vf
    assert "100\\%\\:" in vf


def test_neon_caption_style_adds_glow_layers():
    vf = drawtext_filter(["Glow"], 10, 8, style="neon")
    assert "0x22D3EE" in vf
    assert "0xA855F7" in vf
    assert vf.count("drawtext=") > 1


def test_classic_caption_style_uses_single_boxed_layer():
    vf = drawtext_filter(["Classic"], 5, 8, style="classic")
    assert "box=1" in vf
    assert "0x22D3EE" not in vf


def test_concat_file_line_quotes_paths():
    line = concat_file_line(Path("/tmp/a'b.mp4"))
    assert line.startswith("file '")
    assert "\\''" in line


def test_caption_sequence_can_loop_sequentially():
    assert caption_sequence(["A", "B"], 5, "sequential") == ["A", "B", "A", "B", "A"]


def test_caption_sequence_random_preserves_caption_pool():
    seq = caption_sequence(["A", "B", "C"], 12, "random")
    assert len(seq) == 12
    assert set(seq).issubset({"A", "B", "C"})
    assert {"A", "B", "C"}.issubset(set(seq))


def test_collect_inputs_new_and_legacy(tmp_path):
    from app.renderer import collect_inputs
    job = tmp_path / "jobs" / "j1"
    (job / "input" / "center").mkdir(parents=True)
    (job / "input" / "sides").mkdir(parents=True)
    (job / "input" / "images").mkdir(parents=True)   # legacy -> sides
    (job / "input" / "videos").mkdir(parents=True)   # legacy -> center
    (job / "input" / "center" / "c.mp4").write_bytes(b"x")
    (job / "input" / "sides" / "s.jpg").write_bytes(b"x")
    (job / "input" / "images" / "old.png").write_bytes(b"x")
    (job / "input" / "videos" / "old.mov").write_bytes(b"x")
    center, sides, music = collect_inputs(job)
    center_names = {p.name for p in center}
    sides_names = {p.name for p in sides}
    assert center_names == {"c.mp4", "old.mov"}
    assert sides_names == {"s.jpg", "old.png"}
    assert music is None


def test_cancel_token_check_raises():
    from app.renderer import CancelToken, CancelledError
    tok = CancelToken()
    tok.check()  # no-op when not cancelled
    tok.cancel()
    assert tok.cancelled
    try:
        tok.check()
    except CancelledError:
        pass
    else:
        raise AssertionError("expected CancelledError")
