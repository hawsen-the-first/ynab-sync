#!/bin/sh
set -e

echo "Running database migrations..."
python migrate.py

echo "Starting application..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
