"""How the app talks to Postgres."""

import pytest
from django.db import connection


@pytest.mark.django_db
def test_queries_are_never_jit_compiled():
    """JIT compiled the player list's query for over two seconds on every
    request, to then run it in 50 ms. Off for every connection the app opens."""
    with connection.cursor() as cursor:
        cursor.execute("SHOW jit")
        assert cursor.fetchone()[0] == "off"
