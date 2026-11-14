"""A player's injury report opens on demand instead of riding in every row."""

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.nba.models import Player, PlayerInjury


@pytest.fixture
def signed_in(client, user, password):
    client.login(email=user.email, password=password)
    return client


@pytest.fixture
def hurt(db):
    player = Player.objects.create(first_name="Hurt", last_name="Player", position="G")
    PlayerInjury.objects.create(
        player=player,
        observed_at=timezone.now(),
        status="Out",
        injury_type="Ankle",
        short_comment="Rolled it in practice",
    )
    return player


def modal_url(player):
    return reverse("nba:player-injury", args=[player.slug])


def test_signed_out_is_sent_to_sign_in(client, hurt):
    assert client.get(modal_url(hurt)).status_code == 302


def test_an_unknown_player_is_a_404(signed_in):
    assert signed_in.get(reverse("nba:player-injury", args=["nobody"])).status_code == 404


def test_the_modal_shows_the_newest_report(signed_in, hurt):
    body = signed_in.get(modal_url(hurt)).content.decode()

    assert "Hurt Player Injury Status" in body
    assert "Rolled it in practice" in body
    assert "Ankle" in body
    # The shared modal shell: a dialog that closes itself.
    assert 'role="dialog"' in body
    assert "@close-modal.window" in body


def test_a_healthy_player_says_there_is_no_report(signed_in, db):
    fit = Player.objects.create(first_name="Fit", last_name="Player", position="F")

    body = signed_in.get(modal_url(fit)).content.decode()

    assert "No injury report has been recorded" in body


def test_the_table_carries_the_button_not_the_report(signed_in, hurt):
    body = signed_in.get(reverse("nba:player-list"), {"inactive": "1"}).content.decode()

    assert f'hx-get="{modal_url(hurt)}"' in body
    assert 'hx-target="#modal-root"' in body
    assert "x-teleport" not in body
    assert "Rolled it in practice" not in body
