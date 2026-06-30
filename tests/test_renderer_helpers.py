from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.renderer import caption_sequence, drawtext_filter, resolution_values, concat_file_line


def test_resolution_values():
    assert resolution_values("1440p") == (2560, 1440, 853, 853)
    assert resolution_values("ultrawide") == (3440, 1440, 1146, 1146)


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
