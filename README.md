# VidForge

VidForge is a small containerised web dashboard for creating the three-section FFmpeg compilations from your original macOS Bash script. It is designed for ZimaOS/home servers and defaults to safe CPU rendering so it works on AMD, Intel, ARM and VPS hosts without Apple-only `h264_videotoolbox` dependencies.

![VidForge dashboard](docs/screenshots/dashboard.svg)

## What it does

- Upload a separate batch of images, videos and optional music per job.
- Stores each job under the Docker-mounted `data/jobs/<job-id>` folder.
- Renders one job at a time with a lightweight built-in queue so FFmpeg processes do not collide.
- Writes finished MP4 files to `data/outputs`.
- Uses isolated per-job temp folders under `data/tmp/<job-id>`.
- Deletes a job's temp folder after completion and includes a 12-hour temp-folder cleanup task.
- Provides live progress, logs, requeue, delete and download actions from a responsive web UI.
- Supports 2560×1440 and 3440×1440 ultrawide layouts.
- Supports software x264 by default plus optional VAAPI, Intel QSV and NVIDIA NVENC selections.

## Folder layout after deployment

```text
data/
  jobs/
    <job-id>/
      input/
        images/
        videos/
        music/
      render.log
  outputs/
    your_finished_render.mp4
  tmp/
    <job-id>/        # working files, automatically purged
  jobs.json          # queue state
```

## Quick start locally

```bash
git clone https://github.com/yourname/vidforge.git
cd vidforge
docker compose up -d --build
```

Open:

```text
http://YOUR-SERVER-IP:8080
```

## ZimaOS deployment via GitHub

1. Create a new GitHub repository and push this folder.
2. On ZimaOS, open your Docker/Compose app workflow.
3. Use this repository as the compose project source, or clone it manually:

   ```bash
   git clone https://github.com/yourname/vidforge.git
   cd vidforge
   docker compose up -d --build
   ```

4. Keep the `./data:/data` volume mapping from `docker-compose.yml`. That is where uploads, logs and outputs persist.
5. Visit `http://<zimaos-ip>:8080`.

## Encoder choices

The original script used macOS `h264_videotoolbox`, which will not work in Linux Docker on an AMD ZimaOS server. VidForge defaults to:

- **Software x264**: safest, portable, recommended first option.

Optional advanced encoders are available in the UI:

- **VAAPI**: AMD/Intel Linux hardware encoding. Requires mounting `/dev/dri:/dev/dri` in `docker-compose.yml` and host driver support.
- **Intel QSV**: Intel Quick Sync systems.
- **NVIDIA NVENC**: NVIDIA hosts with the NVIDIA container runtime.

If hardware rendering fails, requeue the job with **Software x264**.

## Optional basic authentication

For LAN-only use you can leave auth disabled. To enable browser basic auth, uncomment these environment variables in `docker-compose.yml`:

```yaml
environment:
  APP_USERNAME: admin
  APP_PASSWORD: change-me
```

Restart afterwards:

```bash
docker compose up -d
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
