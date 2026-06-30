from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.renderer import drawtext_filter, resolution_values, concat_file_line


def test_resolution_values():
    assert resolution_values("1440p") == (2560, 1440, 853, 853)
    assert resolution_values("ultrawide") == (3440, 1440, 1146, 1146)


def test_drawtext_escapes_caption_text():
    vf = drawtext_filter(["Bob's 100%: test"], 10, 8)
    assert "drawtext=" in vf
    assert "Bob\\'s" in vf
    assert "100\\%\\:" in vf


def test_concat_file_line_quotes_paths():
    line = concat_file_line(Path("/tmp/a'b.mp4"))
    assert line.startswith("file '")
    assert "\\''" in line
