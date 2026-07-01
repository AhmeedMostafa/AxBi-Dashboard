"""WebSocket URL routing for Channels (Gemini Live voice proxy)."""

from django.urls import re_path

from . import live_consumer

websocket_urlpatterns = [
    re_path(r"^ws/live/?$", live_consumer.LiveProxyConsumer.as_asgi()),
]
