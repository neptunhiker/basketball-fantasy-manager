from django.urls import path

from . import plan_views, views

app_name = "fantasy"

urlpatterns = [
    path("seasons/", views.SeasonListView.as_view(), name="season-list"),
    path("seasons/new/", views.SeasonCreateView.as_view(), name="season-create"),
    path("seasons/<uuid:pk>/edit/", views.SeasonUpdateView.as_view(), name="season-update"),
    path("seasons/<uuid:pk>/delete/", views.SeasonDeleteView.as_view(), name="season-delete"),
    path(
        "seasons/<uuid:pk>/trade-grants/",
        views.SeasonTradeGrantsView.as_view(),
        name="season-trade-grants",
    ),
    path("managers/", views.ManagerListView.as_view(), name="manager-list"),
    path("managers/new/", views.ManagerCreateView.as_view(), name="manager-create"),
    path("managers/<uuid:pk>/", views.ManagerDetailView.as_view(), name="manager-detail"),
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
        "rosters/<uuid:pk>/sign-player/<uuid:player_pk>/",
        views.PlayerSignView.as_view(),
        name="roster-sign-player",
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
    path("rosters/<uuid:pk>/latest/", views.RosterLatestView.as_view(), name="roster-latest"),
    # --- trade plans ---
    path("rosters/<uuid:pk>/plans/", plan_views.PlanListView.as_view(), name="plan-list"),
    path("rosters/<uuid:pk>/plans/new/", plan_views.PlanCreateView.as_view(), name="plan-create"),
    path(
        "rosters/<uuid:pk>/plans/<uuid:plan_pk>/",
        plan_views.PlanDetailView.as_view(),
        name="plan-detail",
    ),
    path(
        "rosters/<uuid:pk>/plans/<uuid:plan_pk>/rename/",
        plan_views.PlanRenameView.as_view(),
        name="plan-rename",
    ),
    path(
        "rosters/<uuid:pk>/plans/<uuid:plan_pk>/duplicate/",
        plan_views.PlanDuplicateView.as_view(),
        name="plan-duplicate",
    ),
    path(
        "rosters/<uuid:pk>/plans/<uuid:plan_pk>/delete/",
        plan_views.PlanDeleteView.as_view(),
        name="plan-delete",
    ),
    path(
        "rosters/<uuid:pk>/plans/<uuid:plan_pk>/share/",
        plan_views.PlanShareView.as_view(),
        name="plan-share",
    ),
    path("plans/shared/<uuid:token>/", plan_views.SharedPlanView.as_view(), name="plan-shared"),
    path(
        "rosters/<uuid:pk>/plans/<uuid:plan_pk>/steps/new/",
        plan_views.PlanStepEditorView.as_view(),
        name="plan-step-new",
    ),
    path(
        "rosters/<uuid:pk>/plans/<uuid:plan_pk>/steps/<uuid:step_pk>/move/",
        plan_views.PlanStepMoveView.as_view(),
        name="plan-step-move",
    ),
    path(
        "rosters/<uuid:pk>/plans/<uuid:plan_pk>/steps/<uuid:step_pk>/delete/",
        plan_views.PlanStepDeleteView.as_view(),
        name="plan-step-delete",
    ),
]
