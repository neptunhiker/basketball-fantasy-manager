# Crossover Manager

Django-Anwendung zur Verwaltung von Coach–Teilnehmenden-Matchings.

## Stack

| Bereich | Wahl |
| --- | --- |
| Framework | Django 5.2 LTS auf Python 3.13 |
| Frontend | Django-Templates, HTMX, Alpine.js |
| CSS | Tailwind v4 über die Standalone-CLI (kein Node) |
| Datenbank | PostgreSQL |
| Auth | Eigenes User-Modell, E-Mail statt Username, UUID-Primärschlüssel |
| Zugang | Nur per Einladung, kein öffentliches Registrieren |
| E-Mail | Resend über django-anymail |
| Hintergrundjobs | Django-Q2 mit Postgres als Broker |
| Deployment | Fly.io, Docker, WhiteNoise für Static Files |
| Werkzeuge | uv, ruff, pytest-django, pre-commit |

## Lokal starten

Voraussetzungen: [uv](https://docs.astral.sh/uv/) und Docker.

```bash
cp .env.example .env          # SECRET_KEY bei Bedarf anpassen
docker compose up -d db       # Postgres auf Port 5433
uv sync
uv run python manage.py migrate
uv run python manage.py createsuperuser
./scripts/tailwind.sh build   # lädt beim ersten Mal die Tailwind-CLI nach
uv run python manage.py runserver
```

Die App läuft dann auf http://127.0.0.1:8000.

Während der Arbeit an Templates lohnt sich ein zweites Terminal:

```bash
./scripts/tailwind.sh watch
```

> Der Host-Port ist 5433, nicht 5432 — auf 5432 läuft bereits die Datenbank
> eines anderen lokalen Projekts.

E-Mails werden lokal in der Konsole ausgegeben, nicht verschickt. Der
Einladungslink steht also im Log des Entwicklungsservers.

## Tests und Linting

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
uv run pre-commit install     # einmalig
```

## Projektstruktur

```
config/          Settings (base/dev/prod), URLs, WSGI/ASGI
apps/accounts/   User-Modell, Einladungen, Anmeldung
apps/core/       Dashboard, Health-Check, geteilte Bausteine
assets/          Tailwind-Quelle (Build-Input, kein Static File)
static/          Ausgelieferte Assets; css/app.css wird generiert
templates/       base → app.html (angemeldet) / auth_base.html (abgemeldet)
tests/           pytest-Suite
```

## Konten und Einladungen

Es gibt keine öffentliche Registrierung. Team-Mitglieder (`is_staff`) laden
Personen unter **Team → Einladen** ein. Die eingeladene Person bekommt einen
signierten Link, setzt dort ihr Passwort und ist danach angemeldet.

Der Link ist standardmäßig 14 Tage gültig (`INVITATION_TIMEOUT_DAYS`) und
verfällt, sobald ein Passwort gesetzt wurde. Passwort-Reset-Links laufen nach
drei Tagen ab.

## Deployment auf Fly.io

Einmalig:

```bash
fly launch --no-deploy            # ggf. App-Namen in fly.toml anpassen
fly postgres create               # oder: Managed Postgres im Dashboard
fly postgres attach <db-name>     # setzt DATABASE_URL

fly secrets set \
  SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')" \
  RESEND_API_KEY="re_..." \
  ALLOWED_HOSTS="crossover.example.com" \
  DEFAULT_FROM_EMAIL="Crossover Manager <noreply@example.com>"
```

Danach:

```bash
fly deploy
fly ssh console -C "python manage.py createsuperuser"
```

`fly deploy` führt die Migrationen als `release_command` aus, bevor neue
Maschinen Traffic bekommen. Zwei Prozesse sind definiert: `app` (Gunicorn) und
`worker` (Django-Q2).

## Offene Punkte

- Die Domain für den Versand muss in Resend verifiziert werden, sonst landen
  Einladungen im Spam.
- Die Matching-Domänenmodelle (Coaches, Teilnehmende, Zuordnungen) fehlen noch.
