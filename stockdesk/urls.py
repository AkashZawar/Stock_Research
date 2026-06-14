"""Root URL configuration.

Includes each app's ``urls.py`` at the project root, so the final paths come
from the apps themselves (e.g. ``/`` and ``/api/...``). Add a new tab by
creating an app and adding one ``include(...)`` line here.
"""
from django.urls import include, path


urlpatterns = [
    path("", include("core.urls")),
    path("", include("stock_analysis.urls")),
    path("", include("recommendations.urls")),
    path("", include("market_monitor.urls")),
    path("", include("etf_analysis.urls")),
    path("", include("mutual_funds.urls")),
]
