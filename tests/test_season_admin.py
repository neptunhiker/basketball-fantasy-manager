"""Creating, editing and deleting seasons from inside the app.

A season is the only record here an administrator writes by hand -- everything
else is played into existence or imported -- and it is the only place the
signings cutoff can be set. So this is a staff screen, and the tests are mostly
about two things the model decides and the screen only has to report honestly.

`only_one_current_season` is a partial unique constraint, so ticking "current"
cannot mean "and now there are two": it has to hand the flag over, and the
handover has to happen before the save or the constraint fires first.

Deletion has three answers, and none of them is this view's policy.
`Roster.season` is PROTECT and `PlayerSnapshot.season` is CASCADE, so a season
with rosters cannot go at all, a season with only prices can go and takes a
year of salary history with it, and an empty one costs a row.
"""

import datetime as dt
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.fantasy.models import Roster, Season
from apps.nba.models import Player

pytestmark = pytest.mark.django_db

LIST = reverse("fantasy:season-list")
CREATE = reverse("fantasy:season-create")


def update_url(season):
    return reverse("fantasy:season-update", args=[season.pk])


def delete_url(season):
    return reverse("fantasy:season-delete", args=[season.pk])


@pytest.fixture
def staff(client, staff_user, password):
    client.login(email=staff_user.email, password=password)
    return client


@pytest.fixture
def player(client, user, password):
    """Signed in, but not staff. The seasons page is readable, not editable."""
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def season(db):
    return Season.objects.create(
        label="2026-27",
        starts_on=dt.date(2026, 10, 20),
        ends_on=dt.date(2027, 4, 11),
        is_current=True,
    )


def form_values(**overrides):
    return {
        "label": "2027-28",
        "starts_on": "2027-10-19",
        "ends_on": "2028-04-09",
    } | overrides


# --- who may touch it --------------------------------------------------------


def test_the_page_needs_a_login(client):
    assert client.get(LIST).status_code == 302


def test_a_player_sees_the_seasons_but_no_controls(player, season):
    body = player.get(LIST).content.decode()

    assert season.label in body
    assert CREATE not in body
    assert update_url(season) not in body
    assert delete_url(season) not in body


def test_a_player_cannot_reach_the_editor(player, season):
    """The controls being hidden is a courtesy; this is the rule."""
    assert player.get(CREATE).status_code == 403
    assert player.get(update_url(season)).status_code == 403
    assert player.get(delete_url(season)).status_code == 403
    assert player.post(CREATE, form_values()).status_code == 403
    assert Season.objects.count() == 1


def test_staff_get_the_controls(staff, season):
    body = staff.get(LIST).content.decode()

    assert CREATE in body
    assert update_url(season) in body
    assert delete_url(season) in body


# --- creating ----------------------------------------------------------------


def test_a_season_can_be_created(staff):
    response = staff.post(CREATE, form_values(), headers={"HX-Request": "true"})

    assert response.status_code == 204
    assert response["HX-Redirect"] == LIST
    created = Season.objects.get(label="2027-28")
    assert created.starts_on == dt.date(2027, 10, 19)
    assert created.ends_on == dt.date(2028, 4, 9)


def test_a_new_season_is_not_current_unless_asked(staff):
    staff.post(CREATE, form_values(), headers={"HX-Request": "true"})
    assert Season.objects.get(label="2027-28").is_current is False


def test_a_new_season_opens_with_signings_open(staff):
    """Empty by default, which is what makes the cutoff a deliberate act."""
    staff.post(CREATE, form_values(), headers={"HX-Request": "true"})
    created = Season.objects.get(label="2027-28")

    assert created.signings_close_at is None
    assert created.signings_open is True


