from django.db import models


class RequestMetric(models.Model):
    """What one request cost: time, database work and the size of its answer.

    One row per request, written by `apps.perf.middleware`. Deliberately says
    nothing about who asked or what for -- no user, no IP, no parameter
    values. The route and the names of the parameters are enough to tell a
    plain page from a filtered or sorted one, and a performance table should
    not double as an activity log.
    """

    recorded_at = models.DateTimeField(db_index=True)
    # The URL name, e.g. "nba:player-list": stable across slugs and UUIDs,
    # so one route aggregates into one line. Empty for a path nothing matched.
    route = models.CharField(max_length=200, blank=True)
    method = models.CharField(max_length=10)
    status = models.PositiveSmallIntegerField()
    htmx = models.BooleanField(default=False)
    duration_ms = models.FloatField()
    sql_count = models.PositiveIntegerField()
    sql_ms = models.FloatField()
    # Before compression: Caddy compresses on the way out. NULL for a
    # streaming response, whose size is not known when it leaves the app.
    response_bytes = models.PositiveIntegerField(null=True, blank=True)
    # Sorted query-parameter names, e.g. "position,sort" -- never the values.
    query_keys = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-recorded_at"]
        indexes = [models.Index(fields=["route", "-recorded_at"])]

    def __str__(self):
        return f"{self.method} {self.route or '-'} {self.duration_ms:.0f} ms"
