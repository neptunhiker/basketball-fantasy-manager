from django.urls import path

from . import views

app_name = "nba"

urlpatterns = [
    path("teams/", views.TeamListView.as_view(), name="team-list"),
    path("teams/<slug:abbreviation>/", views.TeamDetailView.as_view(), name="team-detail"),
    path("players/", views.PlayerListView.as_view(), name="player-list"),
    path("players/<slug:slug>/", views.PlayerDetailView.as_view(), name="player-detail"),
]
