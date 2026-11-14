from django.urls import path

from . import views

app_name = "perf"

urlpatterns = [
    path("performance/", views.PerformanceView.as_view(), name="dashboard"),
]
