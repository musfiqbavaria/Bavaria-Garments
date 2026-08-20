from pathlib import Path
import os
from celery.schedules import crontab
from dotenv import load_dotenv
BASE_DIR=Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR/'.env')


def env(*names, default=''):
    """First non-empty value among ``names``.

    Several names are accepted per setting because two vocabularies are in use:
    the DJANGO_-prefixed names this project has always read, and the shorter
    unprefixed names (SECRET_KEY, DEBUG, ALLOWED_HOSTS, DB_*) that a hand-written
    .env is likely to use. The prefixed name wins where both are set. The
    canonical spelling for each setting is the one documented in .env.example.
    """
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != '':
            return value.strip()
    return default


def _flag(*names, default='0'):
    """Read a boolean flag. Accepts 1/true/yes/on in any case.

    Plain equality against '1' rejected DEBUG=True, which is the spelling most
    people write.
    """
    return env(*names, default=default).lower() in {'1', 'true', 'yes', 'on'}


def _csv(*names, default=''):
    return [item.strip() for item in env(*names, default=default).split(',') if item.strip()]


def _int(*names, default=0):
    try:
        return int(env(*names, default=str(default)))
    except ValueError:
        return default


SECRET_KEY=env('DJANGO_SECRET_KEY','SECRET_KEY',default='unsafe-dev')
DEBUG=_flag('DJANGO_DEBUG','DEBUG')
ALLOWED_HOSTS=_csv('DJANGO_ALLOWED_HOSTS','ALLOWED_HOSTS',default='localhost')
CSRF_TRUSTED_ORIGINS=_csv('DJANGO_CSRF_TRUSTED_ORIGINS','CSRF_TRUSTED_ORIGINS')
INSTALLED_APPS=['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','portal']
MIDDLEWARE=['django.middleware.security.SecurityMiddleware','whitenoise.middleware.WhiteNoiseMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware','portal.middleware.TenancyMiddleware','portal.authorization.AuthorizationMiddleware','portal.middleware.AuditMiddleware']
ROOT_URLCONF='core.urls'
TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages','portal.context_processors.global_portal','portal.navigation.navigation','django.template.context_processors.i18n']}}]
WSGI_APPLICATION='core.wsgi.application'
ASGI_APPLICATION='core.asgi.application'
# --- database ---------------------------------------------------------------
# Postgres is the deployment target and stays the default. SQLite is selectable
# for local work, because the project previously could not run at all without a
# Postgres instance and the psycopg driver - which made it awkward to try
# anything without Docker.
#
# SQLite is for development only: it has no concurrent-write story, and the
# scoping and reporting queries are written for Postgres.
DB_ENGINE=env('DJANGO_DB_ENGINE','DB_ENGINE',default='postgres').lower()
if DB_ENGINE in {'sqlite','sqlite3','django.db.backends.sqlite3'}:
    DATABASES={'default':{
        'ENGINE':'django.db.backends.sqlite3',
        'NAME':BASE_DIR/env('DB_NAME','POSTGRES_DB',default='db.sqlite3'),
    }}
else:
    DATABASES={'default':{
        'ENGINE':'django.db.backends.postgresql',
        'NAME':env('POSTGRES_DB','DB_NAME'),
        'USER':env('POSTGRES_USER','DB_USER'),
        'PASSWORD':env('POSTGRES_PASSWORD','DB_PASSWORD'),
        'HOST':env('POSTGRES_HOST','DB_HOST',default='db'),
        'PORT':env('POSTGRES_PORT','DB_PORT',default='5432'),
        # Reuse connections between requests instead of reconnecting each time.
        'CONN_MAX_AGE':_int('DJANGO_CONN_MAX_AGE',default=60),
    }}

