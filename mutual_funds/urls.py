"""URL routes for the Mutual Funds tab (``/api/mutual-funds/search``, ``/analyze``)."""
from django.urls import path

from . import views


app_name = "mutual_funds"

urlpatterns = [
    path("api/mutual-funds/search", views.search, name="api-search"),
    path("api/mutual-funds/analyze", views.analyze, name="api-analyze"),
]
