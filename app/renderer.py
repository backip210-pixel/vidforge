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


class CancelledError(RuntimeError):
    """Raised when a render is cancelled by the user."""


class CancelToken:
    """Cooperative cancellation handle shared with the queue worker.

    The worker can call ``cancel()`` from the event loop thread while the
    renderer runs in a worker thread. The renderer registers each live FFmpeg
    process so cancellation can terminate it immediately, and checks the token
    between stages so it stops promptly even between FFmpeg invocations.
    """

    def __init__(self) -> None:
        self._cancelled = False
        self._proc: subprocess.Popen | None = None

    def cancel(self) -> None:
        self._cancelled = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def register(self, proc: subprocess.Popen | None) -> None:
        self._proc = proc

    def check(self) -> None:
        if self._cancelled:
            raise CancelledError("Render cancelled by user.")


def run(cmd: list[str], log: Path, stage: str, cancel: CancelToken | None = None) -> None:
    if cancel:
        cancel.check()
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n$ {' '.join(shlex.quote(c) for c in cmd)}\n")
        fh.flush()
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True)
        if cancel:
            cancel.register(proc)
        try:
            returncode = proc.wait()
        finally:
            if cancel:
                cancel.register(None)
        if cancel and cancel.cancelled:
            raise CancelledError("Render cancelled by user.")
        if returncode != 0:
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


def _even(n: int) -> int:
    return n - (n % 2)


def resolution_values(resolution: str) -> tuple[int, int, int, int]:
    # Side column widths must be even for libx264. The middle column absorbs any
    # remainder so the three columns still sum to the exact total width.
    if resolution == "ultrawide":
        return 3440, 1440, 1146, 1146
    return 2560, 1440, 852, 852


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


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def _is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def collect_inputs(job_dir: Path) -> tuple[list[Path], list[Path], Path | None]:
    """Collect media for a job.

    Returns ``(center_media, sides_media, music)`` where each media list may
    contain both images and videos.

    Layout (newer jobs)::

        input/center/   -> the middle column (images and/or videos)
        input/sides/    -> a shared pool the left and right columns draw from
        input/music/

    Backwards compatibility: older jobs stored images under ``input/images``
    (used for the sides) and videos under ``input/videos`` (used for the
    middle). Those folders are merged into the new model when present.
    """
    media = media_dir = job_dir / "input"

    def files_in(folder: Path) -> list[Path]:
        if not folder.exists():
            return []
        return sorted(p for p in folder.glob("*.*") if _is_image(p) or _is_video(p))

    center = files_in(media / "center")
    sides = files_in(media / "sides")

    # Legacy folders: videos -> center, images -> sides.
    legacy_videos = sorted(p for p in (media / "videos").glob("*.*") if _is_video(p)) if (media / "videos").exists() else []
    legacy_images = sorted(p for p in (media / "images").glob("*.*") if _is_image(p)) if (media / "images").exists() else []
    center = center + legacy_videos
    sides = sides + legacy_images

    music_files = sorted(p for p in (media_dir / "music").glob("*.*") if p.suffix.lower() in AUDIO_EXTS) if (media_dir / "music").exists() else []
    return center, sides, music_files[0] if music_files else None


def clean_video(src: Path, out: Path, opts: RenderOptions, log: Path, cancel: CancelToken | None = None) -> None:
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if opts.encoder == "vaapi":
        cmd += ["-vf", "format=nv12,hwupload"]
    cmd += encoder_args(opts.encoder, "8000k") + ["-an", str(out)]
    run(cmd, log, f"Cleaning {src.name}", cancel)


def image_to_clip(img: Path, out: Path, duration: int, width: int, height: int, opts: RenderOptions, log: Path, cancel: CancelToken | None = None) -> None:
    """Render a single still image into a short padded video clip."""
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-t", str(max(1, duration)), "-i", str(img),
        "-vf", vf, *output_encode_args(opts.encoder, "6000k"), "-an", str(out),
    ]
    run(cmd, log, f"Building image clip {img.name}", cancel)


def normalise_video(src: Path, out: Path, width: int, height: int, opts: RenderOptions, log: Path, cancel: CancelToken | None = None) -> None:
    """Scale/pad an arbitrary video to fit a column, stripping its audio."""
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30"
    )
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vf", vf, *output_encode_args(opts.encoder, "6000k"), "-an", str(out)]
    run(cmd, log, f"Normalising {src.name}", cancel)


