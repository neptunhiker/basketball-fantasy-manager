from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("reports/", views.ReportsView.as_view(), name="index"),
    path("reports/<uuid:run_id>/", views.ReportsView.as_view(), name="run"),
]
