# language: Dockerfile, target: Railway (linux/amd64)
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    SOLVER_HEADLESS=1 \
    SOLVER_TRANSPORT=chromium \
    SOLVER_DB=/data/solver_jobs.db \
    SOLVER_BEST_PARAMS=/app/best_drag_params.json

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY mlbb_solver.py /app/mlbb_solver.py
COPY solver_pool.py /app/solver_pool.py
COPY jobs.py /app/jobs.py
COPY api_server.py /app/api_server.py
COPY best_drag_params.json /app/best_drag_params.json

RUN mkdir -p /data

EXPOSE 8000

CMD uvicorn api_server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'