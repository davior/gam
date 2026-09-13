FROM python:3.13-slim

WORKDIR /app

# curl for the compose healthcheck; ffmpeg for probing, thumbnails and clip extraction.
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ffmpeg \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini entrypoint.sh ./

RUN mkdir -p /app/data/db /app/data/media

# Single process, deliberately: background jobs will run as threads in this process and
# SQLite wants one writer. Scaling means moving the queue out, not adding workers.
ENTRYPOINT ["./entrypoint.sh"]
