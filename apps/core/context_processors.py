from django.conf import settings
from django.utils import timezone

from apps.nba.models import NbaApiUsage
from apps.nba.services import PROVIDER, daily_limit_for


def site(request):
    """Values every template needs."""
    latest_injury_api_usage = (
        NbaApiUsage.objects.filter(provider=PROVIDER).order_by("-usage_date", "-updated_at").first()
    )
    used_today = (
        latest_injury_api_usage.request_count
        if latest_injury_api_usage and latest_injury_api_usage.usage_date == timezone.localdate()
        else 0
    )
    # Only a signed-in user can press the button; anyone else gets the lower
    # limit here, never the command's.
    user = getattr(request, "user", None)
    limit = (
        daily_limit_for(user)
        if user is not None and user.is_authenticated
        else settings.RAPID_API_DAILY_LIMIT
    )
    injury_refresh_available = used_today < limit
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
