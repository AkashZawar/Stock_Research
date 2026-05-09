from django.urls import path

from market import views


urlpatterns = [
    path("", views.index, name="index"),
    path("api/search", views.search, name="api-search"),
    path("api/analyze", views.analyze, name="api-analyze"),
    path("api/search-logs", views.search_logs, name="api-search-logs"),
    path("api/market-monitor", views.market_monitor, name="api-market-monitor"),
    path("api/trade-references", views.trade_references, name="api-trade-references"),
    path("api/trade-references/<int:reference_id>", views.trade_reference_detail, name="api-trade-reference-detail"),
]
