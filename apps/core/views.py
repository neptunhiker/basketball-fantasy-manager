from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connection
from django.http import JsonResponse
from django.views.generic import TemplateView


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"


def healthz(request):
    """Liveness/readiness probe for Fly. Checks the database is reachable."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "error", "database": "unreachable"}, status=503)
    return JsonResponse({"status": "ok"})
