"""Importing the basketball.de player list into players and snapshots.

The fetching and parsing live in `bbde`; this module decides what the rows
mean for our data: which player a row is, what changes, and who has dropped
off the list. Every run is recorded as an `ImportRun`.

The basketball.de login is an argument and nothing more. It is handed to the
client for one login and is never written to the run, the log or a queue.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.fantasy.models import Season
from apps.fantasy.services import record_snapshot

from .bbde import BbdeClient, BbdeError
from .models import ImportRun, Player, Team
from .services import normalize_name

logger = logging.getLogger(__name__)

FREE_AGENTS = "Free Agents"
# basketball.de spells out the one franchise whose official name we shorten.
TEAM_ALIASES = {"Los Angeles Clippers": "LAC"}

# A run that is still "running" after this long died without cleaning up, and
# should not block the next one forever.
RUNNING_TIMEOUT = timedelta(minutes=10)
# Marking players inactive is skipped when a run sees far fewer rows than the
# last one: a half-read list should not retire half the league.
INACTIVATION_MIN_SHARE = Decimal("0.8")


class ImportBlocked(BbdeError):
    """The import could not start. The message is safe to show to staff."""


class NoCurrentSeason(ImportBlocked):
    pass


class ImportAlreadyRunning(ImportBlocked):
    pass


class NoTeams(ImportBlocked):
    pass


class _DryRunRollback(Exception):
    """Raised inside the write transaction to undo a dry run."""


def import_bbde(
    username,
    password,
    *,
    triggered_by=None,
    source=ImportRun.Source.WEB,
    dry_run=False,
    client=None,
):
    """Log in, read every page and bring players and snapshots up to date.

    Everything is fetched before anything is written, and all writes happen in
    one transaction: a run either applies completely or not at all. Returns the
    finished `ImportRun`; raises a `BbdeError` (with the run marked failed) when
    basketball.de cannot be read.
    """
    season = Season.objects.filter(is_current=True).first()
    if season is None:
        raise NoCurrentSeason("There is no current season to import into.")
    if ImportRun.objects.filter(
        status=ImportRun.Status.RUNNING, started_at__gte=timezone.now() - RUNNING_TIMEOUT
    ).exists():
        raise ImportAlreadyRunning("Another import is still running. Please try again shortly.")

    run = ImportRun.objects.create(
        triggered_by=triggered_by, source=source, season=season, dry_run=dry_run
    )
    # Without teams every rostered player would be skipped as "unknown team",
    # and the run would still report success. Stop before logging in instead.
    if not Team.objects.exists():
        message = "There are no NBA teams in the database. Run `manage.py seed_teams` first."
        _fail(run, message)
        raise NoTeams(message)
    try:
        client = client or BbdeClient()
        client.login(username, password)
        pages = list(client.iter_pages())
        _apply(run, season, pages, dry_run=dry_run)
    except BbdeError as exc:
        _fail(run, str(exc))
        raise
    except Exception:
        logger.exception("basketball.de import %s failed", run.pk)
        _fail(run, "Unexpected error. The details are in the server log.")
        raise

    run.status = ImportRun.Status.SUCCEEDED
    run.finished_at = timezone.now()
    run.save()
    return run


def _fail(run, message):
    run.status = ImportRun.Status.FAILED
    run.error = message[:500]
    run.finished_at = timezone.now()
    run.save()


def _unique_rows(run, pages):
    """All rows in page order, each basketball.de player once.

    The list is sorted by name and read page by page, so a player added while
    the import runs can push a row onto the next page and show it twice.
    """
    rows = []
    seen_ids = set()
    for page in pages:
        run.skipped.extend(f"{name}: {reason}" for name, reason in page.problems)
        for row in page.rows:
            if row.bbde_id is not None:
                if row.bbde_id in seen_ids:
                    continue
                seen_ids.add(row.bbde_id)
            rows.append(row)
    return rows


def _apply(run, season, pages, *, dry_run):
    run.pages = len(pages)
    expected_pages = pages[0].last_page if pages else None
    if expected_pages and expected_pages != len(pages):
        run.warnings.append(
            f"The pager announced {expected_pages} pages but {len(pages)} were read."
        )
    rows = _unique_rows(run, pages)
    run.rows = len(rows)

    try:
        with transaction.atomic():
            _write(run, season, rows)
            if dry_run:
                raise _DryRunRollback
    except _DryRunRollback:
        pass


def _write(run, season, rows):
    teams = {team.name: team for team in Team.objects.all()}
    by_abbreviation = {team.abbreviation: team for team in teams.values()}
    for name, abbreviation in TEAM_ALIASES.items():
        if abbreviation in by_abbreviation:
            teams.setdefault(name, by_abbreviation[abbreviation])

    players = list(Player.objects.all())
    by_bbde_id = {player.bbde_id: player for player in players if player.bbde_id is not None}
    unlinked = {}
    for player in players:
        if player.bbde_id is None:
            key = (normalize_name(player.first_name), normalize_name(player.last_name))
            unlinked.setdefault(key, []).append(player)

    as_of = run.started_at
    claimed = set()
    for row in rows:
        if row.team_name == FREE_AGENTS:
            team = None
        elif row.team_name in teams:
            team = teams[row.team_name]
        else:
            run.skipped.append(f"{row.display_name}: unknown team {row.team_name!r}")
            continue

        player = by_bbde_id.get(row.bbde_id) if row.bbde_id is not None else None
        if player is None:
            key = (normalize_name(row.first_name), normalize_name(row.last_name))
            candidates = [p for p in unlinked.get(key, []) if p.pk not in claimed]
            same_team = [p for p in candidates if p.team_id == (team.pk if team else None)]
            if len(same_team) == 1:
                player = same_team[0]
            elif len(same_team) > 1 or len(candidates) > 1:
                names = ", ".join(sorted(p.slug for p in (same_team or candidates)))
                run.ambiguous.append(f"{row.display_name} ({row.team_name}): {names}")
                continue
            elif len(candidates) == 1:
                player = candidates[0]

        if player is None:
            player = Player.objects.create(
                first_name=row.first_name,
                last_name=row.last_name,
                position=row.position,
                team=team,
                bbde_id=row.bbde_id,
                is_rookie=row.is_rookie,
            )
            run.created += 1
            run.created_players.append(f"{row.display_name} ({row.team_name})")
        else:
            run.matched += 1
            _update_player(run, player, row)
        claimed.add(player.pk)

        if not row.games_consistent:
            run.warnings.append(
                f"{row.display_name}: games played could not be derived exactly "
                f"(FP {row.total_fp}, FP/G {row.fp_per_game})."
            )
        record_snapshot(
            player,
            season,
            as_of,
            salary=row.salary,
            total_fp=row.total_fp,
            games_played=row.games_played,
            team=team,
            position=row.position,
        )

    _inactivate_missing(run, claimed)


def _update_player(run, player, row):
    changed = []
    if player.bbde_id is None and row.bbde_id is not None:
        player.bbde_id = row.bbde_id
        changed.append("bbde_id")
    if player.is_rookie != row.is_rookie:
        player.is_rookie = row.is_rookie
        changed.append("is_rookie")
    if not player.is_active:
        player.is_active = True
        changed.append("is_active")
        run.reactivated += 1
    if changed:
        player.save(update_fields=[*changed, "updated_at"])


def _inactivate_missing(run, seen):
    previous = (
        ImportRun.objects.filter(status=ImportRun.Status.SUCCEEDED, dry_run=False)
        .exclude(pk=run.pk)
        .first()
    )
    if previous and previous.rows and run.rows < previous.rows * INACTIVATION_MIN_SHARE:
        run.warnings.append(
            f"Only {run.rows} rows were read, against {previous.rows} last time, "
            "so no player was marked inactive."
        )
        return
    run.inactivated = (
        Player.objects.filter(is_active=True, bbde_id__isnull=False)
        .exclude(pk__in=seen)
        .update(is_active=False, updated_at=timezone.now())
    )
