"""URL route for the IPO Radar tab (``/api/ipo``)."""
from django.urls import path

from . import views


app_name = "ipo"

urlpatterns = [
    path("api/ipo", views.ipo, name="api-ipo"),
]
