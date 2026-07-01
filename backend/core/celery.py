"""
Celery application for the BI Dashboard backend.

This module creates the Celery app and configures it to auto-discover
tasks from all installed Django apps (specifically api/tasks.py).

Usage:
    # Start the worker (from backend/ directory):
    celery -A core worker --loglevel=info

    # On Windows (use solo pool since prefork is not supported):
    celery -A core worker --loglevel=info --pool=solo
"""

import os

from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

app = Celery('core')

# Read config from Django settings, using the CELERY_ namespace.
# e.g. CELERY_BROKER_URL in settings.py becomes broker_url in Celery.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks.py in all installed apps (api/tasks.py, etc.)
app.autodiscover_tasks()
