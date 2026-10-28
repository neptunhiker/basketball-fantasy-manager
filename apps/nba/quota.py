"""What RapidAPI says about a subscription's monthly requests, and when to stop.

Every RapidAPI response carries the subscription's count in its headers. It
covers each request made with the key -- from production, staging and a
laptop alike -- so it is the one tally all of them agree on, unlike the daily
counters each database keeps for itself.
"""

from datetime import timedelta

from django.utils import timezone

from .models import ApiQuota


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def record(provider, headers, now=None):
    """Keep what a response's headers say about `provider`'s monthly requests."""
    if headers is None:
        return
    remaining = _int(headers.get("X-RateLimit-Requests-Remaining"))
    if remaining is None or remaining < 0:
        return
    now = now or timezone.now()
    reset = _int(headers.get("X-RateLimit-Requests-Reset"))
    ApiQuota.objects.update_or_create(
        provider=provider,
        defaults={
            "limit": _int(headers.get("X-RateLimit-Requests-Limit")),
            "remaining": remaining,
            "resets_at": now + timedelta(seconds=reset) if reset is not None else None,
            "reported_at": now,
        },
    )


def low(provider, reserve, now=None):
    """`provider`'s quota row when it is below `reserve` and still current, else None.

    A count from before RapidAPI's reset says nothing about now: the next
    request goes ahead and brings the new figure. Without a reset time, a count
    from an earlier calendar month is taken as out of date instead.
    """
    quota = ApiQuota.objects.filter(provider=provider).first()
    if quota is None or quota.remaining >= reserve:
        return None
    now = now or timezone.now()
    if quota.resets_at is not None:
        current = quota.resets_at > now
    else:
        reported = timezone.localtime(quota.reported_at)
        today = timezone.localtime(now)
        current = (reported.year, reported.month) == (today.year, today.month)
    return quota if current else None


def describe(quota, reserve):
    """Why `quota` stops requests, for a log line or a command's refusal."""
    until = (
        f" until {timezone.localtime(quota.resets_at):%d.%m.%Y %H:%M}" if quota.resets_at else ""
    )
    return f"only {quota.remaining} RapidAPI requests left{until}, below the reserve of {reserve}"
