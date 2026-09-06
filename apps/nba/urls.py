from django.urls import path

from . import views

app_name = "nba"

urlpatterns = [
    path("teams/", views.TeamListView.as_view(), name="team-list"),
    path("spieler/", views.PlayerListView.as_view(), name="player-list"),
]
