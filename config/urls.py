from django.contrib import admin
from django.urls import include, path

from apps.core.views import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    path("account/", include("apps.accounts.urls")),
    path("", include("apps.nba.urls")),
    path("", include("apps.fantasy.urls")),
    path("", include("apps.core.urls")),
]

admin.site.site_header = "Basketball Fantasy Manager"
admin.site.site_title = "Basketball Fantasy Manager"
admin.site.index_title = "Administration"
