"""Keep `Player`'s stored hotness in step with his snapshots.

A signal rather than a call inside `services.record_snapshot` alone: that
service is the only writer in the app, but snapshots are also created by
hand in the shell, in tests and by fixtures, and a stored figure that only
one of those paths refreshes is a figure that is quietly wrong elsewhere.
`raw` saves (loaddata) are skipped -- the instance is half-loaded then; run
`manage.py refresh_hotness` afterwards.

Deletes are not hooked here. A post_delete receiver would make Django load
and signal every row of a season deleted by cascade -- a year of daily
prices -- so each delete path recounts on its own instead: SeasonDeleteView
and SeasonAdmin recount everyone in one pass, PlayerSnapshotAdmin the
players whose rows went, and seed_demo_data after its clean-up.
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