def test_a_duplicate_label_is_refused(staff, season):
    response = staff.post(CREATE, form_values(label=season.label), headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert "already exists" in response.content.decode()
    assert Season.objects.count() == 1


def test_a_season_ending_before_it_starts_is_refused(staff):
    """The check constraint said in a sentence, before it becomes a 500."""
    response = staff.post(
        CREATE,
        form_values(starts_on="2028-04-09", ends_on="2027-10-19"),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "has to end after it starts" in response.content.decode()
    assert Season.objects.count() == 0


def test_the_form_survives_a_plain_post(staff):
    """Progressive enhancement: no HTMX, so a redirect rather than a 204."""
    response = staff.post(CREATE, form_values())

    assert response.status_code == 302
    assert response["Location"] == LIST
    assert Season.objects.filter(label="2027-28").exists()


# --- the cutoff --------------------------------------------------------------


def test_the_cutoff_can_be_set_on_the_form(staff, season):
    response = staff.post(
        update_url(season),
        form_values(
            label=season.label,
            starts_on="2026-10-20",
            ends_on="2027-04-11",
            is_current="on",
            signings_close_at="2026-10-20T19:30",
        ),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 204
    season.refresh_from_db()
    assert season.signings_close_at is not None


def test_the_cutoff_is_read_in_the_local_timezone(staff, season):
    """`datetime-local` carries no offset, so the app has to supply one.

    Berlin, per `TIME_ZONE`: an administrator types the wall-clock time the
    deadline actually falls at, and gets that moment back on the form.
    """
    from django.utils import timezone

    staff.post(
        update_url(season),
        form_values(
            label=season.label,
            starts_on="2026-10-20",
            ends_on="2027-04-11",
            signings_close_at="2026-10-20T19:30",
        ),
        headers={"HX-Request": "true"},
    )
    season.refresh_from_db()

    local = timezone.localtime(season.signings_close_at)
    assert (local.hour, local.minute) == (19, 30)
    # And round-trips: the form shows back what was typed, not a UTC shift.
    assert 'value="2026-10-20T19:30"' in staff.get(update_url(season)).content.decode()


def test_the_cutoff_can_be_cleared_again(staff, season):
    from django.utils import timezone

    season.signings_close_at = timezone.now()
    season.save(update_fields=["signings_close_at"])

    staff.post(
        update_url(season),
        form_values(label=season.label, starts_on="2026-10-20", ends_on="2027-04-11"),
        headers={"HX-Request": "true"},
    )
    season.refresh_from_db()

    assert season.signings_close_at is None
    assert season.signings_open is True


def test_a_cutoff_after_the_season_ends_is_refused(staff, season):
    """`Season.clean`, reached through the ModelForm rather than duplicated."""
    response = staff.post(
        update_url(season),
        form_values(
            label=season.label,
            starts_on="2026-10-20",
            ends_on="2027-04-11",
            signings_close_at="2027-05-01T19:30",
        ),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "cannot close after the season ends" in response.content.decode()
    season.refresh_from_db()
    assert season.signings_close_at is None


# --- the current season ------------------------------------------------------


def test_editing_a_season_keeps_its_dates(staff, season):
    staff.post(
        update_url(season),
        form_values(label="2026-27 revised", starts_on="2026-10-20", ends_on="2027-04-11"),
        headers={"HX-Request": "true"},
    )
    season.refresh_from_db()

    assert season.label == "2026-27 revised"
    assert season.starts_on == dt.date(2026, 10, 20)


def test_making_a_season_current_takes_it_off_the_other(staff, season):
    """The constraint allows one, so ticking the box has to be a handover.

    Two current seasons is an IntegrityError, not a state -- which is why the
    form unsets the old one before saving the new.
    """
    other = Season.objects.create(
        label="2027-28", starts_on=dt.date(2027, 10, 19), ends_on=dt.date(2028, 4, 9)
    )

    response = staff.post(
        update_url(other),
        form_values(
            label=other.label, starts_on="2027-10-19", ends_on="2028-04-09", is_current="on"
        ),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 204
    season.refresh_from_db()
    other.refresh_from_db()
    assert other.is_current is True
    assert season.is_current is False
    assert Season.objects.filter(is_current=True).count() == 1


def test_a_new_season_may_be_created_current(staff, season):
    """The same handover, on a season that does not exist yet."""
    staff.post(CREATE, form_values(is_current="on"), headers={"HX-Request": "true"})

    season.refresh_from_db()
    assert Season.objects.get(label="2027-28").is_current is True
    assert season.is_current is False


def test_keeping_a_season_current_is_not_a_clash(staff, season):
    """Saving the form unchanged must not unset the flag it is re-asserting."""
    response = staff.post(
        update_url(season),
        form_values(
            label=season.label, starts_on="2026-10-20", ends_on="2027-04-11", is_current="on"
        ),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 204
    season.refresh_from_db()
    assert season.is_current is True


def test_a_season_can_stop_being_current(staff, season):
    staff.post(
        update_url(season),
        form_values(label=season.label, starts_on="2026-10-20", ends_on="2027-04-11"),
        headers={"HX-Request": "true"},
    )
    season.refresh_from_db()

    assert season.is_current is False
    assert Season.objects.filter(is_current=True).count() == 0


# --- deleting ----------------------------------------------------------------


def test_an_empty_season_deletes_on_a_plain_confirm(staff, season):
    """No word to type: there is nothing to lose but the row."""
    dialog = staff.get(delete_url(season)).content.decode()
    assert "nothing goes with it" in dialog
    assert 'name="confirm"' not in dialog

    response = staff.post(delete_url(season), headers={"HX-Request": "true"})

    assert response.status_code == 204
    assert not Season.objects.filter(pk=season.pk).exists()


def test_a_season_holding_rosters_cannot_be_deleted(staff, season, manager):
    """`Roster.season` is PROTECT, so this is the database's answer, not mine."""
    Roster.objects.create(manager=manager, season=season, name="Roster 1")

    dialog = staff.get(delete_url(season)).content.decode()
    assert "still holds 1 roster" in dialog
    # No confirm button at all: the modal is explaining, not daring.
    assert "Delete season" not in dialog
    assert ">\n        Close\n      </button>" in dialog

    # The empty confirm the dialog itself would post. "DELETE" would be
    # answering a question this season never asks -- `confirm_word` is empty
    # here -- so it would be rejected by the word check and never reach the
    # guard being tested.
    response = staff.post(delete_url(season), {"confirm": ""})
    body = response.content.decode()

    assert Season.objects.filter(pk=season.pk).exists()
    assert "Delete those first" in body
    # Refused by the guard, not by tripping over PROTECT and reporting back:
    # the race message below is what that would look like, and its absence is
    # what says the delete was never attempted.
    assert "has a roster in it now" not in body


def test_a_roster_appearing_late_is_still_refused(staff, season, manager, monkeypatch):
    """The race the count cannot close, and PROTECT is what actually closes it.

    A roster created between the modal counting and this POST arriving. Faked
    by making the view count zero while one exists, which is precisely what
    that ordering looks like from inside the request -- and the only way to
    reach the ProtectedError branch, since the guard above shuts every path a
    user has.
    """
    from apps.fantasy.views import SeasonDeleteView

    Roster.objects.create(manager=manager, season=season, name="Roster 1")
    monkeypatch.setattr(SeasonDeleteView, "counts", lambda self, obj: (0, 0))

    response = staff.post(delete_url(season), {"confirm": ""})

    assert response.status_code == 200
    assert Season.objects.filter(pk=season.pk).exists()
    assert "has a roster in it now" in response.content.decode()


def test_a_season_holding_prices_needs_the_word_typed(staff, season):
    """CASCADE here, so confirming really does destroy the salary history."""
    _snapshot(season)

    dialog = staff.get(delete_url(season)).content.decode()
    assert "1 imported price" in dialog
    assert "nothing can rebuild" in dialog
    assert 'name="confirm"' in dialog

    refused = staff.post(delete_url(season), {"confirm": "delete"})
    assert refused.status_code == 200
    assert Season.objects.filter(pk=season.pk).exists()

    response = staff.post(delete_url(season), {"confirm": "DELETE"}, headers={"HX-Request": "true"})
    assert response.status_code == 204
    assert not Season.objects.filter(pk=season.pk).exists()


def test_deleting_a_season_takes_its_prices_with_it(staff, season):
    """The reason the word has to be typed, asserted rather than assumed."""
    from apps.fantasy.models import PlayerSnapshot

    _snapshot(season)
    assert PlayerSnapshot.objects.count() == 1

    staff.post(delete_url(season), {"confirm": "DELETE"}, headers={"HX-Request": "true"})

    assert PlayerSnapshot.objects.count() == 0


def test_a_player_cannot_delete_a_season(player, season):
    player.post(delete_url(season))
    assert Season.objects.filter(pk=season.pk).exists()


def test_the_list_shows_what_makes_a_season_undeletable(staff, season, manager):
    """The counts are the reason, so they are on the row before anyone tries."""
    Roster.objects.create(manager=manager, season=season, name="Roster 1")
    _snapshot(season)

    body = staff.get(LIST).content.decode()
    row = body.split(season.label)[1].split("</tr>")[0]

    assert ">\n                1\n              </td>" in row


def test_the_list_says_whether_signings_have_passed(staff, season):
    from django.utils import timezone

    assert "Open all season" in staff.get(LIST).content.decode()

    season.signings_close_at = timezone.now() + dt.timedelta(days=3)
    season.save(update_fields=["signings_close_at"])
    assert "Closes" in staff.get(LIST).content.decode()

    season.signings_close_at = timezone.now() - dt.timedelta(days=3)
    season.save(update_fields=["signings_close_at"])
    assert "Closed" in staff.get(LIST).content.decode()


def _snapshot(season):
    from apps.fantasy.models import PlayerSnapshot

    player = Player.objects.create(
        first_name="Priced", last_name="Player", position="G", current_salary=Decimal(1_000_000)
    )
    return PlayerSnapshot.objects.create(
        player=player,
        season=season,
        as_of=dt.datetime(2026, 11, 1, 8, tzinfo=dt.UTC),
        salary=Decimal(1_000_000),
        total_fp=Decimal("250.00"),
        games_played=10,
    )
