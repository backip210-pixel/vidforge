# VidForge deployment

This project is Docker-first. The intended default deployment on ZimaOS is Docker Compose.

## Option 1 — ZimaOS / Docker Compose from GitHub source

Use this when you have cloned the repository with GitHub Desktop or directly on the server.

```bash
git clone https://github.com/backip210-pixel/vidforge.git
cd vidforge
docker compose up -d --build
```

Open:

```text
http://YOUR-ZIMAOS-IP:8080
```

Persistent app data is stored in:

```text
./data
```

## Option 2 — ZimaOS custom app / Compose import

Paste or import the repository `docker-compose.yml` into ZimaOS/CasaOS. It includes:

- exposed web port `8080`
- persistent volume `./data:/data`
- upload support for images, videos, music and caption text files
- healthcheck
- app metadata labels
- app icon URL
- screenshot URLs

App icon:

```text
https://raw.githubusercontent.com/backip210-pixel/vidforge/main/app/static/logo.svg
```

Web UI:

```text
http://YOUR-ZIMAOS-IP:8080
```

## Option 3 — Pull prebuilt GHCR image

After GitHub Actions has built the package, you can deploy without building locally:

```bash
docker compose -f docker-compose.ghcr.yml up -d
```

Or with plain Docker:

```bash
docker run -d \
  --name vidforge \
  --restart unless-stopped \
  -p 8080:8080 \
  -v "$(pwd)/data:/data" \
  -e APP_DATA_DIR=/data \
  ghcr.io/backip210-pixel/vidforge:latest
```

## Optional AMD/Intel hardware encoding on ZimaOS

The default software renderer is the safest option and should work everywhere. For AMD/Intel VAAPI hardware encoding, uncomment this volume in the compose file:

```yaml
- /dev/dri:/dev/dri
```

Then choose `VAAPI` in the VidForge render form.

If the job fails, requeue it with `Software x264`.

## Optional basic auth

Uncomment and set these environment variables:

```yaml
APP_USERNAME: admin
APP_PASSWORD: change-me
```

Then restart:

```bash
docker compose up -d
```
