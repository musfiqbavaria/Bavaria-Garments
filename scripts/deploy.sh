#!/usr/bin/env bash
set -euo pipefail
docker compose up -d --build
docker compose exec web python manage.py makemigrations portal --noinput
docker compose exec web python manage.py migrate --run-syncdb --noinput
docker compose exec web python manage.py seed_project1
docker compose exec web python manage.py collectstatic --noinput
docker compose exec web python manage.py check
docker compose ps
