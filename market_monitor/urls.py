"""URL route for the Market Monitor tab (``/api/market-monitor``)."""
from django.urls import path

from . import views


app_name = "market_monitor"

urlpatterns = [
    path("api/market-monitor", views.market_monitor, name="api-market-monitor"),
]
