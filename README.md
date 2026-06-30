<p align="center">
  <img src="docs/logo.svg" width="112" alt="VidForge logo" />
</p>

<h1 align="center">VidForge</h1>

<p align="center">
  A Docker-first FFmpeg render queue for ZimaOS and home servers.
</p>

<p align="center">
  <a href="https://github.com/backip210-pixel/vidforge">Repository</a> ·
  <a href="DEPLOYMENT.md">Deployment guide</a>
</p>

![VidForge dashboard](docs/screenshots/dashboard.svg)

VidForge is a containerised web dashboard for creating the three-section FFmpeg compilations from your original macOS Bash script. It defaults to safe CPU rendering so it works on AMD, Intel, ARM and VPS hosts without Apple-only `h264_videotoolbox` dependencies.

## Default deployment: pull the container from GitHub

The intended deployment is now:

```text
GitHub repo -> GitHub Actions -> GHCR container image -> ZimaOS pulls image
```

Once this repo has been pushed and the GitHub Actions workflow has completed, deploy this image on ZimaOS:

```text
ghcr.io/backip210-pixel/vidforge:latest
```

Recommended container settings:

```text
Port: 8080 container -> 8080 host
Volume: ./data -> /data
Environment: APP_DATA_DIR=/data, APP_PORT=8080, TEMP_MAX_AGE_HOURS=12
```

Open:

```text
http://YOUR-ZIMAOS-IP:8080
```

The server does **not** need a source checkout or local Docker build for normal deployment.

For detailed ZimaOS, Compose and Docker Run options, see [`DEPLOYMENT.md`](DEPLOYMENT.md).

## Quick Docker Compose deployment

The default compose file pulls the GitHub Container Registry image:

```bash
docker compose up -d
```

If you are developing locally and want to build from source instead:

```bash
docker compose -f docker-compose.build.yml up -d --build
```

## What it does

- Upload a separate batch of images, videos, optional music and captions per job.
- Stores each job under the Docker-mounted `data/jobs/<job-id>` folder.
- Renders one job at a time with a lightweight built-in queue so FFmpeg processes do not collide.
- Writes finished MP4 files to `data/outputs`.
- Uses isolated per-job temp folders under `data/tmp/<job-id>`.
- Deletes a job's temp folder after completion and includes a 12-hour temp-folder cleanup task.
- Provides live progress, logs, requeue, delete and download actions from a responsive web UI.
- Lets users type captions directly or upload a UTF-8 `.txt` caption file.
- Loops captions across the video in either random order or sequential order.
- Supports caption styling: white text with cyan/purple neon glow or classic boxed captions.
- Supports 2560×1440 and 3440×1440 ultrawide layouts.
- Supports software x264 by default plus optional VAAPI, Intel QSV and NVIDIA NVENC selections.
- Includes an application logo, web favicon/manifest, compose metadata labels and screenshots.

## Docker files included

| File | Purpose |
| --- | --- |
| `Dockerfile` | Builds the VidForge app image with Python, FFmpeg and fonts. |
| `docker-compose.yml` | Default deployment using the prebuilt GHCR image. |
| `compose.yaml` | Same default deployment for systems that prefer `compose.yaml`. |
| `ZIMAOS_COMPOSE.yml` | Minimal image-only compose file for ZimaOS custom-app/import workflows. |
| `docker-compose.ghcr.yml` | Alias of the image-only GHCR deployment compose. |
| `docker-compose.build.yml` | Developer-only local source build. |
| `.github/workflows/docker.yml` | Builds and publishes the Docker image to GHCR on pushes to `main`. |
| `DEPLOYMENT.md` | Detailed Docker, ZimaOS and GHCR deployment instructions. |

## Folder layout after deployment

```text
data/
  jobs/
    <job-id>/
      input/
        images/
        videos/
        music/
        captions.txt
      render.log
  outputs/
    your_finished_render.mp4
  tmp/
    <job-id>/        # working files, automatically purged
  jobs.json          # queue state
```

## First-time GitHub package note

The GitHub Actions workflow publishes:

```text
ghcr.io/backip210-pixel/vidforge:latest
```

After the first successful workflow run, check the repo's **Packages** section. If ZimaOS cannot pull the image, make the GHCR package public or configure Docker/GHCR authentication on the server.

## Caption options

Each render job can use captions typed into the dashboard and/or uploaded from a UTF-8 `.txt` file. Use one caption per line.

Caption ordering options:

- **Random loop**: shuffles the caption pool and keeps looping it across the whole video.
- **Sequential loop**: repeats captions in the order provided.

Caption style options:

- **White + neon glow**: bright white foreground text with cyan/purple neon highlighting.
- **Classic boxed text**: the simpler high-contrast black box style.

## Encoder choices

The original script used macOS `h264_videotoolbox`, which will not work in Linux Docker on an AMD ZimaOS server. VidForge defaults to:

- **Software x264**: safest, portable, recommended first option.

Optional advanced encoders are available in the UI:

- **VAAPI**: AMD/Intel Linux hardware encoding. Requires mounting `/dev/dri:/dev/dri` in the container and host driver support.
- **Intel QSV**: Intel Quick Sync systems.
- **NVIDIA NVENC**: NVIDIA hosts with the NVIDIA container runtime.

If hardware rendering fails, requeue the job with **Software x264**.

## Application logo

The application logo is included here:

```text
app/static/logo.svg
docs/logo.svg
```

The dashboard uses it as the header logo and favicon. The Docker Compose metadata also exposes the raw GitHub icon URL:

```text
https://raw.githubusercontent.com/backip210-pixel/vidforge/main/app/static/logo.svg
```

## Optional basic authentication

For LAN-only use you can leave auth disabled. To enable browser basic auth, set these environment variables:

```yaml
environment:
  APP_USERNAME: admin
  APP_PASSWORD: change-me
```

## Screenshots

### Desktop

![Desktop dashboard](docs/screenshots/dashboard.svg)

### Mobile

![Mobile dashboard](docs/screenshots/mobile.svg)

## Development

Run without Docker:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
APP_DATA_DIR=./data python -m app.main
```

The container image installs FFmpeg and DejaVu fonts automatically.

## Notes

- Uploaded files are intentionally stored in `data/jobs/<job-id>/input` for reproducibility and requeueing.
- Temporary FFmpeg intermediates are stored in `data/tmp/<job-id>` and removed after a successful or failed render. A periodic cleanup also purges stale temp folders older than 12 hours when no job is using them.
- The queue is intentionally single-worker to avoid heavy FFmpeg jobs colliding on a small home server.
