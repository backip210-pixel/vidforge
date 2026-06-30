FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_DATA_DIR=/data \
    APP_HOST=0.0.0.0 \
    APP_PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg \
       fonts-dejavu-core \
       fontconfig \
       tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

VOLUME ["/data"]
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "app.main"]
