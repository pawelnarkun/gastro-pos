import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import core.routing  # Importujemy Twój plik routing.py

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'moj_lokal.settings')

# Inicjalizujemy aplikację Django HTTP
django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter({
    # Obsługa standardowych zapytań HTTP (widoki, admin itp.)
    "http": django_asgi_app,

    # Obsługa WebSocketów
    "websocket": AuthMiddlewareStack(
        URLRouter(
            core.routing.websocket_urlpatterns
        )
    ),
})