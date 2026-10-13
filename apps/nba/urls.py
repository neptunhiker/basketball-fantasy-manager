from django.urls import path

from . import season_stats_views, views

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
    path(
        "players/season-stats/import/",
        season_stats_views.SeasonStatsUploadView.as_view(),
        name="season-stats-upload",
    ),
    path(
        "players/season-stats/import/<uuid:pk>/",
        season_stats_views.SeasonStatsImportView.as_view(),
        name="season-stats-import",
    ),
    path(
        "players/season-stats/import/<uuid:pk>/confirm/",
        season_stats_views.SeasonStatsConfirmView.as_view(),
        name="season-stats-confirm",
    ),
    path(
        "players/season-stats/import/<uuid:pk>/discard/",
        season_stats_views.SeasonStatsDiscardView.as_view(),
        name="season-stats-discard",
    ),
    path("players/compare/", views.PlayerCompareView.as_view(), name="player-compare"),
    path(
        "players/compare/modal/",
        views.PlayerCompareModalView.as_view(),
        name="player-compare-modal",
    ),
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
