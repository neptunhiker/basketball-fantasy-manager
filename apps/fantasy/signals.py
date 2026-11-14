"""Keep `Player`'s stored hotness in step with his snapshots.

A signal rather than a call inside `services.record_snapshot` alone: that
service is the only writer in the app, but snapshots are also created by
hand in the shell, in tests and by fixtures, and a stored figure that only
one of those paths refreshes is a figure that is quietly wrong elsewhere.
`raw` saves (loaddata) are skipped -- the instance is half-loaded then; run
`manage.py refresh_hotness` afterwards. Deletes are not hooked: the only
delete is a whole season cascading away, which refreshes everyone in one go
(`SeasonDeleteView`) instead of once per row.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from . import services
from .models import PlayerSnapshot


@receiver(post_save, sender=PlayerSnapshot, dispatch_uid="fantasy.refresh_hotness")
def refresh_hotness_after_a_snapshot(sender, instance, raw=False, **kwargs):
    if raw:
        return
    player = instance.player if PlayerSnapshot.player.is_cached(instance) else None
    services.refresh_hotness(instance.player_id, player=player)
