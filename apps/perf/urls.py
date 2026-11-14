from django.urls import path

from . import views

app_name = "perf"

urlpatterns = [
    path("performance/", views.PerformanceView.as_view(), name="dashboard"),
    # Under /performance/ like the page, so PERF_IGNORED_PATHS covers it too.
    path("performance/requests/", views.RequestListView.as_view(), name="requests"),
]
