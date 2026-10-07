"""The German catalog is complete, and stays complete.

A string marked for translation but missing from `django.po` shows up in
English on the German screens without any error, so these checks are what
notices it.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import polib
import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import translation

from apps.core.dates import local_date, local_moment

CATALOG = Path(settings.BASE_DIR) / "locale" / "de" / "LC_MESSAGES" / "django.po"
# What makemessages reads; copied so the check never rewrites the real catalog.
SOURCES = ["apps", "config", "templates", "locale", "manage.py"]


def missing_entries(po):
    return [
        entry.msgid
        for entry in po
        if not entry.obsolete and (not entry.translated() or "fuzzy" in entry.flags)
    ]


def test_every_entry_in_the_german_catalog_is_translated():
    assert missing_entries(polib.pofile(str(CATALOG))) == []


def test_the_german_catalog_says_cash_not_bargeld():
    po = polib.pofile(str(CATALOG))
    texts = []
    for entry in po:
        texts += [entry.msgstr, *entry.msgstr_plural.values()]

    assert not [text for text in texts if "bargeld" in text.lower()]


@pytest.mark.skipif(shutil.which("xgettext") is None, reason="needs GNU gettext")
def test_every_marked_string_is_in_the_catalog(tmp_path):
    """Re-extract into a copy: a new `_()` or `{% translate %}` without a German
    entry fails here instead of reaching the German screens in English."""
    root = Path(settings.BASE_DIR)
    for name in SOURCES:
        source = root / name
        if source.is_dir():
            shutil.copytree(source, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, tmp_path / name)
    if (root / ".env").exists():
        shutil.copy2(root / ".env", tmp_path / ".env")

    subprocess.run(
        [sys.executable, "manage.py", "makemessages", "-l", "de", "--no-obsolete"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    po = polib.pofile(str(tmp_path / "locale" / "de" / "LC_MESSAGES" / "django.po"))
    assert missing_entries(po) == []


@override_settings(TIME_ZONE="Europe/Berlin")
def test_dates_in_messages_follow_the_language():
    import datetime as dt
    from zoneinfo import ZoneInfo

    moment = dt.datetime(2026, 10, 20, 18, 0, tzinfo=ZoneInfo("Europe/Berlin"))

    with translation.override("en"):
        assert local_date(moment) == "20 October 2026"
        assert local_moment(moment) == "20 October 2026 at 18:00"
    with translation.override("de"):
        assert local_date(moment) == "20. Oktober 2026"
        assert local_moment(moment) == "20. Oktober 2026 um 18:00 Uhr"


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_page_names_its_language(client, user, password, language):
    user.language = language
    user.save(update_fields=["language"])
    client.login(email=user.email, password=password)

    assert f'<html lang="{language}"' in client.get("/").text
