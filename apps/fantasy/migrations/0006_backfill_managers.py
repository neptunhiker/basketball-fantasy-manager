"""Give every roster a manager, one per owner.

Only users who already own a roster get a profile. A manager is someone who
manages a roster, so a user with nothing to manage is not one yet -- they
become one when they build their first roster, in `services.manager_for`.

The nickname rule is written out here rather than imported from `services`. A
migration is a historical record: it should still do in a year what it did the
day it ran, even after the runtime rule has moved on. The two are allowed to
drift, because this one runs exactly once.
"""

from django.conf import settings
from django.db import migrations


def _nickname(user):
    """A first guess at a handle, from whatever the account already knows."""
    return (user.first_name or user.email.split("@")[0])[:40]


def create_managers(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Manager = apps.get_model("fantasy", "Manager")
    Roster = apps.get_model("fantasy", "Roster")

    # One profile per owner rather than per roster: a user's rosters were all
    # played by the same person, so they belong under the same manager.
    owner_ids = set(Roster.objects.values_list("owner_id", flat=True))
    for owner in User.objects.filter(pk__in=owner_ids):
        manager = Manager.objects.create(user=owner, nick_name=_nickname(owner))
        Roster.objects.filter(owner=owner).update(manager=manager)


def restore_owners(apps, schema_editor):
    """Point the rosters back at the accounts behind their managers.

    Leaves the `Manager` rows alone: 0005 drops the table on its way back out,
    and deleting them here would cascade to the very rosters being preserved.
    """
    Manager = apps.get_model("fantasy", "Manager")
    Roster = apps.get_model("fantasy", "Roster")

    for manager in Manager.objects.all():
        Roster.objects.filter(manager=manager).update(owner_id=manager.user_id)


class Migration(migrations.Migration):

    dependencies = [
        ("fantasy", "0005_manager"),
    ]

    operations = [
        migrations.RunPython(create_managers, restore_owners),
    ]
