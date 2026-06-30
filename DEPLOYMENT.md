# VidForge deployment

VidForge is designed to be deployed as a locked-down container. You should not need to clone the repository on your ZimaOS server once the GitHub Container Registry image has been built.

## How the GitHub-to-container flow works

1. Push this repository to GitHub:

   ```text
   https://github.com/backip210-pixel/vidforge
   ```

2. GitHub Actions builds the Docker image from the `Dockerfile`.
3. The image is published to GitHub Container Registry:

   ```text
   ghcr.io/backip210-pixel/vidforge:latest
   ```

4. ZimaOS pulls that image and runs it. No source checkout is required on the server.

> After the first successful GitHub Actions run, check the repository's **Packages** section. If the package is private and ZimaOS cannot pull it, change the package visibility to public or configure GHCR authentication on the server.

## Option 1 — ZimaOS custom app using the prebuilt image

Create a custom Docker/Compose app in ZimaOS and use this image:

```text
ghcr.io/backip210-pixel/vidforge:latest
```

Recommended settings:

```text
Container name: vidforge
Port: 8080 container -> 8080 host
Volume: ./data -> /data
Restart policy: unless-stopped
```

Environment variables:

```text
APP_DATA_DIR=/data
APP_PORT=8080
TEMP_MAX_AGE_HOURS=12
```

Optional app icon URL:

```text
https://raw.githubusercontent.com/backip210-pixel/vidforge/main/app/static/logo.svg
```

Open:

```text
http://YOUR-ZIMAOS-IP:8080
```

## Option 2 — Paste/import Compose into ZimaOS

Use either:

```text
docker-compose.yml
compose.yaml
ZIMAOS_COMPOSE.yml
```

The default compose file now uses the prebuilt GHCR image, not a local build.

Minimal compose:

```yaml
services:
  vidforge:
    image: ghcr.io/backip210-pixel/vidforge:latest
    container_name: vidforge
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./data:/data
    environment:
      APP_DATA_DIR: /data
      APP_PORT: 8080
      TEMP_MAX_AGE_HOURS: 12
```

## Option 3 — Plain Docker run

```bash
docker run -d \
  --name vidforge \
  --restart unless-stopped \
  -p 8080:8080 \
  -v "$(pwd)/data:/data" \
  -e APP_DATA_DIR=/data \
  -e APP_PORT=8080 \
  -e TEMP_MAX_AGE_HOURS=12 \
  ghcr.io/backip210-pixel/vidforge:latest
```

## Option 4 — Developer/source build only

Only use this when developing or testing changes locally:

```bash
git clone https://github.com/backip210-pixel/vidforge.git
cd vidforge
docker compose -f docker-compose.build.yml up -d --build
```

## Optional AMD/Intel hardware encoding on ZimaOS

The default software renderer is the safest option and should work everywhere. For AMD/Intel VAAPI hardware encoding, mount the device into the container:

```yaml
volumes:
  - ./data:/data
  - /dev/dri:/dev/dri
```

Then choose `VAAPI` in the VidForge render form.

If the job fails, requeue it with `Software x264`.

## Optional basic auth

Set these environment variables:

```yaml
APP_USERNAME: admin
APP_PASSWORD: change-me
```

Then restart the container.
