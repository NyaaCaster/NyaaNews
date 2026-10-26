FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
RUN apt-get update && apt-get install -y --no-install-recommends cron tini openssh-client tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock /app/
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY src /app/src
COPY ops/container.py /app/ops/container.py
ENTRYPOINT ["/usr/bin/tini","--","python","/app/ops/container.py"]
