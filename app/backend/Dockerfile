# TaskBoard backend image (multi-stage build)

# ---------- Stage 1: build Python wheels ----------
FROM python:3.13-slim AS build
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---------- Stage 2: small runtime image ----------
FROM python:3.13-slim

# Version label shown in the UI footer. Build v2 with:
#   docker build --build-arg APP_VERSION=v2 -t <user>/taskboard-backend:v2 .
ARG APP_VERSION=v1
ENV APP_VERSION=${APP_VERSION} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install only the pre-built wheels (no compilers in the final image)
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

# Run as a non-root user
RUN useradd --uid 10001 --no-create-home appuser
COPY app.py .
USER 10001

EXPOSE 5000

# gunicorn: 2 worker processes, 4 threads each; long timeout for /api/stress
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "60", "--access-logfile", "-", "app:app"]
