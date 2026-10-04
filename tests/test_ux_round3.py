"""Round 3 of the UX fixes: Value, Hotness, explanations, the comparison page,
the team pages and editable notes."""

import datetime as dt
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.nba.models import Player, PlayerInjury, PlayerNote, Team, TeamNote
from apps.nba.stats import hotness_reading


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def teams(db):
    call_command("seed_teams", verbosity=0)
    return {team.abbreviation: team for team in Team.objects.all()}


def make_player(last_name, *, team=None, position="G", salary=None, fp=None, games=None):
    return Player.objects.create(
        first_name="P",
        last_name=last_name,
        position=position,
        team=team,
        current_salary=salary,
        current_total_fp=fp,
        current_games_played=games,
        current_fp_per_game=(Decimal(fp) / games) if fp is not None and games else None,
    )


# --- Value -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "expected", "css"),
    [
        (Decimal("1200000"), "+$1.20M", "text-emerald-700"),
        (Decimal("-800000"), "-$0.80M", "text-rose-700"),
    ],
)
def test_value_is_signed_and_coloured(amount, expected, css):
    html = render_to_string("nba/partials/value.html", {"amount": amount})

    assert expected in html
    assert css in html


def test_a_missing_value_is_a_dash():
    assert "\N{EN DASH}" in render_to_string("nba/partials/value.html", {"amount": None})


def test_the_players_list_calls_it_value_and_explains_it(signed_in, db):
    make_player("Bargain", salary=500_000, fp=300, games=10)  # 30 FP/G for $0.5M
    make_player("Overpaid", salary=40_000_000, fp=50, games=10)

    body = signed_in.get(reverse("nba:player-list")).text

    assert ">Value<" in body
    assert "Difference" not in body
    assert "text-emerald-700" in body and "text-rose-700" in body
    assert 'aria-label="What is Value?"' in body
    assert 'aria-label="What is Hotness?"' in body
    assert "means a bargain" in body  # the legend under the table


def test_the_player_page_has_a_value_card(signed_in, db):
    player = make_player("Bargain", salary=500_000, fp=300, games=10)

    body = signed_in.get(reverse("nba:player-detail", args=[player.slug])).text

    assert ">\n          Value " in body or "Value <span" in body or ">Value<" in body
    assert "text-emerald-700" in body


# --- Hotness ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("score", "label"),
    [("6/10", "hot"), ("3/5", "hot"), ("5/10", "steady"), ("4/10", "cold"), ("0/3", "cold")],
)
def test_hotness_labels_follow_the_thresholds(score, label):
    assert hotness_reading(score)["label"] == label


def test_two_comparisons_are_too_few_for_a_label():
    assert hotness_reading("2/2") == {"increases": 2, "comparisons": 2, "label": None}
    assert hotness_reading(None) is None


def test_hotness_shows_label_and_count():
    html = render_to_string("nba/partials/hotness.html", {"score": "7/10"})

    assert "Hot" in html
    assert "7 of 10" in html


def test_too_few_comparisons_show_only_the_count():
    html = render_to_string("nba/partials/hotness.html", {"score": "1/2"})

    assert "1 of 2" in html
    assert "Hot" not in html and "Cold" not in html and "Steady" not in html


# --- Comparison page --------------------------------------------------------------------


def test_the_comparison_page_is_german_for_german_users(signed_in, user, db):
    user.language = "de"
    user.save()
    first, second = make_player("Eins", salary=1_000_000), make_player("Zwei", salary=2_000_000)

    body = signed_in.get(
        reverse("nba:player-compare") + f"?player={first.slug}&player={second.slug}"
    ).text

    for english in (
        "Add player",
        "Comparison overview",
        "Remove P",
        ">Remove<",
        "Based on current",
    ):
        assert english not in body, english
    assert "Spieler hinzufügen" in body


def test_the_empty_comparison_no_longer_promises_roster_fit(signed_in, db):
    body = signed_in.get(reverse("nba:player-compare")).text

    assert "roster fit" not in body
    assert "value and recent form" in body


# --- Team pages ---------------------------------------------------------------------------


def test_the_team_page_puts_notes_after_the_players(signed_in, teams):
    make_player("Roster", team=teams["MIA"], salary=1_000_000)

    body = signed_in.get(reverse("nba:team-detail", args=["mia"])).text

    assert body.index("P Roster") < body.index("Private notes")


