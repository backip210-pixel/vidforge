from __future__ import annotations

import os
import random
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from .models import Job, RenderOptions, safe_name

Progress = Callable[[int, str], None]

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
FONT_FILE = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def run(cmd: list[str], log: Path, stage: str) -> None:
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n$ {' '.join(shlex.quote(c) for c in cmd)}\n")
        fh.flush()
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"{stage} failed. See log for FFmpeg output.")


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(proc.stdout.strip())
    except Exception:
        return 0.0


def resolution_values(resolution: str) -> tuple[int, int, int, int]:
    if resolution == "ultrawide":
        return 3440, 1440, 1146, 1146
    return 2560, 1440, 853, 853


def encoder_args(encoder: str, bitrate: str) -> list[str]:
    # Software is the safest and works on AMD, Intel, ARM and VPS hosts.
    if encoder == "vaapi":
        # Requires /dev/dri mounted into the container and a VAAPI-capable Linux host.
        return ["-vaapi_device", "/dev/dri/renderD128", "-c:v", "h264_vaapi", "-b:v", bitrate]
    if encoder == "nvenc":
        return ["-c:v", "h264_nvenc", "-b:v", bitrate]
    if encoder == "qsv":
        return ["-c:v", "h264_qsv", "-b:v", bitrate]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-b:v", bitrate]


def output_encode_args(encoder: str, bitrate: str) -> list[str]:
    return encoder_args(encoder, bitrate) + ["-pix_fmt", "yuv420p", "-movflags", "+faststart"]


def concat_file_line(path: Path) -> str:
    # FFmpeg concat demuxer accepts single quoted paths, with quotes escaped like this.
    escaped = str(path).replace("'", "'\\''")
    return f"file '{escaped}'\n"


def collect_inputs(job_dir: Path) -> tuple[list[Path], list[Path], Path | None]:
    media = job_dir / "input"
    images = sorted([p for p in (media / "images").glob("*.*") if p.suffix.lower() in IMAGE_EXTS])
    videos = sorted([p for p in (media / "videos").glob("*.*") if p.suffix.lower() in VIDEO_EXTS])
    music_files = sorted([p for p in (media / "music").glob("*.*") if p.suffix.lower() in AUDIO_EXTS])
    return images, videos, music_files[0] if music_files else None


def clean_video(src: Path, out: Path, opts: RenderOptions, log: Path) -> None:
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if opts.encoder == "vaapi":
        cmd += ["-vf", "format=nv12,hwupload"]
    cmd += encoder_args(opts.encoder, "8000k") + ["-an", str(out)]
    run(cmd, log, f"Cleaning {src.name}")


def create_image_section(images: list[Path], middle_duration: int, out: Path, width: int, height: int, opts: RenderOptions, tmp: Path, log: Path) -> None:
    if not images:
        run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=black:s={width}x{height}:d={max(middle_duration, 30)}",
            *output_encode_args(opts.encoder, "6000k"), str(out)
        ], log, "Creating blank side section")
        return

    shuffled = images[:]
    random.shuffle(shuffled)
    concat = tmp / f"{out.stem}_images.txt"
    current = 0
    with concat.open("w", encoding="utf-8") as fh:
        while current < middle_duration:
            for img in shuffled:
                fh.write(concat_file_line(img))
                fh.write(f"duration {opts.duration_per_image}\n")
                current += opts.duration_per_image
                if current >= middle_duration:
                    fh.write(concat_file_line(img))
                    break
            random.shuffle(shuffled)

    vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-vf", vf, *output_encode_args(opts.encoder, "6000k"), str(out)]
    run(cmd, log, "Creating image section")


def caption_sequence(captions: list[str], needed: int, order: str) -> list[str]:
    captions = [c.strip() for c in captions if c.strip()] or [""]
    if order != "random" or len(captions) <= 1:
        return [captions[i % len(captions)] for i in range(needed)]

    result: list[str] = []
    previous: str | None = None
    while len(result) < needed:
        bag = captions[:]
        random.shuffle(bag)
        for caption in bag:
            # Avoid the same caption appearing across a shuffle boundary where possible.
            if previous is not None and caption == previous and len(bag) > 1:
                continue
            result.append(caption)
            previous = caption
            if len(result) >= needed:
                break
    return result


def drawtext_layer(text: str, start: int, end: float, *, fontsize: int, fontcolor: str,
                   x: str = "(w-text_w)/2", y: str = "h-120", borderw: int = 0,
                   bordercolor: str | None = None, box: bool = False,
                   boxcolor: str = "0x000000@0.70", boxborderw: int = 10,
                   shadowcolor: str | None = None, shadowx: int = 0, shadowy: int = 0) -> str:
    parts = [
        f"drawtext=fontfile={FONT_FILE}",
        f"text='{text}'",
        f"enable='between(t,{start},{end})'",
        f"fontcolor={fontcolor}",
        f"fontsize={fontsize}",
        f"x={x}",
        f"y={y}",
    ]
    if borderw:
        parts.append(f"borderw={borderw}")
    if bordercolor:
        parts.append(f"bordercolor={bordercolor}")
    if box:
        parts.extend(["box=1", f"boxcolor={boxcolor}", f"boxborderw={boxborderw}"])
    if shadowcolor:
        parts.extend([f"shadowcolor={shadowcolor}", f"shadowx={shadowx}", f"shadowy={shadowy}"])
    return ":".join(parts)


