from django.urls import path

from . import views


app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("app", views.index, name="index"),
    path("api/search-logs", views.search_logs, name="api-search-logs"),
    path("api/watchlist", views.watchlist_items, name="api-watchlist"),
    path("api/watchlist/<int:item_id>", views.watchlist_item_detail, name="api-watchlist-detail"),
    path("api/trade-references", views.trade_references, name="api-trade-references"),
    path("api/trade-references/<int:reference_id>", views.trade_reference_detail, name="api-trade-reference-detail"),
]
