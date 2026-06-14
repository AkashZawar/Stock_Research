"""App config for the shared ``core`` app.

``core`` is the general/shared Django app. It holds the analysis engine
(``services.py``), the database models, request/form helpers, the base page
template, and the cross-cutting JSON APIs (watchlist, trade references,
search logs). Every per-tab app imports from here.
"""
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
