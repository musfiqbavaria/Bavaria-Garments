# Deploying to a Hetzner server from GitHub

Step-by-step, from a fresh Hetzner Cloud server to a running site on HTTPS.

Repository: `https://github.com/musfiqbavaria/Bavaria-Garments.git`

Read this once before starting. Steps 1–9 get the site running on HTTP; step 10
puts it on HTTPS; step 11 is what you must do before letting real users in.

> **`.env` is never in git.** It holds the secret key, the database password and
> the admin password, and `.gitignore` excludes it. You create it on the server by
> hand, once. Do not copy your local `.env` up — it is a development config.

---

## 1. Push what you want to deploy

On your machine:

```bash
git status                    # must be clean
git push origin main
```

Check what the server will actually get:

```bash
git log --oneline -1 origin/main
```

If that commit is not what you expect, fix it locally before touching the server.

---

## 2. Create the server

Hetzner Cloud → **Add Server**:

| Setting | Value |
|---|---|
| Image | **Ubuntu 24.04** |
| Type | **CX22** minimum (2 vCPU / 4 GB). The build compiles `psycopg`, and Postgres, Redis, gunicorn, two Celery processes and nginx all run on this box. 2 GB will thrash. |
| SSH key | Add your public key. **Do not use a root password.** |
| Firewall | Create one now — see step 3 |

Note the public IPv4 address.

---

## 3. Lock down the network

In Hetzner Cloud → **Firewalls**, allow **inbound** only:

| Port | Protocol | Source | Why |
|---|---|---|---|
| 22 | TCP | your IP if fixed, else `0.0.0.0/0` | SSH |
| 80 | TCP | `0.0.0.0/0` | HTTP, and Let's Encrypt validation |
| 443 | TCP | `0.0.0.0/0` | HTTPS |

Leave everything else closed. **Postgres (5432) and Redis (6379) must never be
open** — the compose file deliberately does not publish them, and the firewall is
your second line of defence.

---

## 4. Create a non-root user

```bash
ssh root@YOUR_SERVER_IP

adduser --disabled-password --gecos "" deploy
usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys
```

Disable root login and password auth:

```bash
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh
```

**Open a second terminal and confirm `ssh deploy@YOUR_SERVER_IP` works before
closing this one.** If you lock yourself out you will need Hetzner's console.

---

## 5. Install Docker

As `deploy`:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-plugin git
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

Log out and back in so the group applies, then check:

```bash
docker info | head -5
```

---

## 6. Clone the repository

**Public repo:**

```bash
cd ~
git clone https://github.com/musfiqbavaria/Bavaria-Garments.git app
cd app
```

**Private repo — use a read-only deploy key**, not your personal credentials:

```bash
ssh-keygen -t ed25519 -C "hetzner-deploy" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

Paste that into GitHub → repo → **Settings → Deploy keys → Add deploy key**.
Leave *Allow write access* **unticked** — the server only ever reads. Then:

```bash
git clone git@github.com:musfiqbavaria/Bavaria-Garments.git app
cd app
```

The clone is roughly 90 MB, mostly the approved reference screenshots.

---

## 7. Write the production `.env`

```bash
cp .env.example .env
```

Generate a secret key:

```bash
docker run --rm python:3.14.3-slim python -c \
  "import secrets,string;print(''.join(secrets.choice(string.ascii_letters+string.digits+'!@#%^&*(-_=+)') for _ in range(64)))"
```

Now `nano .env` and set:

```ini
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<the 64-character value you just generated>
DJANGO_ALLOWED_HOSTS=erp.yourdomain.com,YOUR_SERVER_IP
DJANGO_CSRF_TRUSTED_ORIGINS=http://erp.yourdomain.com

# TLS comes in step 10. Leave 0 until then.
DJANGO_SECURE_SSL=0

DB_ENGINE=postgres
POSTGRES_DB=emerald_rozalia_project1
POSTGRES_USER=emerald
POSTGRES_PASSWORD=<a long random password>
POSTGRES_HOST=db
POSTGRES_PORT=5432

REDIS_URL=redis://redis:6379/0
TIME_ZONE=Asia/Dhaka
BASE_CURRENCY=EUR

# Migrations are an explicit deploy step on a server, never done by a starting
# container: three containers racing the same migration is how a schema ends up
# half-applied. scripts/deploy.sh runs them.
DJANGO_AUTO_MIGRATE=0
DJANGO_AUTO_SEED=0

TENANCY_STRICT=0

DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_EMAIL=it@yourdomain.com
DEFAULT_ADMIN_PASSWORD=<a strong password, at least 12 characters>
```

Then:

```bash
chmod 600 .env
```

**Two things will stop you if you get this wrong, on purpose:**

- With `DJANGO_DEBUG=0` and a placeholder secret key, the application **refuses to
  start** and says so. That is the guard working.
- Passwords must satisfy Django's validators: 12+ characters, not a common
  password, not all digits.

---

## 8. First deploy

```bash
bash ./scripts/deploy.sh
```

That builds the images, starts all six services, checks the models match the
committed migrations, migrates, seeds the registry and organisation tree,
collects static files, and runs Django's checks. Expect a few minutes the first
time.

Then seed once and turn it off — you do not want the seeder running on every
deploy:

```bash
docker compose exec -T web python manage.py seed_project1
```

---

## 9. Confirm it works on HTTP

```bash
curl -I http://YOUR_SERVER_IP/login/          # expect 200
docker compose ps                             # all six services up
```

Open `http://YOUR_SERVER_IP/` in a browser and sign in with the admin username
and password from `.env`. **Change that password immediately** in
`/admin/password_change/`.

