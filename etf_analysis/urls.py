from django.urls import path

from . import views


app_name = "etf_analysis"

urlpatterns = [
    path("api/etf/search", views.search, name="api-search"),
    path("api/etf/analyze", views.analyze, name="api-analyze"),
]
