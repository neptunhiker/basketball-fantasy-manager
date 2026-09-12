"""Static guards over the chrome that changes shape with the viewport.

Both rules here come from bugs that shipped and were only found by measuring a
real browser at real widths -- neither breaks a page, raises anything, or fails
any other test. They are asserted statically so a template nobody screenshots
is covered too.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"

COMMENT = re.compile(r"\{#.*?#\}")
# A label the small screen hides, leaving whatever icon sits beside it.
HIDDEN_LABEL = re.compile(r'<span class="hidden sm:(?:inline|block|flex)[^"]*">')
CLICKABLE_OPEN = re.compile(r"<(?:button|a)\b[^>]*>", re.S)
NEGATIVE_VERTICAL_MARGIN = re.compile(r'class="[^"]*?(-m[bt]-[\w.[\]]+|-my-[\w.[\]]+)')


def _templates():
    return sorted(TEMPLATES.rglob("*.html"))


def _uncommented(path):
    """The template with its `{# #}` comments removed.

    The comments explain these very rules, so scanning them would have every
    template that documents the trap fail the test for it.
    """
    return COMMENT.sub("", path.read_text())


def test_there_are_templates_to_check():
    """Guards the guard: an empty sweep passes everything below in silence."""
    assert len(_templates()) > 20


# --- an icon that replaces a word has to say the word ---


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_a_label_hidden_on_small_screens_leaves_an_accessible_name(path):
    """`hidden sm:inline` on a button's text must leave an `aria-label` behind.

    The bar on the roster page carries seven controls. At 390px the roster name
    was down to 38px and at 320px to nothing at all, so `Trade`, `History` and
    `Sign out` became icons below `sm`. That is fine for the eye and silent for
    everything else: with the words display:none, a screen reader reaching the
    button finds an `<svg aria-hidden>` and no name whatsoever.

    Checked by finding the nearest clickable element opening before the hidden
    label, which is the element whose accessible name the label was providing.
    """
    source = _uncommented(path)
    offenders = []

    for match in HIDDEN_LABEL.finditer(source):
        opens = list(CLICKABLE_OPEN.finditer(source[: match.start()]))
        if not opens:
            continue  # A hidden label that is not inside a control at all.
        tag = opens[-1].group(0)
        if "aria-label" not in tag:
            line = source[: match.start()].count("\n") + 1
            offenders.append((line, " ".join(tag.split())[:70]))

    assert not offenders, (
        f"{path.relative_to(ROOT)} hides a control's text below `sm` without an "
        f"aria-label, so the icon that replaces it has no accessible name: {offenders}"
    )


# --- a negative vertical margin does not trim a `space-y` gap ---


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_negative_vertical_margins(path):
    """Tailwind v4 makes these silently destructive, so the repo does without.

    v4 compiles `space-y-6` to a `margin-block-end` on each non-last child,
    inside a `:where()` that carries no specificity. Any `mb-*` on such a child
    therefore *replaces* the gap instead of adjusting it -- `-mb-2`, written to
    pull an element closer to the card below it, set the gap to minus eight
    pixels and the card rendered on top of the text. Nothing errors; the words
    are simply covered.

    A blanket rule because the damage depends on the parent, which is not
    visible from the element's own class list. If a negative vertical margin is
    ever genuinely needed, the honest move is to name the exception here.
    """
    source = _uncommented(path)
    offenders = [
        (source[: m.start()].count("\n") + 1, m.group(1))
        for m in NEGATIVE_VERTICAL_MARGIN.finditer(source)
    ]

    assert not offenders, (
        f"{path.relative_to(ROOT)} uses a negative vertical margin: {offenders}. "
        f"Inside a `space-y-*` parent this replaces the gap rather than trimming "
        f"it. Use a positive `mb-*`/`mt-*`, which overrides it to a real value."
    )
