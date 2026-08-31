"""URL routes owned by the core app.

Maps the landing page (``/``) and the workspace shell (``/app``). Included at
the project root by ``stockdesk/urls.py``.

There are no CRUD routes here: the app stores nothing, so every other route
belongs to a tab app and reads live from its upstream.
"""
from django.urls import path

from . import views


app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("app", views.index, name="index"),
    path("app/", views.index, name="index-slash"),
]
