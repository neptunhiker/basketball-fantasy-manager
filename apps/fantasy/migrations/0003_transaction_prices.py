"""Record what each side of a move was worth, not just the net.

The backfill deliberately stops short of the trades. A buy's price *is* its cash
delta and a sale's likewise, so those two can be filled in with certainty. A
trade's delta is the difference of two figures, and nothing in the database can
say which two -- reading today's salaries back onto a past trade would be
inventing history rather than recovering it. Those rows keep their net and admit
they know nothing more.
"""

from django.db import migrations, models


def price_the_certain_moves(apps, schema_editor):
    Transaction = apps.get_model("fantasy", "Transaction")
    # A pure purchase: the roster spent exactly the price, so -delta is it.
    Transaction.objects.filter(player_in__isnull=False, player_out__isnull=True).update(
        price_in=models.F("cash_delta") * -1
    )
    # A pure sale: the roster received exactly the price.
    Transaction.objects.filter(player_out__isnull=False, player_in__isnull=True).update(
        price_out=models.F("cash_delta")
    )


def unprice(apps, schema_editor):
    Transaction = apps.get_model("fantasy", "Transaction")
    Transaction.objects.update(price_in=None, price_out=None)


class Migration(migrations.Migration):

    dependencies = [
        ('fantasy', '0002_playersnapshot'),
        ('nba', '0003_player_current_fp_per_game_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaction',
            name='price_in',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Preis Zugang'),
        ),
        migrations.AddField(
            model_name='transaction',
            name='price_out',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Preis Abgang'),
        ),
        migrations.RunPython(price_the_certain_moves, unprice),
        migrations.AddConstraint(
            model_name='transaction',
            constraint=models.CheckConstraint(condition=models.Q(('price_in__gte', 0), ('price_out__gte', 0)), name='prices_are_never_negative'),
        ),
        migrations.AddConstraint(
            model_name='transaction',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('price_in__isnull', True), ('player_in__isnull', False), _connector='OR'), models.Q(('price_out__isnull', True), ('player_out__isnull', False), _connector='OR')), name='a_price_needs_the_player_it_paid_for'),
        ),
    ]
