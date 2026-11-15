from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.core.views import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    path("account/", include("apps.accounts.urls")),
    path("", include("apps.nba.urls")),
    path("", include("apps.fantasy.urls")),
    path("", include("apps.analytics.urls")),
    path("", include("apps.reports.urls")),
    path("", include("apps.perf.urls")),
    path("", include("apps.core.urls")),
]

if "debug_toolbar" in settings.INSTALLED_APPS:  # local development only
    from debug_toolbar.toolbar import debug_toolbar_urls

    urlpatterns += debug_toolbar_urls()

admin.site.site_header = "Pengi Fantasy Manager"
admin.site.site_title = "Pengi Fantasy Manager"
admin.site.index_title = "Administration"
