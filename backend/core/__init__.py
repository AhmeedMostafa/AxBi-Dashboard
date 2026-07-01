# Import the Celery app so that it is always loaded when
# Django starts, and @shared_task decorators use this app.
from .celery import app as celery_app

__all__ = ('celery_app',)
