from django.urls import include, path


urlpatterns = [
    path("", include("core.urls")),
    path("", include("stock_analysis.urls")),
    path("", include("recommendations.urls")),
    path("", include("market_monitor.urls")),
    path("", include("etf_analysis.urls")),
    path("", include("mutual_funds.urls")),
]