# --- passwords --------------------------------------------------------------
# Validation was disabled entirely, so any user could set "1" as a password on a
# system holding payroll and banking data.
AUTH_PASSWORD_VALIDATORS=[
 {'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
 {'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator','OPTIONS':{'min_length':12}},
 {'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},
 {'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# --- refuse to run in production with the published development secret -------
# .env shipped inside the delivered package with this exact key, so it must be
# treated as public. Failing loudly here is the only reliable way to stop a
# deploy that would otherwise silently sign sessions with a known secret.
_PUBLISHED_DEV_SECRETS={'unsafe-dev','local-dev-change-before-production',
                       'CHANGE_THIS_TO_A_LONG_RANDOM_SECRET','changeme','secret'}
if not DEBUG and SECRET_KEY in _PUBLISHED_DEV_SECRETS:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY is still the development value that shipped with the '
        'package and is therefore public. Generate a new one before deploying:\n'
        '  python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"'
    )

# --- transport security -----------------------------------------------------
# Gated on DJANGO_SECURE_SSL so turning these on cannot lock anyone out of a
# deployment that has no TLS terminator yet. Set DJANGO_SECURE_SSL=1 once the
# nginx TLS server block in nginx/default.conf is enabled.
SECURE_SSL_ENABLED=_flag('DJANGO_SECURE_SSL')
SECURE_SSL_REDIRECT=SECURE_SSL_ENABLED
SESSION_COOKIE_SECURE=SECURE_SSL_ENABLED
CSRF_COOKIE_SECURE=SECURE_SSL_ENABLED
SECURE_HSTS_SECONDS=_int('DJANGO_HSTS_SECONDS',default=31536000) if SECURE_SSL_ENABLED else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS=SECURE_SSL_ENABLED
SECURE_HSTS_PRELOAD=SECURE_SSL_ENABLED

# --- cookies and sessions ---------------------------------------------------
SESSION_COOKIE_HTTPONLY=True
CSRF_COOKIE_HTTPONLY=False          # the CSRF token must be readable by forms
SESSION_COOKIE_SAMESITE='Lax'
CSRF_COOKIE_SAMESITE='Lax'
SESSION_COOKIE_AGE=_int('DJANGO_SESSION_AGE',default=43200)   # 12 hours
SESSION_EXPIRE_AT_BROWSER_CLOSE=True
SESSION_SAVE_EVERY_REQUEST=True     # sliding expiry on an operational console

# --- browser-side headers ---------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF=True
SECURE_REFERRER_POLICY='same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY='same-origin'
X_FRAME_OPTIONS='DENY'

# --- upload limits ----------------------------------------------------------
# nginx caps the body at 50M; keep Django's own limits in step so a large upload
# fails cleanly instead of exhausting worker memory.
DATA_UPLOAD_MAX_MEMORY_SIZE=_int('DJANGO_MAX_UPLOAD_BYTES',default=52428800)
FILE_UPLOAD_MAX_MEMORY_SIZE=5242880
DATA_UPLOAD_MAX_NUMBER_FIELDS=2000

# --- cache ------------------------------------------------------------------
# Backs the login throttle. Redis is shared across gunicorn workers, so a
# per-process LocMemCache would let the throttle be bypassed by hitting a
# different worker. Falls back to LocMemCache when no Redis URL is configured.
_REDIS_URL=env('REDIS_URL')
if _REDIS_URL:
    # Short socket timeouts matter: without them a Redis outage makes every
    # login hang on the throttle lookup instead of failing open immediately.
    CACHES={'default':{
        'BACKEND':'django.core.cache.backends.redis.RedisCache',
        'LOCATION':_REDIS_URL,
        'OPTIONS':{'socket_connect_timeout':1,'socket_timeout':1,'retry_on_timeout':False},
    }}
else:
    CACHES={'default':{'BACKEND':'django.core.cache.backends.locmem.LocMemCache','LOCATION':'project1'}}

# --- login throttle ---------------------------------------------------------
# There was no rate limiting on /login/ or any API endpoint.
LOGIN_ATTEMPT_LIMIT=_int('DJANGO_LOGIN_ATTEMPT_LIMIT',default=10)
LOGIN_ATTEMPT_WINDOW_SECONDS=_int('DJANGO_LOGIN_ATTEMPT_WINDOW',default=900)

# --- public recruitment throttle --------------------------------------------
# /careers/ is the only unauthenticated endpoint that writes rows and stores
# uploaded files, so it is the one place where an anonymous caller can consume
# disk and flood the HR approval queue. Every POST is counted, valid or not, so
# a loop of rejected submissions is bounded too.
CAREERS_SUBMISSION_LIMIT=_int('DJANGO_CAREERS_SUBMISSION_LIMIT',default=5)
CAREERS_SUBMISSION_WINDOW_SECONDS=_int('DJANGO_CAREERS_SUBMISSION_WINDOW',default=3600)

# --- logging ----------------------------------------------------------------
# LOGGING was not configured at all, so no failure anywhere in the platform was
# recorded server-side - including the dashboards' blanket `except Exception`,
# which showed the operator a message and discarded the error.
LOGGING={
 'version':1,
 'disable_existing_loggers':False,
 'formatters':{
   'standard':{'format':'{asctime} {levelname} {name} {message}','style':'{'},
 },
 'handlers':{
   'console':{'class':'logging.StreamHandler','formatter':'standard'},
 },
 'root':{'handlers':['console'],'level':env('DJANGO_LOG_LEVEL',default='INFO')},
 'loggers':{
   'django.request':{'handlers':['console'],'level':'WARNING','propagate':False},
   # Refused access attempts. Worth shipping to a SIEM.
   'portal.authorization':{'handlers':['console'],'level':'INFO','propagate':False},
   'portal':{'handlers':['console'],'level':env('DJANGO_LOG_LEVEL',default='INFO'),'propagate':False},
 },
}
LANGUAGE_CODE='en-gb'
# Asia/Dhaka is the operating clock, signed off for Phase 3.
#
# The attendance engine anchors 08:00 check-in, the 13:00-14:00 break, 17:00 and
# 20:00 checkouts against this timezone, and every report slot is documented as
# Bangladesh-local. It was Europe/Dublin, so a Dhaka operator clocking in at
# 08:00 local was measured against an Irish 08:00 anchor - four to five hours
# out - which corrupted late, early-leave, worked and unpaid minutes and the
# payroll cost derived from them. Dublin also observes DST while Dhaka does not,
# so the error moved twice a year.
#
# Per-site timezones for genuine multi-country operation need OrganizationNode
# to carry its own zone and the report models to be scoped to a factory; see
# TECHNICAL_ASSESSMENT.md 5.5.
TIME_ZONE=env('TIME_ZONE',default='Asia/Dhaka')
USE_I18N=True; USE_TZ=True
STATIC_URL='/static/'; STATIC_ROOT=BASE_DIR/'staticfiles'; STATICFILES_DIRS=[BASE_DIR/'static']
MEDIA_URL='/media/'; MEDIA_ROOT=BASE_DIR/'media'
DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'
LOGIN_URL='/login/'; LOGIN_REDIRECT_URL='/dashboard/'; LOGOUT_REDIRECT_URL='/login/'
CELERY_BROKER_URL=env('REDIS_URL',default='redis://redis:6379/0'); CELERY_RESULT_BACKEND=CELERY_BROKER_URL
# Celery reads only CELERY_* keys, so Django's TIME_ZONE does not reach it.
# Unset, beat defaulted to UTC and the three "Bangladesh" slots actually fired at
# 08:00/13:00/20:00 UTC - 14:00/19:00/02:00 Dhaka, the last on the wrong date.
CELERY_TIMEZONE=TIME_ZONE
CELERY_ENABLE_UTC=True
CELERY_BEAT_SCHEDULE={
 'report-0800':{'task':'portal.tasks.scheduled_report_snapshot','schedule':crontab(hour=8,minute=0)},
 'report-1300':{'task':'portal.tasks.scheduled_report_snapshot','schedule':crontab(hour=13,minute=0)},
 'report-2000':{'task':'portal.tasks.scheduled_report_snapshot','schedule':crontab(hour=20,minute=0)},
 # The 16 department *AutoReport tables every dashboard reads. Nothing wrote
 # them: ten management commands existed but were in no task and no beat entry,
 # seven departments had no command at all, and the deploy/*.cron.example files
 # invoked a venv path that does not exist on a Docker host. Those panels were
 # permanently empty while a healthy beat container made it look scheduled.
 # See SITE_AUDIT_FINDINGS.md A8 and portal/reporting.py.
 'department-reports-0800':{'task':'portal.tasks.generate_department_reports','schedule':crontab(hour=8,minute=5),'args':('08:00',)},
 'department-reports-1300':{'task':'portal.tasks.generate_department_reports','schedule':crontab(hour=13,minute=5),'args':('13:00',)},
 'department-reports-2000':{'task':'portal.tasks.generate_department_reports','schedule':crontab(hour=20,minute=5),'args':('20:00',)},
 # Daily exchange rates, before the 08:00 reporting slot so consolidated
 # figures use the current day's rate. No-op unless EXCHANGE_RATE_API_URL is set.
 'exchange-rates-daily':{'task':'portal.tasks.refresh_exchange_rates','schedule':crontab(hour=6,minute=30)},
 # AuditMiddleware writes a row per authenticated request; trim it weekly.
 'purge-audit-logs-weekly':{'task':'portal.tasks.purge_expired_audit_logs','schedule':crontab(hour=3,minute=15,day_of_week=5)},
}

# --- currency ---------------------------------------------------------------
# Consolidated reporting currency, signed off for Phase 3. Rozalia Limited is the
# Irish parent, so EUR is the base; BDT production costs and USD buyer values
# convert into it for any figure that spans entities.
BASE_CURRENCY=env('BASE_CURRENCY','DEFAULT_CURRENCY',default='EUR')

# --- default country --------------------------------------------------------
# Which Country node the command centre opens on for a user whose organisation
# scope does not resolve to one. The landing page used to hardcode a
# name__icontains='Bangladesh' lookup and fall back to every node in the estate
# when it found nothing. Blank means "the first active Country node".
# See SITE_AUDIT_FINDINGS.md B19.
DEFAULT_COUNTRY=env('DEFAULT_COUNTRY',default='Bangladesh')

# --- public contact details -------------------------------------------------
# The careers page footer hardcoded a phone number and an email address in the
# template. Both are configuration: a change of recruiter or a second country
# should not mean editing markup. See SITE_AUDIT_FINDINGS.md B30.
CAREERS_CONTACT_PHONE=env('CAREERS_CONTACT_PHONE',default='089 978 8187')
CAREERS_CONTACT_EMAIL=env('CAREERS_CONTACT_EMAIL',default='urmos@rozalia.ie')

# Daily rate feed. Deliberately empty by default so no outbound request is made
# until it is configured: the endpoint is an external service and must be
# authorised before use. Any provider returning {"base": "EUR", "rates": {...}}
# works, e.g. https://api.frankfurter.app/latest (ECB rates, no key required).
# The fetched rates are public reference data; no company data is transmitted.
EXCHANGE_RATE_API_URL=env('EXCHANGE_RATE_API_URL')
EXCHANGE_RATE_API_KEY=env('EXCHANGE_RATE_API_KEY')
EXCHANGE_RATE_TIMEOUT_SECONDS=_int('EXCHANGE_RATE_TIMEOUT',default=10)
# Refuse to convert with a rate older than this, rather than silently reporting
# stale figures as current.
EXCHANGE_RATE_MAX_AGE_DAYS=_int('EXCHANGE_RATE_MAX_AGE_DAYS',default=7)

# --- organisation scoping ---------------------------------------------------
# Off means records with no site assigned stay visible to every scope, which is
# required while the backfill is outstanding: every row that exists today has
# scope=None, so filtering them out would hide all existing data on day one.
# Turn this on once `manage.py report_unscoped` comes back clean.
TENANCY_STRICT=_flag('TENANCY_STRICT')

# --- email ------------------------------------------------------------------
# Nothing in the project configured email at all, so the Communication Center's
# EMAIL channel and every notification had no transport. The console backend is
# the default so a misconfigured server prints instead of silently failing, and
# so development never mails a real buyer or member of staff by accident.
EMAIL_BACKEND=env('EMAIL_BACKEND',default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST=env('EMAIL_HOST')
EMAIL_PORT=_int('EMAIL_PORT',default=587)
EMAIL_HOST_USER=env('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD=env('EMAIL_HOST_PASSWORD')
EMAIL_USE_TLS=_flag('EMAIL_USE_TLS',default='1')
EMAIL_USE_SSL=_flag('EMAIL_USE_SSL')
EMAIL_TIMEOUT=_int('EMAIL_TIMEOUT',default=15)
DEFAULT_FROM_EMAIL=env('DEFAULT_FROM_EMAIL',default='no-reply@emeraldrozalia.com')
SERVER_EMAIL=env('SERVER_EMAIL',default=DEFAULT_FROM_EMAIL)

# --- external integrations --------------------------------------------------
# Credentials are read here so they live in the environment rather than in
# source or in a CommunicationConnector row. NOTE: no code sends through these
# yet - the WhatsApp and email channels are represented by connector-backed
# message queues, and an actual delivery implementation is still outstanding.
# Declaring them does not enable them.
#
# Company policy requires written authorisation before any external service is
# used, and before any company data is sent to one. That applies to every value
# in this block. The OpenAI key in particular would send whatever is passed to it
# outside the estate, so treat wiring it up as a decision that needs sign-off,
# not a configuration change.
WHATSAPP_ACCESS_TOKEN=env('WHATSAPP_ACCESS_TOKEN')
WHATSAPP_PHONE_NUMBER_ID=env('WHATSAPP_PHONE_NUMBER_ID')
WHATSAPP_BUSINESS_ACCOUNT_ID=env('WHATSAPP_BUSINESS_ACCOUNT_ID')
WHATSAPP_API_VERSION=env('WHATSAPP_API_VERSION',default='v21.0')
OPENAI_API_KEY=env('OPENAI_API_KEY')

# --- first-run administrator ------------------------------------------------
# Consumed by manage.py seed_project1.
DEFAULT_ADMIN_USERNAME=env('DEFAULT_ADMIN_USERNAME',default='admin')
DEFAULT_ADMIN_EMAIL=env('DEFAULT_ADMIN_EMAIL',default='admin@example.com')
DEFAULT_ADMIN_PASSWORD=env('DEFAULT_ADMIN_PASSWORD',default='')

# --- business rules ---------------------------------------------------------
BANGLADESH_OVERSEAS_INCENTIVE_RATE=env('BANGLADESH_OVERSEAS_INCENTIVE_RATE',default='2.5')

# --- audit retention --------------------------------------------------------
# AuditMiddleware writes a row per authenticated request, with no retention.
AUDIT_LOG_RETENTION_DAYS=_int('AUDIT_LOG_RETENTION_DAYS',default=365)
SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')
