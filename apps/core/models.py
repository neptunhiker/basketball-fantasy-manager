from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base for records where we care when they appeared or changed.

    Almost everything we scrape or the user edits wants this, and having it in
    one place keeps the field names consistent across apps.
    """

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True