def test_the_team_list_shows_players_points_salary_and_injuries(signed_in, teams):
    heat = teams["MIA"]
    make_player("A", team=heat, salary=10_000_000, fp=300, games=10)
    hurt = make_player("B", team=heat, salary=5_000_000, fp=100, games=10)
    PlayerInjury.objects.create(player=hurt, observed_at=timezone.now(), status="Out")

    response = signed_in.get(reverse("nba:team-list"))

    miami = next(team for team in response.context["teams"] if team.abbreviation == "MIA")
    assert miami.active_players == 2
    assert miami.fp_per_game == Decimal("20.00")  # 400 points over 20 games
    assert miami.total_salary == Decimal("15000000")
    assert miami.injured_count == 1
    assert "$15.00M" in response.text


def test_the_team_list_can_rank_by_points_per_game(signed_in, teams):
    make_player("Low", team=teams["MIA"], fp=100, games=10)
    make_player("High", team=teams["BOS"], fp=400, games=10)

    response = signed_in.get(reverse("nba:team-list"), {"sort": "fpg"})

    ranked = [team.abbreviation for team in response.context["teams"]]
    assert ranked[:2] == ["BOS", "MIA"]
    assert response.context["ranked"]


# --- Notes -----------------------------------------------------------------------------------


@pytest.fixture
def note(user, db):
    player = make_player("Noted")
    return PlayerNote.objects.create(user=user, player=player, content="First take")


def edit_url(note):
    return reverse("nba:player-note-edit", args=[note.player.slug, note.pk])


def delete_url(note):
    return reverse("nba:player-note-delete", args=[note.player.slug, note.pk])


def test_a_note_can_be_edited_in_place(signed_in, note):
    form = signed_in.get(edit_url(note)).text
    assert "First take" in form and "<textarea" in form

    PlayerNote.objects.filter(pk=note.pk).update(created_at=timezone.now() - dt.timedelta(hours=2))
    saved = signed_in.post(edit_url(note), {"content": "Second take"}).text

    note.refresh_from_db()
    assert note.content == "Second take"
    assert "Second take" in saved
    assert "edited" in saved


def test_cancel_brings_the_note_back_unchanged(signed_in, note):
    body = signed_in.get(edit_url(note), {"cancel": "1"}).text

    assert "First take" in body
    assert "<textarea" not in body


def test_a_note_can_be_deleted_after_confirming(signed_in, note):
    modal = signed_in.get(delete_url(note)).text
    assert "Delete this note?" in modal

    response = signed_in.post(delete_url(note), headers={"HX-Request": "true"})

    assert not PlayerNote.objects.filter(pk=note.pk).exists()
    assert response["HX-Redirect"].endswith("#notes")


def test_someone_elses_note_cannot_be_touched(client, note, other_manager, db):
    client.login(email=other_manager.user.email, password="x" * 12)

    assert client.get(edit_url(note)).status_code == 404
    assert client.post(edit_url(note), {"content": "Hijacked"}).status_code == 404
    assert client.post(delete_url(note)).status_code == 404
    note.refresh_from_db()
    assert note.content == "First take"


def test_team_notes_can_be_edited_and_deleted(signed_in, user, teams):
    team_note = TeamNote.objects.create(user=user, team=teams["MIA"], content="Deep bench")
    edit = reverse("nba:team-note-edit", args=["mia", team_note.pk])
    delete = reverse("nba:team-note-delete", args=["mia", team_note.pk])

    signed_in.post(edit, {"content": "Thin bench"})
    team_note.refresh_from_db()
    assert team_note.content == "Thin bench"

    signed_in.post(delete, headers={"HX-Request": "true"})
    assert not TeamNote.objects.exists()


def test_the_notes_have_edit_and_delete_buttons(signed_in, note):
    body = signed_in.get(reverse("nba:player-detail", args=[note.player.slug])).text

    assert edit_url(note) in body
    assert delete_url(note) in body
    assert 'id="notes"' in body


def test_note_counts_show_in_the_lists(signed_in, user, note, teams):
    TeamNote.objects.create(user=user, team=teams["MIA"], content="A")
    TeamNote.objects.create(user=user, team=teams["MIA"], content="B")

    players = signed_in.get(reverse("nba:player-list"), {"inactive": "1"}).text
    team_list = signed_in.get(reverse("nba:team-list")).text

    assert reverse("nba:player-detail", args=[note.player.slug]) + "#notes" in players
    assert reverse("nba:team-detail", args=["mia"]) + "#notes" in team_list
    assert "2 private notes" in team_list


def test_other_peoples_notes_are_not_counted(signed_in, other_manager, teams):
    TeamNote.objects.create(user=other_manager.user, team=teams["MIA"], content="Theirs")

    assert "private note" not in signed_in.get(reverse("nba:team-list")).text
