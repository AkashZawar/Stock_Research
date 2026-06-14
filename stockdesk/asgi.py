"""ASGI entry point for asynchronous servers (uvicorn, daphne, etc.)."""
import os

from django.core.asgi import get_asgi_application


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stockdesk.settings")

application = get_asgi_application()
