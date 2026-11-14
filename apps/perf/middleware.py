"""Measure every request: wall time, database work and response size.

Sits first in `MIDDLEWARE`, so its clock covers every other middleware as well
as the view and the template. Responses to staff get a `Server-Timing` header
-- DevTools shows it under the request's Timing tab, on production too -- and
every request, unless switched off, one `RequestMetric` row.

Queries are counted through `connection.execute_wrapper` rather than
`connection.queries`, which only fills while DEBUG is on.
"""

import logging
import random
import re
import time
from urllib.parse import unquote_plus

from django.conf import settings
from django.db import connection
from django.utils import timezone

from .models import RequestMetric

logger = logging.getLogger(__name__)


class QueryTimer:
    """An execute wrapper that counts statements and adds up their time."""

    def __init__(self):
        self.count = 0
        self.seconds = 0.0

    def __call__(self, execute, sql, params, many, context):
        start = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            self.seconds += time.perf_counter() - start
            self.count += 1


# What a parameter name looks like, once decoded.
PARAMETER_NAME = re.compile(r"[A-Za-z0-9_\-\[\]]{1,40}")


def parameter_names(query_string):
    """Sorted parameter names, e.g. "position,sort" -- never values.

    Only a part with an "=" names a parameter, even an empty one ("?q=").
    A bare part ("?someone@mail", "?a1b2c3d4") is a value sent on its own --
    an address, an invite code -- and is dropped whatever it looks like.
    `request.GET` cannot tell the two apart: both arrive as a key with "".
    """
    names = set()
    for part in query_string.split("&"):
        key, equals, _value = part.partition("=")
        key = unquote_plus(key)
        if equals and PARAMETER_NAME.fullmatch(key):
            names.add(key)
    return ",".join(sorted(names))[:300]


def ignored(path):
    return any(path.startswith(prefix) for prefix in settings.PERF_IGNORED_PATHS)


class RequestMetricsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if ignored(request.path):
            return self.get_response(request)

        queries = QueryTimer()
        start = time.perf_counter()
        with connection.execute_wrapper(queries):
            response = self.get_response(request)
        duration_ms = (time.perf_counter() - start) * 1000
        sql_ms = queries.seconds * 1000

        # Staff only. An exact server-side time on every response lets anyone
        # time the password reset, which mails only addresses that exist.
        user = getattr(request, "user", None)
        if user is not None and user.is_staff:
            timing = (
                f'app;dur={duration_ms:.1f}, db;dur={sql_ms:.1f};desc="{queries.count} queries"'
            )
            # Added to, not replaced: the debug toolbar may have set entries of its own.
            existing = response.get("Server-Timing")
            response["Server-Timing"] = f"{existing}, {timing}" if existing else timing
        # Read per request rather than once, so a test can switch it.
        if settings.PERF_RECORD_REQUESTS and random.random() < settings.PERF_SAMPLE_RATE:
            self.record(request, response, duration_ms, queries.count, sql_ms)
        return response

    def record(self, request, response, duration_ms, sql_count, sql_ms):
        # Outside the view's ATOMIC_REQUESTS block, so this commits on its own
        # -- and outside the execute wrapper, so it does not count itself.
        match = getattr(request, "resolver_match", None)
        try:
            RequestMetric.objects.create(
                recorded_at=timezone.now(),
                route=(match.view_name if match else "")[:200],
                method=request.method[:10],
                status=response.status_code,
                htmx=request.headers.get("HX-Request") == "true",
                duration_ms=duration_ms,
                sql_count=sql_count,
                sql_ms=sql_ms,
                response_bytes=None if response.streaming else len(response.content),
                query_keys=parameter_names(request.META.get("QUERY_STRING", "")),
            )
        except Exception:
            # A measurement must never cost the user their page.
            logger.exception("Could not record request metric")
