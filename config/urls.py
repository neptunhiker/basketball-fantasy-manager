from django.contrib import admin
from django.urls import include, path

from apps.core.views import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    path("konto/", include("apps.accounts.urls")),
    path("", include("apps.nba.urls")),
    path("", include("apps.core.urls")),
]

admin.site.site_header = "Crossover Manager"
admin.site.site_title = "Crossover Manager"
admin.site.index_title = "Verwaltung"