def build_pool_column(
    pool: list[Path],
    target_duration: int,
    out: Path,
    width: int,
    height: int,
    opts: RenderOptions,
    tmp: Path,
    log: Path,
    label: str,
    cancel: CancelToken | None = None,
) -> None:
    """Build one side column by independently shuffling the shared media pool.

    Images become ``duration_per_image`` second clips; videos play at their
    native length (scaled/padded to the column). Items keep looping in a fresh
    random order until the column reaches ``target_duration``.
    """
    if not pool:
        run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=black:s={width}x{height}:d={max(target_duration, 30)}",
            *output_encode_args(opts.encoder, "6000k"), str(out)
        ], log, f"Creating blank {label} section", cancel)
        return

    # Pre-normalise each unique pool item once into a column-sized clip, then
    # reuse the normalised clips when assembling the (looping) concat list.
    clips: dict[Path, Path] = {}
    for idx, item in enumerate(pool):
        cancel and cancel.check()
        clip = tmp / f"pool_{label}_{idx:03d}.mp4"
        if _is_image(item):
            image_to_clip(item, clip, opts.duration_per_image, width, height, opts, log, cancel)
        else:
            normalise_video(item, clip, width, height, opts, log, cancel)
        clips[item] = clip

    durations = {item: max(probe_duration(clip), 1.0) for item, clip in clips.items()}

    order: list[Path] = []
    current = 0.0
    shuffled = pool[:]
    random.shuffle(shuffled)
    cursor = 0
    while current < target_duration:
        if cursor >= len(shuffled):
            random.shuffle(shuffled)
            cursor = 0
        item = shuffled[cursor]
        cursor += 1
        order.append(clips[item])
        current += durations[item]

    concat = tmp / f"{out.stem}_pool.txt"
    with concat.open("w", encoding="utf-8") as fh:
        for clip in order:
            fh.write(concat_file_line(clip))

    # The pre-normalised clips are already exactly width x height with identical
    # codec parameters, so re-encode the concatenated stream (trimmed to the
    # target duration) without re-scaling, which would otherwise reintroduce odd
    # dimensions that libx264 rejects.
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
        "-t", str(target_duration), "-vf", "setsar=1",
        *output_encode_args(opts.encoder, "6000k"), str(out),
    ]
    run(cmd, log, f"Building {label} column", cancel)


def build_center(
    center_media: list[Path],
    sides_pool: list[Path],
    out: Path,
    height: int,
    opts: RenderOptions,
    tmp: Path,
    log: Path,
    cancel: CancelToken | None = None,
) -> None:
    """Build the middle column.

    Uses the dedicated center uploads when present; otherwise falls back to the
    shared sides pool; otherwise a blank black column.
    """
    source = center_media or sides_pool
    if not source:
        run([
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=black:s=1148x{height}:d=30",
            *output_encode_args(opts.encoder, "8000k"), str(out)
        ], log, "Creating blank middle video", cancel)
        return

    # Build each center item into a uniform-height clip, then concatenate.
    clips: list[Path] = []
    center_image_dur = max(opts.duration_per_image, 1)
    for idx, item in enumerate(source, start=1):
        cancel and cancel.check()
        clip = tmp / f"center_{idx:03d}.mp4"
        if _is_image(item):
            vf = f"scale=-2:{height},setsar=1,fps=30"
            cmd = [
                "ffmpeg", "-y", "-loop", "1", "-t", str(center_image_dur), "-i", str(item),
                "-vf", vf, *output_encode_args(opts.encoder, "8000k"), "-an", str(clip),
            ]
            run(cmd, log, f"Building center image {item.name}", cancel)
        else:
            clean_video(item, clip, opts, log, cancel)
        clips.append(clip)

    if len(clips) == 1:
        # A single clip: scale to height and copy through.
        cmd = ["ffmpeg", "-y", "-i", str(clips[0]), "-vf", f"scale=-2:{height},setsar=1", *output_encode_args(opts.encoder, "12000k"), str(out)]
        run(cmd, log, "Building middle video", cancel)
        return

    concat = tmp / "center_list.txt"
    # Re-encode through a common pixel format/height so concat is safe across
    # mixed sources.
    normalised: list[Path] = []
    for idx, clip in enumerate(clips, start=1):
        norm = tmp / f"center_norm_{idx:03d}.mp4"
        cmd = ["ffmpeg", "-y", "-i", str(clip), "-vf", f"scale=-2:{height},setsar=1", *output_encode_args(opts.encoder, "12000k"), "-an", str(norm)]
        run(cmd, log, f"Normalising center clip {idx}", cancel)
        normalised.append(norm)
    with concat.open("w", encoding="utf-8") as fh:
        for clip in normalised:
            fh.write(concat_file_line(clip))
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(out)]
    run(cmd, log, "Building middle video", cancel)


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
            # a brighter cyan glow pass, then crisp white text on top. No drop
            # shadows are used -- the glow comes purely from the translucent
            # cyan/purple border passes.
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
                    y="h-120",
                ),
            ])
        t += caption_duration
    return ",".join(pieces)


