FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build deps for argon2-cffi, removed after install to keep the image small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Dependencies resolve from pyproject alone, so this layer is cached until the
# dependency list itself changes -- application edits do not reinstall anything.
#
# Runtime only: no `[dev]`. pytest, ruff and mypy have no business in a
# production image; they enlarge it and widen the dependency surface for no
# runtime benefit. CI installs them separately.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install "."

COPY . .

RUN apt-get purge -y build-essential && apt-get autoremove -y

# Run as a non-root user.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health').status==200 else 1)"

CMD ["python", "run.py"]
