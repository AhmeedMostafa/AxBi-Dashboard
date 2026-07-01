"""
ASGI config for core project.

Routes HTTP traffic to the standard Django application and WebSocket traffic
to the Channels consumer stack (used for the Gemini Live voice proxy).

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

# get_asgi_application() must be called before importing anything that touches
# the app registry (e.g. consumers that import models/views).
from django.core.asgi import get_asgi_application

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from api.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': URLRouter(websocket_urlpatterns),
})