If you get **502** with static files still loading, nginx cached the old address
of a recreated `web` container:

```bash
docker compose restart nginx
```

---

## 10. Put it on HTTPS

Do not skip this. Session and CSRF cookies carry authentication for a system
holding payroll and banking data; on plain HTTP they travel in clear text.

### 10a. Point DNS at the server

Create an **A record**: `erp.yourdomain.com` → `YOUR_SERVER_IP`. Wait for it to
resolve:

```bash
dig +short erp.yourdomain.com
```

### 10b. Get a certificate

```bash
sudo apt install -y certbot
cd ~/app
docker compose stop nginx                     # frees port 80 for validation
sudo certbot certonly --standalone -d erp.yourdomain.com --agree-tos -m it@yourdomain.com -n
```

Copy the certificate where the container can read it:

```bash
mkdir -p nginx/certs
sudo cp /etc/letsencrypt/live/erp.yourdomain.com/fullchain.pem nginx/certs/
sudo cp /etc/letsencrypt/live/erp.yourdomain.com/privkey.pem  nginx/certs/
sudo chown -R $USER:$USER nginx/certs && chmod 600 nginx/certs/privkey.pem
```

### 10c. Turn on the HTTPS server block

Edit `nginx/default.conf`:

1. Uncomment **both** commented `server` blocks at the foot of the file.
2. Replace every `erp.example.com` with `erp.yourdomain.com`.
3. Remove `listen 80;` from the original server block at the top — the new
   redirect server takes port 80.

Edit `docker-compose.yml`, in the `nginx` service:

```yaml
    ports: ["80:80", "443:443"]
    volumes:
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      - static_data:/app/staticfiles:ro
      - ./nginx/certs:/etc/nginx/certs:ro
```

### 10d. Tell Django it is behind TLS

In `.env`:

```ini
DJANGO_SECURE_SSL=1
DJANGO_CSRF_TRUSTED_ORIGINS=https://erp.yourdomain.com
DJANGO_ALLOWED_HOSTS=erp.yourdomain.com
```

**Only now.** Setting `DJANGO_SECURE_SSL=1` before a TLS listener exists makes
Django redirect every request to `https://`, and with nothing on 443 the site
becomes unreachable.

### 10e. Restart and verify

```bash
docker compose up -d
docker compose exec nginx nginx -t            # config must be valid
curl -I http://erp.yourdomain.com/login/      # expect 301 to https
curl -I https://erp.yourdomain.com/login/     # expect 200
```

### 10f. Automate renewal

```bash
sudo crontab -e
```

```cron
0 3 * * 1 certbot renew --pre-hook "cd /home/deploy/app && docker compose stop nginx" --post-hook "cp /etc/letsencrypt/live/erp.yourdomain.com/*.pem /home/deploy/app/nginx/certs/ && chown -R deploy:deploy /home/deploy/app/nginx/certs && cd /home/deploy/app && docker compose up -d nginx"
```

---

## 11. Before real users

Not optional. Each of these is a known gap that is deliberately left open until
you close it.

**Verify the deployment audit is clean:**

```bash
docker compose exec -T web python manage.py check --deploy
```

**Assign organisation sites, then enforce scoping.** Until `TENANCY_STRICT=1`,
a record with no site is visible to every site.

```bash
docker compose exec -T web python manage.py report_unscoped
```

Assign the sites it lists in `/admin/`, re-run until it comes back clean, then set
`TENANCY_STRICT=1` in `.env` and `docker compose up -d web`.

**Set up backups off this machine.** A backup on the server it protects is not a
backup.

```bash
crontab -e
```

```cron
30 1 * * * cd /home/deploy/app && BACKUP_DIR=/home/deploy/backups ./scripts/backup.sh >> /home/deploy/backup.log 2>&1
```

Then copy `/home/deploy/backups` to Hetzner Storage Box or S3 on a schedule, and
**test a restore once** before you rely on it.

**Create real users with real roles.** The seeded `admin` is a superuser and
bypasses every permission check. Give each person a `UserProfile` with the right
role and organisation site; keep `admin` for break-glass only.

**Set up email** if you want notifications to leave the box: switch
`EMAIL_BACKEND` to the SMTP backend and fill in the host and credentials.

---

## 12. Deploying a change

The routine, every time. Two machines: **your PC** for building and testing,
**the server** for running.

### On your PC

**1. Make the change, then test it locally.**

```bash
cd "g:/Projects/Emerald Rozalia/Emerald_Rozalia_Project1_Python_Server_Ready"
bash ./scripts/run_local.sh
docker compose exec web python manage.py test portal --settings=core.settings_test
```