def render_job(job: Job, data_dir: Path, progress: Progress, cancel: CancelToken | None = None) -> Path:
    start = time.time()
    job_dir = data_dir / "jobs" / job.id
    tmp = data_dir / "tmp" / job.id
    outputs = data_dir / "outputs"
    tmp.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)
    log = job_dir / "render.log"
    opts = job.options

    center_media, sides_pool, music = collect_inputs(job_dir)
    center_images = sum(1 for p in center_media if _is_image(p))
    center_videos = sum(1 for p in center_media if _is_video(p))
    side_images = sum(1 for p in sides_pool if _is_image(p))
    side_videos = sum(1 for p in sides_pool if _is_video(p))
    job.input_counts = {
        "center": len(center_media),
        "sides": len(sides_pool),
        "images": center_images + side_images,
        "videos": center_videos + side_videos,
        "music": 1 if music else 0,
    }
    if not center_media and not sides_pool:
        raise ValueError("Upload at least one center or side image/video before starting a render.")

    total_width, total_height, left_width, right_width = resolution_values(opts.resolution)
    progress(5, f"Starting {total_width}x{total_height} render with {opts.encoder} encoder")

    middle = tmp / "middle.mp4"
    build_center(center_media, sides_pool, middle, total_height, opts, tmp, log, cancel)
    progress(35, "Built middle video")

    middle_duration = max(int(probe_duration(middle) + 0.5), 30)

    left = tmp / "left.mp4"
    right = tmp / "right.mp4"
    # Left and right independently shuffle the shared pool, so they may overlap.
    build_pool_column(sides_pool, middle_duration, left, left_width, total_height, opts, tmp, log, "left", cancel)
    progress(50, "Built left side column")
    build_pool_column(sides_pool, middle_duration, right, right_width, total_height, opts, tmp, log, "right", cancel)
    progress(65, "Built right side column")

    final_layout = tmp / "final_layout.mp4"
    middle_width = _even(total_width - left_width - right_width)
    filter_complex = (
        f"[0:v]scale={left_width}:{total_height},setsar=1[left];"
        f"[1:v]scale={middle_width}:{total_height}:force_original_aspect_ratio=increase,"
        f"crop={middle_width}:{total_height},setsar=1[middle];"
        f"[2:v]scale={right_width}:{total_height},setsar=1[right];"
        "[left][middle][right]hstack=inputs=3[v]"
    )
    cmd = ["ffmpeg", "-y", "-i", str(left), "-i", str(middle), "-i", str(right), "-filter_complex", filter_complex, "-map", "[v]", *output_encode_args(opts.encoder, opts.video_bitrate), str(final_layout)]
    run(cmd, log, "Compositing three-section layout", cancel)
    progress(78, "Composited layout")

    total_duration = max(int(probe_duration(final_layout) + 0.5), middle_duration)
    vf = "format=yuv420p," + drawtext_filter(opts.captions, total_duration, opts.caption_duration, opts.caption_order, opts.caption_style)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_name = f"{safe_name(job.name).replace(' ', '_')}_{timestamp}.mp4"
    output = outputs / out_name

    layout_duration = probe_duration(final_layout)
    # Encode captions/audio WITHOUT +faststart first. Combining +faststart with a
    # long 1440p clip and a second (audio) input forces the muxer to buffer the
    # whole file in memory, which can be OOM-killed on small home servers. We add
    # faststart afterwards with a cheap stream-copy remux instead.
    # Burn in the captions first (video only, no +faststart yet to keep muxing
    # memory low on small home servers).
    captioned = tmp / "captioned.mp4"
    run(
        ["ffmpeg", "-y", "-i", str(final_layout), "-vf", vf,
         *encoder_args(opts.encoder, opts.video_bitrate), "-pix_fmt", "yuv420p", "-an", str(captioned)],
        log, "Adding captions", cancel,
    )

    if music:
        # Build a finite, video-length audio track first (loop the music, then
        # trim). Muxing two finite-length streams interleaves cleanly. Looping
        # an input directly inside the final mux makes FFmpeg buffer the whole
        # video in the muxing queue while it waits on the never-ending audio,
        # which can be OOM-killed on small servers.
        music_duration = max(probe_duration(music), 0.1)
        loops = max(0, int(layout_duration // music_duration) + 1)
        audio_track = tmp / "audio.m4a"
        run(
            ["ffmpeg", "-y", "-stream_loop", str(loops), "-i", str(music),
             "-t", f"{layout_duration:.3f}", "-c:a", "aac", "-b:a", "192k", "-vn", str(audio_track)],
            log, "Preparing music track", cancel,
        )
        run(
            ["ffmpeg", "-y", "-i", str(captioned), "-i", str(audio_track),
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "copy",
             "-shortest", "-movflags", "+faststart", str(output)],
            log, "Muxing audio", cancel,
        )
    else:
        # Cheap remux to relocate the moov atom to the front for fast web start.
        run(["ffmpeg", "-y", "-i", str(captioned), "-c", "copy", "-movflags", "+faststart", str(output)],
            log, "Finalising container", cancel)
    progress(98, f"Finalising ({int(time.time() - start)}s)")

    shutil.rmtree(tmp, ignore_errors=True)
    progress(100, "Completed")
    return output
