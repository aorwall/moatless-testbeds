#!/bin/bash
set -e

exec gunicorn --bind 0.0.0.0:8000 --workers 4 --timeout 30 --log-level info --capture-output --enable-stdio-inheritance "testbed.testbed.server:create_app()"