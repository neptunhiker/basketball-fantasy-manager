from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("reports/", views.ReportsView.as_view(), name="index"),
    path("reports/<uuid:run_id>/", views.ReportsView.as_view(), name="run"),
    # Staff only.
    path("reports/runs/new/", views.StartRunView.as_view(), name="start"),
    path("reports/runs/estimate/", views.EstimateView.as_view(), name="estimate"),
    path("reports/settings/", views.SettingsView.as_view(), name="settings"),
    path("reports/<uuid:run_id>/publish/", views.PublishView.as_view(), name="publish"),
    path("reports/<uuid:run_id>/retry/", views.RetryView.as_view(), name="retry"),
    path("reports/<uuid:run_id>/rerun/", views.RerunView.as_view(), name="rerun"),
    path("reports/<uuid:run_id>/progress/", views.RunProgressView.as_view(), name="progress"),
]
