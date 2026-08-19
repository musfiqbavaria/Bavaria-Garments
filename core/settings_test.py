"""Settings for running the test suite.

    python manage.py test portal --settings=core.settings_test

Overrides only what makes tests slow or environment-dependent. Everything else,
including the authorisation middleware and the security settings, is inherited
so the suite exercises the real configuration.
"""
from .settings import *  # noqa: F401,F403

# The security suite creates a user per scenario, and PBKDF2 at production
# iteration counts dominated the runtime (roughly four seconds per test).
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

# REDIS_URL points at the compose service hostname, which does not resolve
# outside the stack. The login throttle fails open on a cache error, so tests
# would still pass, but every attempt would wait on a connection timeout first.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'project1-tests',
    }
}

# Never run the suite with DEBUG on: it changes error handling and would hide
# the difference between a handled 400 and an unhandled 500.
DEBUG = False

# Keep test output readable; the suite asserts on behaviour, not log lines.
LOGGING['root']['level'] = 'ERROR'                      # noqa: F405
for _logger in LOGGING['loggers'].values():             # noqa: F405
    _logger['level'] = 'ERROR'
