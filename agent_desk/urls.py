"""URL route for the Agent Desk tab (``/api/agent-desk/analyze``)."""
from django.urls import path

from . import views


app_name = "agent_desk"

urlpatterns = [
    path("api/agent-desk/analyze", views.analyze, name="api-agent-desk-analyze"),
]
