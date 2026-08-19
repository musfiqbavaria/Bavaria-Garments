# ---------------------------------------------------------------------------
# Emerald Rozalia - Project 1
#
# Two stages so the compiler toolchain needed to build psycopg and Pillow does
# not ship in the runtime image, and the application runs as a non-root user.
# ---------------------------------------------------------------------------
FROM python:3.14.3-slim AS build
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.14.3-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# libpq5 is the runtime half of libpq-dev; curl is used by the healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /install /usr/local

# Run as an unprivileged user. The image previously ran everything as root, so a
# remote-code-execution bug in the application would have had root in the
# container along with the build toolchain to work with.
RUN useradd --create-home --uid 10001 appuser

COPY --chown=appuser:appuser . .
# /app itself is created root-owned by WORKDIR, and --chown only affects the files
# copied into it. Without this the unprivileged user cannot create new files in
# the working directory - which is exactly where celery beat writes its schedule.
# /app/run holds runtime state that must survive a rebuild.
RUN chmod +x scripts/*.sh \
    && mkdir -p /app/media /app/staticfiles /app/run \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Waits for the database, migrates and collects static before the command runs,
# so `docker compose up` yields a working application rather than 500s until
# someone remembers to migrate. Gated by DJANGO_AUTO_MIGRATE / DJANGO_AUTO_SEED.
ENTRYPOINT ["/app/scripts/entrypoint.sh"]

# Suits the web service. worker and beat serve no HTTP and override this in
# docker-compose.yml; inheriting it would mark them permanently unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/login/ >/dev/null || exit 1

# --access-logfile - so request logs reach the container log, matching the
# LOGGING configuration in core/settings.py.
CMD ["gunicorn","core.wsgi:application", \
     "--bind","0.0.0.0:8000", \
     "--workers","3", \
     "--timeout","120", \
     "--access-logfile","-", \
     "--error-logfile","-", \
     "--forwarded-allow-ips","*"]
