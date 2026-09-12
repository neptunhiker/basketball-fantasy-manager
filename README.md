# Basketball Fantasy Manager

A companion tool for the [US-Manager](https://basketball.de/fantasy-basketball/)
fantasy basketball game on basketball.de.

The game itself is still played there. This app mirrors player data and
salaries, keeps a history of both, and helps with the question that comes up
every Sunday, when the official game reprices everyone: who is worth their
money and who is not?

In the official game a roster is 15 players for at most 60 million dollars of
fantasy salary, with at least five Guards, five Forwards and two Centers.
Salaries are recalculated weekly — which is why a salary is a time series here
and not a single value.

## Stack

| Area | Choice |
| --- | --- |
| Framework | Django 5.2 LTS on Python 3.13 |
| Frontend | Django templates, HTMX, Alpine.js |
| CSS | Tailwind v4 via the standalone CLI (no Node) |
| Database | PostgreSQL |
| Auth | Custom user model, email instead of username, UUID primary keys |
| Access | Invitation only, no public sign-up |
| Email | Resend via django-anymail |
| Background jobs | Django-Q2 with Postgres as the broker |
| Deployment | Fly.io, Docker, WhiteNoise for static files |
| Tooling | uv, ruff, pytest-django, pre-commit |

## Running it locally

Prerequisites: [uv](https://docs.astral.sh/uv/) and Docker.

```bash
cp .env.example .env          # adjust SECRET_KEY if you like
docker compose up -d db       # Postgres on port 5433
uv sync
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py seed_teams
./scripts/tailwind.sh build   # downloads the Tailwind CLI on first run
uv run python manage.py runserver
```

The app then runs on http://127.0.0.1:8000.

While working on templates, a second terminal pays off:

```bash
./scripts/tailwind.sh watch
```

> The host port is 5433, not 5432 — 5432 already serves another local
> project's database.

`static/css/app.css` is generated and is not in the repository. After a fresh
clone the pages are therefore unstyled until `./scripts/tailwind.sh build` has
run once.

Emails are printed to the console locally rather than sent, so the invitation
link shows up in the development server's log.

## Tests and linting

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
uv run pre-commit install     # once
```

The test database is reused (`--reuse-db`). After changing a migration, run
once with `--create-db`.

## Project structure

```
config/          Settings (base/dev/prod), URLs, WSGI/ASGI
apps/accounts/   User model, invitations, sign-in
apps/core/       Dashboard, health check, shared building blocks
apps/nba/        Teams and players — the real league, no game logic
apps/fantasy/    The game layer — seasons, rosters, transactions, snapshots
assets/          Tailwind source (build input, not a static file)
static/          Served assets; css/app.css is generated
templates/       base → app.html (signed in) / auth_base.html (signed out)
tests/           pytest suite
```

The split follows where the data comes from: `apps/nba` holds facts about the
league, `apps/fantasy` holds the game played on top of it, and a later
`apps/ingest` will wrap the scraping of basketball.de. `fantasy` may import
from `nba`; nothing in `nba` knows `fantasy` exists.

## Teams and players

The 30 franchises are reference data and hard-coded rather than scraped:

```bash
uv run python manage.py seed_teams    # repeatable as often as you like
```

Players will come from the import. They are never deleted, only retired via
`is_active`, so their history stays resolvable.

For a database you can click around in before the importer exists:

```bash
uv run python manage.py seed_demo_data    # refuses to run unless DEBUG
```

## Managers and rosters

A roster belongs to a `Manager`, not directly to a login. The two are separate
because a `User` is a person who signs in, while a `Manager` is a way of
playing — a handle, and the rosters played under it. One account may keep
several profiles, and does not get one until it builds a first roster: a
manager is someone who manages something.

Scoping is per account rather than per profile. `OwnRosterMixin` matches
`manager__user`, so any roster of yours opens whichever profile holds it, and
someone else's UUID is a 404. Roster names are unique per profile and season,
so two of your own profiles may each keep a `Roster 1`.

**Managers** in the sidebar lists the account's profiles with the rosters
played under each, and adds, renames and removes them. A nickname is unique per
account and the check is case-insensitive — stricter than
`unique_nickname_per_user`, which compares exactly, because the point of the
constraint is that two profiles can be told apart on screen.

Deleting a profile that still plays rosters is **refused**, not warned about:
`Roster.manager` cascades, and "delete this handle" is not the same intention
as "destroy a season of roster history". The rosters go first, through the
dialog that says what that costs. A profile with nothing under it deletes on a
plain confirm, with no word to type — friction that guards nothing only teaches
people not to read.

The build page names the profile back: *Managed by <handle>* in the header
bar beside the roster name, linking to **Managers**. It is there because
nothing else on that screen said whose roster it was, and with several profiles
on one account that is not something being signed in tells you. Below `md` the
bar is already full, so the line steps down into the content block — the two
are exclusive by breakpoint, never both and never neither. The roster cards
on **Rosters** say it too, sharing the subtitle with the season rather than
taking a row each.

`services.manager_for` returns the oldest profile, which is still a
placeholder — the day the app has a notion of viewing *as* one profile, that
choice should come from the request rather than from age.

## Transactions and the ledger

A roster is 15 players inside the cap. Buying, selling and trading all go
through `apps.fantasy.services`, which writes the membership change, the cash
change and one transaction row in a single atomic block — so the three cannot
drift apart.

`Roster.cash` is a balance, not a derivation: a database check constraint keeps
it at or above zero, which is how the salary cap enforces itself. Every move
records what each side was worth at the time, so `Starting cash + Σ cash flow`
always comes back to the balance on the roster. The **History** page per roster
shows that reconciliation, and says so out loud if it ever fails.

The transaction log is read-only in the Django admin. A mistaken move is
corrected by recording the move that reverses it.

### Editing seasons

**Seasons** is readable by anyone signed in and editable by staff. Staff get a
**New season** button and an edit and delete control per row; everyone else
sees the table alone. The controls being hidden is a courtesy — each view
carries `StaffRequiredMixin`, so a non-staff request gets a 403 whether or not
a button was rendered.

Ticking *the season the app works with* hands the flag over rather than adding
a second holder: `only_one_current_season` is a partial unique constraint, so
two current seasons is an IntegrityError, not a state. That is also why
`is_current` is declared on `SeasonForm` instead of coming from the model —
Django validates a model's constraints during form validation, and an instance
carrying the flag while another season still holds it would be rejected before
`save` could do the handover. Off the instance until then, the illegal
intermediate state never exists to be validated.

Deleting has three answers, and the model decides all three rather than the
view inventing a policy. `Roster.season` is PROTECT, so a season holding
rosters cannot go at all — the dialog says so and offers no button.
`PlayerSnapshot.season` is CASCADE, so a season holding only prices *can* go
and takes the whole salary history for that year with it — that one asks for
`DELETE` to be typed. A season holding neither costs a row, and gets a plain
confirm.

`apps.core.views.ModalFormView` is the machinery: the third modal view beside
`PromptView` and `TypedConfirmView`, for a record with more fields than a
prompt can ask about. It renders every field through the same
`partials/form_field.html` the full-page forms use.

### When signings close

`Season.signings_close_at` is set on that form, which is the only place in the
app it can be set. After that moment a roster in that season can only change
through **trades**: buying and selling are refused, and no new roster can be
started.
Null — the default for every season — means signings stay open all year. The
cutoff instant itself counts as closed.

A moment rather than a boolean flag, and `Season.signings_open` computes from
it the way `timing` computes from `starts_on`. That means it can be set weeks
in advance, nobody has to be present to flip anything, and the log can be
audited against the rule afterwards — none of which a flag could do.

`services._require_open_signings` is the enforcement, called inside `buy` and
`sell` after the row lock, so a cutoff passing mid-request cannot let one last
purchase through behind it. `services.create_roster` exists for the same
reason: roster creation is not a balance change and needed no service of its
own, but the rule deserved one home rather than living in a view.

The consequence is deliberate and stated on screen before the deadline, not
after: **a trade swaps one player for one player**, so a roster short of 15 when
signings close stays short for the season. The roster card shows a *Short*
badge instead of *Building*, the position panel says the gap can no longer be
filled, and the rules page names the deadline while it is still ahead.

Trading itself is not restricted to a weekday. Salaries reprice weekly on
Sundays, which is why Sunday is when the decisions get interesting, but a trade
can be made any day.

## Accounts and invitations

There is no public sign-up. Team members (`is_staff`) invite people under
**Users → Invite**. The invited person gets a signed link, sets a password
there, and is signed in afterwards.

The link is valid for 14 days by default (`INVITATION_TIMEOUT_DAYS`) and
expires as soon as a password has been set. Password reset links expire after
three days.

**Profile** is where someone edits their own record: first name, last name and
the email address they sign in with. The address is the credential, so it is
required and checked case-insensitively against every other account — sign-in
lowercases the domain, so two rows differing only in case would compete for the
same login. Changing it does not sign you out: Django's session hash is built
from the password.

Role and membership date are read-only there. `is_staff` is not a field on that
form, so it cannot be granted to oneself.

## Deploying to Fly.io

Once:

```bash
fly launch --no-deploy            # adjust the app name in fly.toml if needed
fly postgres create               # or: managed Postgres in the dashboard
fly postgres attach <db-name>     # sets DATABASE_URL

fly secrets set \
  SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
  RESEND_API_KEY="re_..." \
  ALLOWED_HOSTS="fantasy.example.com" \
  DEFAULT_FROM_EMAIL="Basketball Fantasy Manager <noreply@example.com>"
```

Then:

```bash
fly deploy
fly ssh console -C "python manage.py createsuperuser"
```

`fly deploy` runs the migrations as its `release_command` before new machines
take traffic. Two processes are defined: `app` (Gunicorn) and `worker`
(Django-Q2).

## Open points

- The basketball.de importer is still missing. Two open questions there:
  whether their pages expose a stable player ID — without one, matching has to
  go by name, and names arrive misspelled and inconsistent — and whether the
  fantasy scoring formula is published anywhere.
- No scoring, standings, per-game logs or optimizer layer yet.
- The sending domain has to be verified in Resend, or invitations land in spam.
- Internal identifiers are still called `crossover-manager` (Fly app, package
  name, database name). That is deliberate — renaming the Fly app would change
  the deployment URL.
