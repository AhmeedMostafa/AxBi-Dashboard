# ─────────────────────────────────────────────────────────────────────────────
# AxBi backend image — Django (ASGI/daphne) + Celery workers share this image.
# The same image runs three roles (web, default worker, forecasts worker); the
# role is selected by the `command` in docker-compose.yml.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Don't buffer stdout/stderr (live logs), don't write .pyc files.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

# System libraries:
#  - build-essential / libffi-dev: compile any sdists (prophet/cmdstanpy, etc.)
#  - WeasyPrint runtime: cairo, pango, gdk-pixbuf, libjpeg, shared-mime-info, fonts
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libjpeg62-turbo \
        shared-mime-info \
        fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer cached unless requirements change).
COPY requirements.docker.txt ./
RUN pip install --upgrade pip && pip install -r requirements.docker.txt

# Copy the backend source (see .dockerignore for exclusions: venv, logs, .env...).
COPY . .

EXPOSE 8000

# Default = web role: apply Django's internal sqlite migrations, then serve
# HTTP + WebSocket (/ws) via daphne. Worker roles override this in compose.
CMD ["sh", "-c", "python manage.py migrate --noinput && daphne -b 0.0.0.0 -p 8000 core.asgi:application"]
