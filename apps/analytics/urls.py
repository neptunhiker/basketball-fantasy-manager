from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("analytics/", views.DreamTeamView.as_view(), name="dream-team"),
]
