from django.conf import settings
from django.utils import timezone

from apps.nba.models import NbaApiUsage
from apps.nba.services import PROVIDER


def site(request):
    """Values every template needs."""
    latest_injury_api_usage = (
        NbaApiUsage.objects.filter(provider=PROVIDER)
        .order_by("-usage_date", "-updated_at")
        .first()
    )
    injury_refresh_available = not (
        latest_injury_api_usage
        and latest_injury_api_usage.usage_date == timezone.localdate()
        and latest_injury_api_usage.request_count > 0
    )
    return {
        "SITE_NAME": settings.SITE_NAME,
        "DEBUG": settings.DEBUG,
        "ENVIRONMENT": settings.ENVIRONMENT,
        "INVITATION_TIMEOUT_DAYS": settings.INVITATION_TIMEOUT_DAYS,
        "last_injury_api_call": (
            latest_injury_api_usage.updated_at if latest_injury_api_usage else None
        ),
        "injury_refresh_available": injury_refresh_available,
    }
