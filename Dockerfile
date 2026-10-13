FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# Everything the app WRITES lives in /app/storage, so a volume can be mounted there.
# /app/data keeps the files shipped with the code (runbooks, sample logs); mounting a volume
# over it would hide them.
ENV DATABASE_PATH=/app/storage/incident_suite.db \
    CHROMA_PERSIST_DIR=/app/storage/chroma \
    UPLOAD_DIR=/app/storage/uploads \
    APP_LOG_FILE=/app/storage/logs/app.jsonl \
    HF_HOME=/app/storage/hf_cache

WORKDIR /app
COPY requirements.txt .
# CPU-only PyTorch first: the default Linux wheel bundles ~2.5 GB of CUDA libraries we don't use.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt

COPY . .
# Read-only files shipped with the code get their own folder, so they stay visible even if a
# volume is mounted over /app/data by mistake.
RUN mkdir -p /app/bundled && cp -r data/runbooks data/sample_logs /app/bundled/
ENV RUNBOOK_DIR=/app/bundled/runbooks \
    SAMPLE_LOG_DIR=/app/bundled/sample_logs

RUN useradd --create-home --uid 1000 appuser && mkdir -p data storage && chown -R appuser:appuser /app
USER appuser

# Hosting platforms such as Railway pass the port in $PORT; locally it defaults to 8000.
ENV PORT=8000
EXPOSE 8000 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ[\"PORT\"]}/health', timeout=3)" || exit 1
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
