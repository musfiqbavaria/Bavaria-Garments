from pathlib import Path
import os
from dotenv import load_dotenv
BASE_DIR=Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR/'.env')
SECRET_KEY=os.getenv('DJANGO_SECRET_KEY','unsafe-dev')
DEBUG=os.getenv('DJANGO_DEBUG','0')=='1'
ALLOWED_HOSTS=[x.strip() for x in os.getenv('DJANGO_ALLOWED_HOSTS','localhost').split(',') if x.strip()]
CSRF_TRUSTED_ORIGINS=[x.strip() for x in os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS','').split(',') if x.strip()]
INSTALLED_APPS=['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','portal']
MIDDLEWARE=['django.middleware.security.SecurityMiddleware','whitenoise.middleware.WhiteNoiseMiddleware','django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware','portal.middleware.AuditMiddleware']
ROOT_URLCONF='core.urls'
TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages','portal.context_processors.global_portal']}}]
WSGI_APPLICATION='core.wsgi.application'
ASGI_APPLICATION='core.asgi.application'
DATABASES={'default':{'ENGINE':'django.db.backends.postgresql','NAME':os.getenv('POSTGRES_DB'),'USER':os.getenv('POSTGRES_USER'),'PASSWORD':os.getenv('POSTGRES_PASSWORD'),'HOST':os.getenv('POSTGRES_HOST','db'),'PORT':os.getenv('POSTGRES_PORT','5432')}}
AUTH_PASSWORD_VALIDATORS=[]
LANGUAGE_CODE='en-gb'; TIME_ZONE=os.getenv('TIME_ZONE','Europe/Dublin'); USE_I18N=True; USE_TZ=True
STATIC_URL='/static/'; STATIC_ROOT=BASE_DIR/'staticfiles'; STATICFILES_DIRS=[BASE_DIR/'static']
MEDIA_URL='/media/'; MEDIA_ROOT=BASE_DIR/'media'
DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'
LOGIN_URL='/login/'; LOGIN_REDIRECT_URL='/dashboard/'; LOGOUT_REDIRECT_URL='/login/'
CELERY_BROKER_URL=os.getenv('REDIS_URL','redis://redis:6379/0'); CELERY_RESULT_BACKEND=CELERY_BROKER_URL
CELERY_BEAT_SCHEDULE={
 'report-0800':{'task':'portal.tasks.scheduled_report_snapshot','schedule':__import__('celery').schedules.crontab(hour=8,minute=0)},
 'report-1300':{'task':'portal.tasks.scheduled_report_snapshot','schedule':__import__('celery').schedules.crontab(hour=13,minute=0)},
 'report-2000':{'task':'portal.tasks.scheduled_report_snapshot','schedule':__import__('celery').schedules.crontab(hour=20,minute=0)},
}
SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')
