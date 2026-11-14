"""Deploys: recorded by `record_deploy` once a deploy is live."""

import datetime as dt
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.perf.models import Deploy

SHA = "4b45e6e27c8965322aa1ff6828f7fb7a007331e7"


def record(*args):
    out = StringIO()
    call_command("record_deploy", *args, stdout=out)
    return out.getvalue()


def test_a_deploy_is_recorded_with_its_commit(db):
    record(SHA, "Perf: keep request metrics for 60 days")

    deploy = Deploy.objects.get()
    assert deploy.sha == SHA
    assert deploy.short_sha == "4b45e6e"
    assert deploy.subject == "Perf: keep request metrics for 60 days"
    assert timezone.now() - deploy.deployed_at < dt.timedelta(seconds=5)
    assert str(deploy) == "4b45e6e Perf: keep request metrics for 60 days"


def test_deploying_the_same_commit_again_records_nothing(db):
    record(SHA, "First")

    out = record(SHA[:7], "Again")

    assert Deploy.objects.count() == 1
    assert "already the latest" in out


def test_a_commit_deployed_before_counts_again_after_another(db):
    record(SHA)
    record("d1ca3a03421779e0e2b4cf8d478094142dee3afe")
    record(SHA)

    assert Deploy.objects.count() == 3


def test_the_subject_is_optional_and_tidied(db):
    record(SHA.upper(), "  Two   spaces\nand a newline  ")
    record("d1ca3a0")

    first, second = Deploy.objects.order_by("deployed_at")
    assert first.sha == SHA
    assert first.subject == "Two spaces and a newline"
    assert second.subject == ""


@pytest.mark.parametrize("sha", ["", "main", "4b45e6", "4b45e6e!", "g" * 40])
def test_anything_but_a_commit_hash_is_refused(db, sha):
    with pytest.raises(CommandError):
        record(sha)

    assert not Deploy.objects.exists()


def test_a_subject_that_looks_like_an_option_survives_the_double_dash(db):
    """deploy.sh passes `--` first, so "-v2 ..." is a subject, not an option."""
    record("--", SHA, "-v2 of the chart")

    assert Deploy.objects.get().subject == "-v2 of the chart"
