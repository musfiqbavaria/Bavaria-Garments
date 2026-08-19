# Running Project 1 locally

Step-by-step guide to get the platform running on your own machine.

Two ways to do it:

- **[Docker stack](#docker-stack-recommended)** — the same Postgres, Redis, Celery and Nginx a
  server runs. Use this unless you have a reason not to.
- **[Without Docker](#running-without-docker)** — SQLite, faster for editing code, but not what
  production looks like.

---

## Docker stack (recommended)

**You need:** Docker Desktop. Nothing else — no Python, no Postgres, no Redis on
your machine.

### Step 1 — Start Docker Desktop

Launch it and **wait until it reports "Running"**. This takes 30–90 seconds and
it does not start automatically.

Check it is ready:

```bash
docker info
```

If that errors, Docker is not up yet. Everything below fails until it is.

### Step 2 — Open the project folder

```bash
cd "g:/Projects/Emerald Rozalia/Emerald_Rozalia_Project1_Python_Server_Ready"
```

### Step 3 — Check you have a `.env`

```bash
ls .env
```

If it is missing:

```bash
cp .env.example .env
```

Then open it and set `DB_ENGINE=postgres`, plus a `POSTGRES_PASSWORD` and a
`DEFAULT_ADMIN_PASSWORD` of your own. `.env` is gitignored and never leaves your
machine.

### Step 4 — Start everything

```bash
./scripts/run_local.sh
```

That is the whole thing. It builds the images, starts all six services, waits for
Postgres, applies migrations, collects static files, seeds the data on first boot,
and then polls until the site genuinely answers before telling you it is ready.

- **First run:** a few minutes — it pulls the base images and compiles `psycopg`.
- **Later runs:** roughly 30 seconds.

You are ready when you see:

```
==> ready
    Site            http://localhost
```

### Step 5 — Open the site

**http://localhost** — note there is **no port number**. Nginx serves on port 80,
not 8000.

| | |
|---|---|
| Username | value of `DEFAULT_ADMIN_USERNAME` in `.env` (default `admin`) |
| Password | value of `DEFAULT_ADMIN_PASSWORD` in `.env` |

**Change that password once you are signed in.**

Django admin is at **http://localhost/admin/**.

### Step 6 — Stopping

```bash
./scripts/run_local.sh --down
```

Stops the containers and keeps your data. Next time, run `./scripts/run_local.sh`
again and it comes straight back.

---

## What is running

| Service | What it does | Reachable from your machine |
|---|---|---|
| `nginx` | Reverse proxy, security headers, static files | **http://localhost** |
| `web` | Django on gunicorn, 3 workers | through nginx only |
| `db` | PostgreSQL 17 | no — use `docker compose exec db psql …` |
| `redis` | Celery broker, login throttle, cache | no |
| `worker` | Celery worker | — |
| `beat` | Scheduler: report slots, FX refresh, audit purge | — |

Only nginx publishes a port. The database is deliberately not exposed.

---

## Everyday commands

```bash
./scripts/run_local.sh --logs     # start, then follow the logs live
./scripts/run_local.sh --down     # stop, keep the data
./scripts/run_local.sh --fresh    # wipe the database and start clean

docker compose logs -f web        # follow just the application
docker compose logs --tail 40 web # last 40 lines
docker compose exec web bash      # shell inside the container
docker compose ps                 # what is up, and is it healthy
```

Run a Django management command:

```bash
docker compose exec web python manage.py report_unscoped
docker compose exec web python manage.py sync_roles --check
docker compose exec web python manage.py fetch_exchange_rates
docker compose exec web python manage.py rebuild_org_paths
```

Run the test suite against the real Postgres:

```bash
docker compose exec web python manage.py test portal --settings=core.settings_test
```

Open the database:

```bash
docker compose exec db psql -U emerald -d emerald_rozalia_project1
```

---

## Four things that look broken but are not

**`/media/` returns 404.** Deliberate. Uploads include confidential documents,
payslips, QC photographs and buyer signatures. Serving them from a URL would skip
the per-file permission check and the audit log, so every file is delivered
through `/files/<type>/<id>/<view|preview|download|print>/` instead.

**A dashboard shows no data.** Usually the organisation scope, not a bug. Records
are filtered to the signed-in user's site: a user assigned to a factory sees that
factory and everything below it. A user with no scope is head office and sees
everything, which is how the seeded `admin` account is set up. Superusers are
never filtered.

**You get 403 instead of a page.** That role is not permitted on that route. The
whole policy is one table in `portal/authorization.py`. A route nobody has
classified is refused on purpose, not by accident.

**You cannot approve your own request.** By design: approving needs a
manager-or-above role, the requester is refused, and a request that has already
been decided cannot be decided again. Create a second user to test approvals.

---

## Other things worth knowing

**Report slots run on Asia/Dhaka**, because the attendance engine anchors
check-in, breaks and checkout against that clock. Changing `TIME_ZONE` changes
payroll — see the warning in `.env.example`.

**Exchange rates are seeded as indicative `SEED` rows** so consolidated reporting
works offline. Replace them before trusting a figure. Reporting refuses to convert
once a rate is older than `EXCHANGE_RATE_MAX_AGE_DAYS` rather than presenting
stale numbers as current.

**Two departments do not exist yet:** Sewing (#42) and Print (#39). They are left
visible in the navigation on purpose, as outstanding work. Twenty other legacy
pages were superseded by a later module and redirect to their replacement.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `the Docker daemon is not reachable` | Docker Desktop is not running. Start it, wait for "Running", try again. |
| Forms silently do nothing | `DJANGO_CSRF_TRUSTED_ORIGINS` must contain the exact origin you are browsing, including the port. Nginx serves on port 80, so it needs `http://localhost` with **no** port. GET pages look fine while this is wrong. |
| `Port 80 already in use` | Something else has it (IIS, Skype, another stack). Change the nginx mapping in `docker-compose.yml` to `"8080:80"` and add `http://localhost:8080` to `DJANGO_CSRF_TRUSTED_ORIGINS`. |
| `ImproperlyConfigured: DJANGO_SECRET_KEY is still the development value` | The guard working as intended. Generate one: `python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"` |
| `models have changes with no matching migration` | The entrypoint refusing to invent a migration on a server. Generate and review it locally: `python manage.py makemigrations portal` |
| First boot is slow | 174 tables and roughly 1,300 indexes. One-off. |
| A container keeps restarting | `docker compose logs --tail 40 <service>` shows why. |

---

## Running without Docker

Faster for editing code, but it is not what runs on a server: no concurrent
writes, no Celery, and Redis-backed features fall back with a logged warning.

```bash
pip install -r requirements.txt
```

In `.env`:

```
DB_ENGINE=sqlite
REDIS_URL=
```

Then:

```bash
python manage.py migrate
python manage.py seed_project1
python manage.py runserver
```

Open **http://127.0.0.1:8000** — port 8000 here, unlike the Docker stack.

---

## Before this goes near a server

Not local concerns, but do not lose track of them:

1. Generate a fresh `DJANGO_SECRET_KEY` and set `DJANGO_DEBUG=0`.
2. Enable TLS: follow the instructions at the foot of `nginx/default.conf`, then
   set `DJANGO_SECURE_SSL=1`. **Not before** — without a TLS listener every
   request redirects to a closed port and the site becomes unreachable.
3. Set `DJANGO_AUTO_MIGRATE=0` and deploy with `scripts/deploy.sh`, so a rollout
   is an explicit step and web replicas cannot race each other's migrations.
4. Change `POSTGRES_PASSWORD` and the admin password.
5. Run `report_unscoped`, assign the sites it lists, then set `TENANCY_STRICT=1`.
6. Point `BACKUP_DIR` at storage on another host and put `scripts/backup.sh` on a
   schedule.

Current status and known gaps: **`TECHNICAL_ASSESSMENT.md`**.
