#!/bin/bash
set -e

exec gunicorn --bind 0.0.0.0:8000 --workers 12 --threads 4 --timeout 300 "main:create_app()"
