"""Static checks over every template, rendered or not.

The one here exists because of a real escape: `{# ... #}` is a *single-line*
comment in Django, so an opening `{#` whose `#}` is on a later line is not a
comment at all -- the template engine passes it through as text and the prose
lands on the page. Nothing in the app fails, no test renders every template,
and the only symptom is words on a screen someone has to notice.

Checked statically rather than by rendering, so a template no test exercises is
covered too, and a new one is covered the moment it is written.
"""

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _template_files():
    return sorted(TEMPLATES.rglob("*.html"))


def test_there_are_templates_to_check():
    """Guards the guard: an empty sweep would pass every test below in silence."""
    assert len(_template_files()) > 20


@pytest.mark.parametrize("path", _template_files(), ids=lambda p: p.name)
def test_no_comment_spans_more_than_its_line(path):
    """`{# ... #}` must open and close on one line, or it is not a comment.

    Multi-line commentary is spelled `{% comment %} ... {% endcomment %}`, which
    the engine does understand across lines.
    """
    source = path.read_text()
    offenders = []

    for match in re.finditer(r"\{#", source):
        line_end = source.find("\n", match.start())
        line = source[match.start() : line_end if line_end != -1 else len(source)]
        if "#}" not in line:
            offenders.append((source[: match.start()].count("\n") + 1, line.strip()[:60]))

    assert not offenders, (
        f"{path.relative_to(TEMPLATES.parent)} has {{# #}} comments that do not close on "
        f"their own line, so the text renders onto the page: {offenders}. "
        f"Use one {{# #}} per line, or {{% comment %}}...{{% endcomment %}}."
    )