def drawtext_filter(captions: list[str], total_duration: int, caption_duration: int,
                    order: str = "random", style: str = "neon") -> str:
    needed = (total_duration // max(caption_duration, 1)) + 2
    pieces: list[str] = []
    t = 0
    for caption in caption_sequence(captions, needed, order):
        # Escape for FFmpeg drawtext.
        safe = caption.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")
        end = t + caption_duration - 0.1
        if style == "classic":
            pieces.append(
                drawtext_layer(
                    safe, t, end,
                    fontsize=48,
                    fontcolor="white",
                    box=True,
                    boxcolor="0x000000@0.70",
                    boxborderw=10,
                    y="h-120",
                )
            )
        else:
            # FFmpeg drawtext does not provide a real blur effect, so the neon look is
            # built from multiple inexpensive text layers: a large translucent glow,
            # offset cyan/purple glow passes, then crisp white text on top.
            pieces.extend([
                drawtext_layer(
                    safe, t, end,
                    fontsize=56,
                    fontcolor="0x22D3EE@0.22",
                    borderw=18,
                    bordercolor="0xA855F7@0.28",
                    y="h-124",
                ),
                drawtext_layer(
                    safe, t, end,
                    fontsize=50,
                    fontcolor="0x67E8F9@0.38",
                    borderw=8,
                    bordercolor="0x06B6D4@0.55",
                    shadowcolor="0x06B6D4@0.85",
                    shadowx=2,
                    shadowy=2,
                    y="h-120",
                ),
                drawtext_layer(
                    safe, t, end,
                    fontsize=48,
                    fontcolor="white",
                    borderw=3,
                    bordercolor="0x22D3EE@0.95",
                    box=True,
                    boxcolor="0x020617@0.42",
                    boxborderw=16,
                    shadowcolor="0xA855F7@0.95",
                    shadowx=0,
                    shadowy=0,
                    y="h-120",
                ),
            ])
        t += caption_duration
    return ",".join(pieces)


def render_job(job: Job, data_dir: Path, progress: Progress) -> Path:
    start = time.time()
    job_dir = data_dir / "jobs" / job.id
    tmp = data_dir / "tmp" / job.id
    outputs = data_dir / "outputs"
    tmp.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)
    log = job_dir / "render.log"
    opts = job.options

    images, videos, music = collect_inputs(job_dir)
    job.input_counts = {"images": len(images), "videos": len(videos), "music": 1 if music else 0}
    if not images and not videos:
        raise ValueError("Upload at least one image or video before starting a render.")

    total_width, total_height, left_width, right_width = resolution_values(opts.resolution)
    progress(5, f"Starting {total_width}x{total_height} render with {opts.encoder} encoder")

    clean_videos: list[Path] = []
    for idx, video in enumerate(videos, start=1):
        out = tmp / f"clean_{idx:03d}_{video.stem}.mp4"
        clean_video(video, out, opts, log)
        clean_videos.append(out)
        progress(5 + int(20 * idx / max(len(videos), 1)), f"Cleaned video {idx}/{len(videos)}")

    middle = tmp / "middle.mp4"
    if clean_videos:
        concat = tmp / "middle_list.txt"
        with concat.open("w", encoding="utf-8") as fh:
            for v in clean_videos:
                fh.write(concat_file_line(v))
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-vf", f"scale=-2:{total_height},setsar=1", *output_encode_args(opts.encoder, "12000k"), str(middle)]
        run(cmd, log, "Building middle video")
    else:
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=black:s=1148x{total_height}:d=30", *output_encode_args(opts.encoder, "8000k"), str(middle)]
        run(cmd, log, "Creating blank middle video")
    progress(35, "Built middle video")

    middle_duration = max(int(probe_duration(middle) + 0.5), 30)
    left = tmp / "left.mp4"
    right = tmp / "right.mp4"
    create_image_section(images, middle_duration, left, left_width, total_height, opts, tmp, log)
    progress(50, "Built left image section")
    create_image_section(images, middle_duration, right, right_width, total_height, opts, tmp, log)
    progress(65, "Built right image section")

    final_layout = tmp / "final_layout.mp4"
    filter_complex = (
        f"[0:v]scale={left_width}:{total_height},setsar=1[left];"
        f"[1:v]scale=-2:{total_height},setsar=1[middle];"
        f"[2:v]scale={right_width}:{total_height},setsar=1[right];"
        "[left][middle][right]hstack=inputs=3[v]"
    )
    cmd = ["ffmpeg", "-y", "-i", str(left), "-i", str(middle), "-i", str(right), "-filter_complex", filter_complex, "-map", "[v]", *output_encode_args(opts.encoder, opts.video_bitrate), str(final_layout)]
    run(cmd, log, "Compositing three-section layout")
    progress(78, "Composited layout")

    total_duration = max(int(probe_duration(final_layout) + 0.5), middle_duration)
    vf = "format=yuv420p," + drawtext_filter(opts.captions, total_duration, opts.caption_duration, opts.caption_order, opts.caption_style)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_name = f"{safe_name(job.name).replace(' ', '_')}_{timestamp}.mp4"
    output = outputs / out_name

    cmd = ["ffmpeg", "-y", "-i", str(final_layout)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music), "-vf", vf, *encoder_args(opts.encoder, opts.video_bitrate), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(output)]
    else:
        cmd += ["-vf", vf, *output_encode_args(opts.encoder, opts.video_bitrate), "-an", str(output)]
    run(cmd, log, "Adding captions and audio")
    progress(98, f"Finalising ({int(time.time() - start)}s)")

    shutil.rmtree(tmp, ignore_errors=True)
    progress(100, "Completed")
    return output
