FROM python:3.12-slim

# FFmpeg is a hard runtime dependency: it is the render engine, not an optional extra.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-noto-core \
        fonts-noto-devanagari \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir -e ".[postgres]"

RUN useradd --create-home --uid 10001 agency \
    && mkdir -p /artifacts /secrets \
    && chown -R agency:agency /app /artifacts /secrets
USER agency

ENV ARTIFACT_ROOT=/artifacts \
    CHANNEL_DNA_ROOT=/channel_dna \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
