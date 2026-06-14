from django.urls import path

from . import views


app_name = "stock_analysis"

urlpatterns = [
    path("api/search", views.search, name="api-search"),
    path("api/analyze", views.analyze, name="api-analyze"),
]
