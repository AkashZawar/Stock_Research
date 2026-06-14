"""URL route for the Recommendations tab (``/api/recommendations``)."""
from django.urls import path

from . import views


app_name = "recommendations"

urlpatterns = [
    path("api/recommendations", views.recommendations, name="api-recommendations"),
]
