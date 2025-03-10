#!/bin/bash
set -e

# Set environment variables for better performance
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

# Use uvicorn workers with gunicorn for FastAPI
exec gunicorn \
    --bind 0.0.0.0:8000 \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --timeout 900 \
    --keep-alive 120 \
    --access-logfile - \
    --error-logfile - \
    "main:create_app()"
