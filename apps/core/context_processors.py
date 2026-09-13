from django.conf import settings


def site(request):
    """Values every template needs."""
    return {
        "SITE_NAME": settings.SITE_NAME,
        "DEBUG": settings.DEBUG,
        "ENVIRONMENT": settings.ENVIRONMENT,
        "INVITATION_TIMEOUT_DAYS": settings.INVITATION_TIMEOUT_DAYS,
    }
