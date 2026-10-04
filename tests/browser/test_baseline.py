"""Screenshots of the key pages at phone and tablet width, plus a report of
which pages scroll sideways. Run before and after each mobile phase:

    SCREENS_FOLDER=phase1 uv run pytest -m browser tests/browser/test_baseline.py -s
"""

import os

from django.urls import reverse

from .conftest import page_overflow, shot

FOLDER = os.environ.get("SCREENS_FOLDER", "baseline")


def key_pages(game):
    player, team, roster = game["players"][0], game["teams"][0], game["roster"]
    compare = reverse("nba:player-compare") + "".join(
        f"{'&' if i else '?'}player={p.slug}" for i, p in enumerate(game["players"][:3])
    )
    return [
        ("home", game["user"], reverse("core:dashboard")),
        ("players", game["user"], reverse("nba:player-list")),
        ("player", game["user"], reverse("nba:player-detail", args=[player.slug])),
        ("compare", game["user"], compare),
        ("teams", game["user"], reverse("nba:team-list")),
        ("team", game["user"], reverse("nba:team-detail", args=[team.abbreviation.lower()])),
        ("rosters", game["user"], reverse("fantasy:roster-list")),
        ("build", game["user"], reverse("fantasy:roster-build", args=[roster.pk])),
        ("history", game["user"], reverse("fantasy:roster-history", args=[roster.pk])),
        ("rules", game["user"], reverse("fantasy:roster-rules", args=[roster.pk])),
        ("managers", game["user"], reverse("fantasy:manager-list")),
        ("seasons", game["admin"], reverse("fantasy:season-list")),
        ("users", game["admin"], reverse("accounts:user-list")),
        ("user", game["admin"], reverse("accounts:user-detail", args=[game["user"].pk])),
    ]


def test_screenshots_and_sideways_scroll(game, open_page):
    report = []
    for name, user, path in key_pages(game):
        for size in ("phone", "tablet"):
            page = open_page(user, path, size=size)
            shot(page, FOLDER, f"{name}-{size}")
            report.append((name, size, page_overflow(page)))
        dark = open_page(user, path, size="phone", dark=True)
        shot(dark, FOLDER, f"{name}-phone-dark")

    # The trade dialog, opened the way a user opens it -- in a running season
    # (the seeded one has not tipped off yet) with a trade to spend.
    season = game["season"]
    season.starts_on = season.starts_on.replace(year=season.starts_on.year - 1)
    season.signings_open_at = season.signings_close_at = None
    season.save()
    game["roster"].__class__.objects.filter(pk=game["roster"].pk).update(trades_available=1)
    page = open_page(game["user"], reverse("fantasy:roster-build", args=[game["roster"].pk]))
    page.get_by_role("button", name="Trade").first.click()
    page.wait_for_selector("#trade-body")
    shot(page, FOLDER, "trade-dialog-phone")

    print("\nSideways scroll at each width (px, 0 is right):")
    for name, size, overflow in report:
        flag = "  <-- scrolls sideways" if overflow > 0 else ""
        print(f"  {name:<10} {size:<6} {overflow:>5}{flag}")
