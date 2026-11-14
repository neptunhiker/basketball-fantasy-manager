"""Count every player's hotness once, so the new stored figure starts filled.

From here on each saved snapshot keeps it current (apps.fantasy.signals).
Uses the same pure function as the app, applied to this migration's models.
"""

from collections import defaultdict
from types import SimpleNamespace

from django.db import migrations

from apps.nba.stats import hotness_counts

FIELDS = ("as_of", "season_id", "fp_per_game", "games_played")


def backfill(apps, schema_editor):
    Player = apps.get_model("nba", "Player")
    PlayerSnapshot = apps.get_model("fantasy", "PlayerSnapshot")
    by_player = defaultdict(list)
    for row in PlayerSnapshot.objects.values("player_id", *FIELDS).iterator(chunk_size=5000):
        by_player[row.pop("player_id")].append(SimpleNamespace(**row))
    changed = []
    for player in Player.objects.only("pk"):
        rises, comparisons = hotness_counts(by_player.get(player.pk, ()))
        if comparisons:
            player.hotness_increases, player.hotness_comparisons = rises, comparisons
            changed.append(player)
    Player.objects.bulk_update(
        changed, ["hotness_increases", "hotness_comparisons"], batch_size=500
    )


class Migration(migrations.Migration):
    dependencies = [
        ("fantasy", "0016_trade_plans"),
        ("nba", "0026_player_hotness"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