The suite must pass before you push. It checks that every template compiles, no
route returns a server error, every page renders, and the authorisation,
file-access and payroll rules still hold.

**2. If you changed a model, generate the migration here** - never on the server.

```bash
docker compose exec web python manage.py makemigrations portal
```

Read the generated file before committing it. Look for `RemoveField`,
`DeleteModel` or `RenameField`: those destroy data, and Django guesses at renames.

**3. Commit and push.**

```bash
git add -A
git status                    # confirm nothing unexpected, and no .env
git commit -m "what changed and why"
git push origin main
```

### On the server

**4. Pull and deploy.**

```bash
ssh root@YOUR_SERVER_IP
cd ~/app
git pull origin main
bash ./scripts/deploy.sh
docker compose restart nginx
```

`deploy.sh` rebuilds the images, verifies the code matches the committed
migrations, applies them, re-seeds the registry, collects static files and runs
Django's checks.

**5. Verify.**

```bash
curl -I http://localhost/login/       # expect 200
docker compose ps                     # all six up; db, redis, web healthy
docker compose logs --tail 30 web     # no tracebacks
```

Then load the page in a browser and **hard refresh** (`Ctrl` + `F5`).

---

### Why each step is there

**`git status` before committing.** `.env` is gitignored, but this catches a stray
file you did not mean to ship.

**Migrations generated on your PC, not the server.** `deploy.sh` runs
`makemigrations --check --dry-run` and **refuses to continue** if the models have
drifted from the committed migrations. That is deliberate: a migration invented on
the server is untracked, unreviewed, and will differ between environments.

**`docker compose restart nginx`.** Rebuilding recreates the `web` container with
a new address, and nginx caches the old one at startup. Skip this and you get
**502 Bad Gateway** while static files still load - the tell-tale sign.

**Hard refresh.** nginx serves static files with a seven-day cache. Without
`Ctrl` + `F5` you will swear your CSS change did nothing.

---

### Special cases

**You added a new setting to `.env.example`.** `.env` is not in git, so the server
does not get it. Add it by hand *before* deploying:

```bash
nano .env          # add the new line
docker compose up -d
```

Note `up -d`, not `restart` - env files are read when a container is **created**.

**You changed `requirements.txt`.** `deploy.sh` already builds with `--build`, so
it is handled. If a dependency still looks stale:

```bash
docker compose build --no-cache web && docker compose up -d web
```

**You only changed a template or CSS.** Still needs a rebuild: the source is baked
into the image, not mounted from disk. `deploy.sh` handles it.

**Nothing seems to have changed.** Work through the ordered checks in the "I
changed the code but the site looks the same" section of `RUNNING_LOCALLY.md` -
the same procedure applies on the server.

---

### If a deploy breaks the site

Roll the code back and redeploy:

```bash
cd ~/app
git log --oneline -5              # find the last good commit
git reset --hard <that-commit>
bash ./scripts/deploy.sh
docker compose restart nginx
```

**A migration that has already applied is NOT undone by this.** The code goes
back; the database does not. If the bad deploy changed the schema, restore from
backup instead:

```bash
gunzip -c backups/db_<STAMP>.sql.gz | docker compose exec -T db psql -U emerald -d emerald_rozalia_project1
```

Which is why step 11 says to test a restore *before* you need one.

---

### Quick sanity check any time

```bash
cd ~/app && git log --oneline -1 && docker compose ps
```

Tells you which commit is live and whether everything is running.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ImproperlyConfigured: DJANGO_SECRET_KEY is still the development value` | The guard working. Generate a real key (step 7). |
| `502` but static files load | nginx cached a stale `web` address. `docker compose restart nginx`. |
| `DisallowedHost` in the logs | The hostname you browsed is not in `DJANGO_ALLOWED_HOSTS`. |
| Every form fails a CSRF check | `DJANGO_CSRF_TRUSTED_ORIGINS` must contain the exact scheme and host you browse, e.g. `https://erp.yourdomain.com`. GET pages look fine while this is wrong. |
| Site unreachable right after enabling TLS | `DJANGO_SECURE_SSL=1` with nothing on 443. Set it back to 0, finish step 10, then re-enable. |
| `models have changes with no matching migration` | Generate it locally, commit, push, pull, redeploy. |
| Build killed / out of memory | Server too small. CX22 or larger. |
| A container keeps restarting | `docker compose logs --tail 40 <service>` |

Useful:

```bash
docker compose logs -f web
docker compose exec web bash
docker compose exec db psql -U emerald -d emerald_rozalia_project1
```

---

## What is deployed

Current status and known gaps: **`TECHNICAL_ASSESSMENT.md`**.

Two things worth knowing before you show this to anyone:

- **Sewing (#42) and Print (#39) do not exist.** They are visible in the
  navigation on purpose, as outstanding work. A full Sewing module with
  SMV/SAM/efficiency/cost reporting exists on the `backup/sewing-module` branch
  and is not part of `main`.
- **Account Master is a static page.** No accounting models exist behind it.
