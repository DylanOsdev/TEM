import os
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'True')

from .settings import *

# Env-driven DB selection: use MySQL when explicitly requested via
# DB_ENGINE=mysql or USE_MYSQL=1 (or DB_HOST defined with engine mysql),
# otherwise fall back to fast SQLite in-memory.
_DB_ENGINE = os.environ.get("DB_ENGINE", "").lower()
_USE_MYSQL = os.environ.get("USE_MYSQL", "") == "1"
_DB_HOST = os.environ.get("DB_HOST")

if _USE_MYSQL or _DB_ENGINE == "mysql" or (_DB_HOST is not None and _DB_ENGINE == "mysql"):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': os.environ.get('DB_NAME', 'tem_dbv2'),
            'USER': os.environ.get('DB_USER', 'root'),
            'PASSWORD': os.environ.get('DB_PASSWORD', 'root'),
            'HOST': os.environ.get('DB_HOST', '127.0.0.1'),
            'PORT': os.environ.get('DB_PORT', '3306'),
        }
    }
else:
    # Use SQLite in-memory for fast, isolated tests (default for test-sqlite)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
            'TEST': {
                'NAME': ':memory:',
            },
        }
    }

# Disable password validators during tests (speed)
AUTH_PASSWORD_VALIDATORS = []

# Use fast password hasher
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

# Disable CSRF for API-like test client calls
TESTING = True

# Disable email sending during tests
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

# Use console cache for tests
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}


# Migration 0005 uses SeparateDatabaseAndState with empty database_operations
# (column renamed manually in MySQL). SQLite needs the rename applied.
# We monkey-patch the migration class at import time.
import importlib
from django.db.migrations.state import ProjectState
from django.db.migrations.executor import MigrationExecutor

_original_apply = MigrationExecutor._migrate_all_forwards


def _patched_migrate_all_forwards(executor, *args, **kwargs):
    """After all migrations, rename cedula→identificacion if on SQLite."""
    result = _original_apply(executor, *args, **kwargs)
    from django.db import connection
    if connection.vendor == 'sqlite':
        with connection.cursor() as cursor:
            try:
                cursor.execute(
                    "ALTER TABLE usuarios RENAME COLUMN cedula TO identificacion"
                )
            except Exception:
                pass
    return result


MigrationExecutor._migrate_all_forwards = _patched_migrate_all_forwards
