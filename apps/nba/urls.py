from django.urls import path

from . import views

app_name = "nba"

urlpatterns = [
    path("teams/", views.TeamListView.as_view(), name="team-list"),
    path(
        "teams/<slug:abbreviation>/notes/<uuid:note_pk>/edit/",
        views.TeamNoteEditView.as_view(),
        name="team-note-edit",
    ),
    path(
        "teams/<slug:abbreviation>/notes/<uuid:note_pk>/delete/",
        views.TeamNoteDeleteView.as_view(),
        name="team-note-delete",
    ),
    path("teams/<slug:abbreviation>/", views.TeamDetailView.as_view(), name="team-detail"),
    path("players/injuries/refresh/", views.InjuryRefreshView.as_view(), name="injury-refresh"),
    path("players/import/", views.BbdeImportView.as_view(), name="bbde-import"),
    path("players/compare/", views.PlayerCompareView.as_view(), name="player-compare"),
    path("players/compare/modal/", views.PlayerCompareModalView.as_view(), name="player-compare-modal"),
    path("players/", views.PlayerListView.as_view(), name="player-list"),
    path("players/watchlist/", views.WatchlistView.as_view(), name="watchlist"),
    path(
        "players/<slug:slug>/watchlist/",
        views.WatchlistToggleView.as_view(),
        name="watchlist-toggle",
    ),
    path(
        "players/<slug:slug>/notes/<uuid:note_pk>/edit/",
        views.PlayerNoteEditView.as_view(),
        name="player-note-edit",
    ),
    path(
        "players/<slug:slug>/notes/<uuid:note_pk>/delete/",
        views.PlayerNoteDeleteView.as_view(),
        name="player-note-delete",
    ),
    path("players/<slug:slug>/", views.PlayerDetailView.as_view(), name="player-detail"),
]
