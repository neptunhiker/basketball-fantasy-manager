"""The search boxes' query, read the same way on every page.

A single letter matches nearly every player, so it is not searched at all:
the list stays as it is until a second letter arrives. The search boxes skip
a lone letter themselves (see the `hx-trigger` filters in the templates); this
is the rule behind them, for the request that still gets through -- typing
"le" and deleting back to "l" before the delay is up sends "l".
"""

SEARCH_MIN_LENGTH = 2


def search_query(data, key="q"):
    """The trimmed query from `data` (a GET or POST), or "" when it is too short."""
    query = data.get(key, "").strip()
    return query if len(query) >= SEARCH_MIN_LENGTH else ""
