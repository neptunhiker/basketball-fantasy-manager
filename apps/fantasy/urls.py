from django.urls import path

from . import views

app_name = "fantasy"

urlpatterns = [
    path("seasons/", views.SeasonListView.as_view(), name="season-list"),
    path("seasons/new/", views.SeasonCreateView.as_view(), name="season-create"),
    path("seasons/<uuid:pk>/edit/", views.SeasonUpdateView.as_view(), name="season-update"),
    path("seasons/<uuid:pk>/delete/", views.SeasonDeleteView.as_view(), name="season-delete"),
    path("managers/", views.ManagerListView.as_view(), name="manager-list"),
    path("managers/new/", views.ManagerCreateView.as_view(), name="manager-create"),
    path("managers/<uuid:pk>/rename/", views.ManagerRenameView.as_view(), name="manager-rename"),
    path("managers/<uuid:pk>/delete/", views.ManagerDeleteView.as_view(), name="manager-delete"),
    path("rosters/", views.RosterListView.as_view(), name="roster-list"),
    path("rosters/new/", views.RosterCreateView.as_view(), name="roster-create"),
    path("rosters/<uuid:pk>/rules/", views.RosterRulesView.as_view(), name="roster-rules"),
    path("rosters/<uuid:pk>/build/", views.RosterBuildView.as_view(), name="roster-build"),
    path("rosters/<uuid:pk>/rename/", views.RosterRenameView.as_view(), name="roster-rename"),
    path("rosters/<uuid:pk>/delete/", views.RosterDeleteView.as_view(), name="roster-delete"),
    path(
        "rosters/<uuid:pk>/buy/<uuid:player_pk>/",
        views.RosterBuyView.as_view(),
        name="roster-buy",
    ),
    path(
        "rosters/<uuid:pk>/sell/<uuid:player_pk>/",
        views.RosterSellView.as_view(),
        name="roster-sell",
    ),
    path(
        "rosters/<uuid:pk>/trades/buy/",
        views.RosterBuyTradeView.as_view(),
        name="roster-buy-trade",
    ),
    path(
        "rosters/<uuid:pk>/trades/sell/",
        views.RosterSellTradeView.as_view(),
        name="roster-sell-trade",
    ),
    path("rosters/<uuid:pk>/trade/", views.RosterTradeView.as_view(), name="roster-trade"),
    path("rosters/<uuid:pk>/history/", views.RosterHistoryView.as_view(), name="roster-history"),
]
