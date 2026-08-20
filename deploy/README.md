# deploy/

## Scheduled reports

There is nothing to install here. The department reports are scheduled by Celery
beat, which runs as its own service in `docker-compose.yml`; the schedule is
`CELERY_BEAT_SCHEDULE` in `core/settings.py` and the work is
`portal.tasks.generate_department_reports`, driven by the registry in
`portal/reporting.py`.

This directory previously held ten `*.cron.example` files invoking
`cd /path/to/project && /path/to/venv/bin/python manage.py run_x_auto_report`.
They were removed because:

- that layout does not exist on a Docker host, and nothing installed a crontab;
- they covered 9 of the 16 report models;
- following them alongside the beat service would have generated every report
  twice.

To generate a slot by hand - a catch-up after an outage, or a check during
deployment:

```bash
docker compose exec -T web python manage.py run_auto_reports --slot 08:00
docker compose exec -T web python manage.py run_auto_reports --slot 13:00 --date 2026-08-19
```

The slots are read in `TIME_ZONE` (Asia/Dhaka by default), which Celery beat
inherits from `CELERY_TIMEZONE`. Changing `TIME_ZONE` moves the reporting slots
as well as payroll - see the note in `.env.example`.
