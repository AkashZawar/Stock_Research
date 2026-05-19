from django.urls import path

from market import views


urlpatterns = [
    path("", views.index, name="index"),
    path("api/search", views.search, name="api-search"),
    path("api/search-assets", views.search_assets, name="api-search-assets"),
    path("api/analyze", views.analyze, name="api-analyze"),
    path("api/analyze-asset", views.analyze_asset, name="api-analyze-asset"),
    path("api/search-logs", views.search_logs, name="api-search-logs"),
    path("api/market-monitor", views.market_monitor, name="api-market-monitor"),
    path("api/watchlist", views.watchlist_items, name="api-watchlist"),
    path("api/watchlist/<int:item_id>", views.watchlist_item_detail, name="api-watchlist-detail"),
    path("api/trade-references", views.trade_references, name="api-trade-references"),
    path("api/trade-references/<int:reference_id>", views.trade_reference_detail, name="api-trade-reference-detail"),
]
