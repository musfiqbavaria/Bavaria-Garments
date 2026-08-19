# Running Project 1 locally

Two ways to run it. The Docker stack is the realistic one — same Postgres, Redis,
Celery and Nginx as a server. SQLite is the quick one for editing code.

---

## 1. Docker stack (recommended)

**Needs:** Docker Desktop running. Nothing else — no Python, no Postgres, no
Redis on the host.

```bash
cp .env.example .env      # only if you don't already have a .env
./scripts/run_local.sh
```

Then open **http://localhost** and sign in with `DEFAULT_ADMIN_USERNAME` /
`DEFAULT_ADMIN_PASSWORD` from `.env`. **Change that password immediately.**

The first run takes a few minutes: it pulls the base images, compiles `psycopg`,
then waits for Postgres, migrates, collects static files and seeds the registry,
the organisation tree and demo data. The script waits until the site genuinely
answers before saying it is ready, rather than reporting success as soon as the
containers launch.

### What comes up

| Service | What it does | Reachable from the host |
|---|---|---|
| `nginx` | Reverse proxy, security headers, static files | **http://localhost** |
| `web` | Django on gunicorn, 3 workers | via nginx only |
| `db` | PostgreSQL 17 | no — use `docker compose exec db psql …` |
| `redis` | Celery broker, login throttle, cache | no |
| `worker` | Celery worker | — |
| `beat` | Celery scheduler: report slots, FX refresh, audit purge | — |

Only nginx publishes a port. The database is not exposed to the host on purpose.

### Everyday commands

```bash
./scripts/run_local.sh --logs     # start, then follow the logs
./scripts/run_local.sh --down     # stop, keep the data
./scripts/run_local.sh --fresh    # start over from an empty database

docker compose logs -f web        # follow just the application
docker compose exec web bash      # shell inside the container
docker compose exec web python manage.py <command>
docker compose exec db psql -U emerald -d emerald_rozalia_project1
```

### Useful management commands

```bash
docker compose exec web python manage.py report_unscoped      # sites still unassigned
docker compose exec web python manage.py sync_roles --check   # role/group drift
docker compose exec web python manage.py fetch_exchange_rates # no-op until configured
docker compose exec web python manage.py rebuild_org_paths    # repair the org tree
docker compose exec web python manage.py test portal --settings=core.settings_test
```

---

## 2. Without Docker (SQLite)

Faster for editing code, but it is not what runs on a server: no concurrent
writes, no Celery, and Redis-backed features fall back with a logged warning.

```bash
pip install -r requirements.txt
```

Then in `.env`:

```
DB_ENGINE=sqlite
REDIS_URL=
```

```bash
python manage.py migrate
python manage.py seed_project1
python manage.py runserver
```

Open **http://127.0.0.1:8000**.

---

## Things that will confuse you if nobody says them

**`/media/` returns 404, deliberately.** Uploads include confidential documents,
payslips, QC photographs and buyer signatures. Serving them from a URL would
bypass the per-file permission check and the audit log, so every file is
delivered through `/files/<type>/<id>/<view|preview|download|print>/` instead.

**A blank dashboard is usually the scope, not a bug.** Data is filtered to the
signed-in user's `OrganizationNode`. A user with no scope is head office and sees
everything; a user assigned to a factory sees that factory and below. Superusers
are never filtered.

**403 rather than a page** means the role is not permitted on that route. The
whole policy is one table: `portal/authorization.py`. An unclassified route is
refused by design, not by accident.

**You cannot approve your own request.** The approval endpoint requires a
manager-or-above role, refuses the requester, and refuses a request that has
already been decided. Create a second user to test approvals.

**Report slots run on Asia/Dhaka**, because the attendance engine anchors
check-in, breaks and checkout against that clock. Changing `TIME_ZONE` changes
payroll — see the warning in `.env.example`.

**Exchange rates are seeded as indicative `SEED` rows** so consolidated reporting
works offline. Consolidated figures refuse to convert once a rate is older than
`EXCHANGE_RATE_MAX_AGE_DAYS` rather than reporting stale numbers as current.

**Two departments do not exist**: Sewing (#42) and Print (#39). They are visible
in the navigation on purpose, as outstanding work. Twenty other legacy pages were
superseded by a later module; those redirect to their replacement.

---

## Troubleshooting

**"the Docker daemon is not reachable"** — start Docker Desktop and wait for it to
report *Running*, then re-run the script.

**Every POST fails a CSRF check** — `DJANGO_CSRF_TRUSTED_ORIGINS` must contain the
origin you are browsing, including the port. nginx serves on port 80, so it needs
`http://localhost` with no port. GET pages look fine while this is wrong, so the
symptom is "forms silently do nothing".

**`ImproperlyConfigured: DJANGO_SECRET_KEY is still the development value`** — the
guard working as intended. Generate one:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

**"models have changes with no matching migration"** — the entrypoint refuses to
start rather than inventing a migration on the server. Generate and review it
locally:

```bash
python manage.py makemigrations portal
```

**Port 80 already in use** — something else is on it (IIS, Skype, another stack).
Change the nginx mapping in `docker-compose.yml` to e.g. `"8080:80"` and add
`http://localhost:8080` to `DJANGO_CSRF_TRUSTED_ORIGINS`.

**Migrations are slow on first boot** — 173 tables and ~900 indexes. It is a
one-off.

---

## Before this goes anywhere near a server

Not local concerns, but do not lose track of them:

1. Generate a fresh `DJANGO_SECRET_KEY` and set `DJANGO_DEBUG=0`.
2. Enable TLS: follow the instructions at the foot of `nginx/default.conf`, then
   set `DJANGO_SECURE_SSL=1`. Not before — without a TLS listener every request
   redirects to a closed port.
3. Set `DJANGO_AUTO_MIGRATE=0` and use `scripts/deploy.sh`, so a rollout is an
   explicit step and web replicas cannot race each other's migrations.
4. Change `POSTGRES_PASSWORD` and the admin password.
5. Run `report_unscoped`, assign the sites it lists, then set `TENANCY_STRICT=1`.
6. Point `BACKUP_DIR` at storage on another host and check `scripts/backup.sh`
   runs on a schedule.

Current status and known gaps: `TECHNICAL_ASSESSMENT.md`.
