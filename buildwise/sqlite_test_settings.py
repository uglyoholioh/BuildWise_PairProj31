"""File-backed SQLite settings for reproducible writer-contention tests."""
from .settings import *

DATABASES['default']['TEST'] = {'NAME': BASE_DIR / 'test_buildwise.sqlite3'}
