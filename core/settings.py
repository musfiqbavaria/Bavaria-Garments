from pathlib import Path
import os
from celery.schedules import crontab
from dotenv import load_dotenv
BASE_DIR=Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR/'.env')
SECRET_KEY=os.getenv('DJANGO_SECRET_KEY','unsafe-dev')
DEBUG=os.getenv('DJANGO_DEBUG','0')=='1'
ALLOWED_HOSTS=[x.strip() for x in os.getenv('DJANGO_ALLOWED_HOSTS','localhost').split(',') if x.strip()]
CSRF_TRUSTED_ORIGINS=[x.strip() for x in os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS','').split(',') if x.strip()]
INSTALLED_APPS=['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','portal']
MIDDLEWARE=['django.middleware.security.SecurityMiddleware','whitenoise.middleware.WhiteNoiseMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware','portal.middleware.TenancyMiddleware','portal.authorization.AuthorizationMiddleware','portal.middleware.AuditMiddleware']
ROOT_URLCONF='core.urls'
TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages','portal.context_processors.global_portal']}}]
WSGI_APPLICATION='core.wsgi.application'
ASGI_APPLICATION='core.asgi.application'
DATABASES={'default':{'ENGINE':'django.db.backends.postgresql','NAME':os.getenv('POSTGRES_DB'),'USER':os.getenv('POSTGRES_USER'),'PASSWORD':os.getenv('POSTGRES_PASSWORD'),'HOST':os.getenv('POSTGRES_HOST','db'),'PORT':os.getenv('POSTGRES_PORT','5432')}}

def _flag(name, default='0'):
    """Read a boolean environment flag."""
    return os.getenv(name, default).strip().lower() in {'1', 'true', 'yes', 'on'}


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
_PUBLISHED_DEV_SECRETS={'unsafe-dev','local-dev-change-before-production'}
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
SECURE_HSTS_SECONDS=int(os.getenv('DJANGO_HSTS_SECONDS','31536000')) if SECURE_SSL_ENABLED else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS=SECURE_SSL_ENABLED
SECURE_HSTS_PRELOAD=SECURE_SSL_ENABLED

# --- cookies and sessions ---------------------------------------------------
SESSION_COOKIE_HTTPONLY=True
CSRF_COOKIE_HTTPONLY=False          # the CSRF token must be readable by forms
SESSION_COOKIE_SAMESITE='Lax'
CSRF_COOKIE_SAMESITE='Lax'
SESSION_COOKIE_AGE=int(os.getenv('DJANGO_SESSION_AGE','43200'))   # 12 hours
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
DATA_UPLOAD_MAX_MEMORY_SIZE=int(os.getenv('DJANGO_MAX_UPLOAD_BYTES', str(52428800)))
FILE_UPLOAD_MAX_MEMORY_SIZE=5242880
DATA_UPLOAD_MAX_NUMBER_FIELDS=2000

# --- cache ------------------------------------------------------------------
# Backs the login throttle. Redis is shared across gunicorn workers, so a
# per-process LocMemCache would let the throttle be bypassed by hitting a
# different worker. Falls back to LocMemCache when no Redis URL is configured.
_REDIS_URL=os.getenv('REDIS_URL','').strip()
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
LOGIN_ATTEMPT_LIMIT=int(os.getenv('DJANGO_LOGIN_ATTEMPT_LIMIT','10'))
LOGIN_ATTEMPT_WINDOW_SECONDS=int(os.getenv('DJANGO_LOGIN_ATTEMPT_WINDOW','900'))

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
 'root':{'handlers':['console'],'level':os.getenv('DJANGO_LOG_LEVEL','INFO')},
 'loggers':{
   'django.request':{'handlers':['console'],'level':'WARNING','propagate':False},
   # Refused access attempts. Worth shipping to a SIEM.
   'portal.authorization':{'handlers':['console'],'level':'INFO','propagate':False},
   'portal':{'handlers':['console'],'level':os.getenv('DJANGO_LOG_LEVEL','INFO'),'propagate':False},
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
TIME_ZONE=os.getenv('TIME_ZONE','Asia/Dhaka')
USE_I18N=True; USE_TZ=True
STATIC_URL='/static/'; STATIC_ROOT=BASE_DIR/'staticfiles'; STATICFILES_DIRS=[BASE_DIR/'static']
MEDIA_URL='/media/'; MEDIA_ROOT=BASE_DIR/'media'
DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'
LOGIN_URL='/login/'; LOGIN_REDIRECT_URL='/dashboard/'; LOGOUT_REDIRECT_URL='/login/'
CELERY_BROKER_URL=os.getenv('REDIS_URL','redis://redis:6379/0'); CELERY_RESULT_BACKEND=CELERY_BROKER_URL
# Celery reads only CELERY_* keys, so Django's TIME_ZONE does not reach it.
# Unset, beat defaulted to UTC and the three "Bangladesh" slots actually fired at
# 08:00/13:00/20:00 UTC - 14:00/19:00/02:00 Dhaka, the last on the wrong date.
CELERY_TIMEZONE=TIME_ZONE
CELERY_ENABLE_UTC=True
CELERY_BEAT_SCHEDULE={
 'report-0800':{'task':'portal.tasks.scheduled_report_snapshot','schedule':crontab(hour=8,minute=0)},
 'report-1300':{'task':'portal.tasks.scheduled_report_snapshot','schedule':crontab(hour=13,minute=0)},
 'report-2000':{'task':'portal.tasks.scheduled_report_snapshot','schedule':crontab(hour=20,minute=0)},
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
BASE_CURRENCY=os.getenv('BASE_CURRENCY','EUR')

# Daily rate feed. Deliberately empty by default so no outbound request is made
# until it is configured: the endpoint is an external service and must be
# authorised before use. Any provider returning {"base": "EUR", "rates": {...}}
# works, e.g. https://api.frankfurter.app/latest (ECB rates, no key required).
# The fetched rates are public reference data; no company data is transmitted.
EXCHANGE_RATE_API_URL=os.getenv('EXCHANGE_RATE_API_URL','').strip()
EXCHANGE_RATE_API_KEY=os.getenv('EXCHANGE_RATE_API_KEY','').strip()
EXCHANGE_RATE_TIMEOUT_SECONDS=int(os.getenv('EXCHANGE_RATE_TIMEOUT','10'))
# Refuse to convert with a rate older than this, rather than silently reporting
# stale figures as current.
EXCHANGE_RATE_MAX_AGE_DAYS=int(os.getenv('EXCHANGE_RATE_MAX_AGE_DAYS','7'))

# --- organisation scoping ---------------------------------------------------
# Off means records with no site assigned stay visible to every scope, which is
# required while the backfill is outstanding: every row that exists today has
# scope=None, so filtering them out would hide all existing data on day one.
# Turn this on once `manage.py report_unscoped` comes back clean.
TENANCY_STRICT=_flag('TENANCY_STRICT')

# --- audit retention --------------------------------------------------------
# AuditMiddleware writes a row per authenticated request, with no retention.
AUDIT_LOG_RETENTION_DAYS=int(os.getenv('AUDIT_LOG_RETENTION_DAYS','365'))
SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')
