"""Everything clickable has to show the hand.

Tailwind v3's Preflight set `button { cursor: pointer }`; v4 dropped it, and
every button in the app quietly went back to the browser's arrow -- the kind of
regression that a version bump reintroduces without touching a single line of
our own code. So the rule is asserted here rather than trusted.

Two halves, because the invariant has two ways of breaking: the stylesheet can
stop carrying the rule, and a template can grow a clickable element the rule
was never written to cover.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STYLESHEET = ROOT / "static" / "css" / "app.css"
TEMPLATES = ROOT / "templates"

# What makes an element clickable in this codebase: an HTMX request, an Alpine
# handler, or a plain inline handler.
CLICKABLE = re.compile(r"\bhx-(get|post|put|patch|delete)\b|@click|x-on:click|\bonclick\b")

# Tags the stylesheet's selector list already reaches, plus the ones the browser
# has always given a hand to unprompted (`a`, and `form` is never the target).
COVERED_TAGS = {"a", "button", "form", "input", "label", "select", "summary"}

# `<div class="modal-backdrop">` in partials/modal.html and the mobile-nav scrim
# in app.html. Both dismiss on click, and both are deliberately left as the
# arrow: a hand tracking across a full-screen overlay claims there is something
# at that point to click, when the whole region does the same one thing.
BACKDROPS = {("app.html", "navOpen = false"), ("modal.html", "open = false")}

OPENING_TAG = re.compile(r"<([a-zA-Z][\w-]*)((?:[^<>]|\{%[^%]*%\}|\{\{[^}]*\}\})*?)>", re.S)


def clickable_elements():
    """Every element in every template that responds to a click, as
    (path, line, tag, attributes)."""
    for path in sorted(TEMPLATES.rglob("*.html")):
        source = path.read_text()
        for match in OPENING_TAG.finditer(source):
            tag, attributes = match.group(1).lower(), match.group(2)
            if CLICKABLE.search(attributes):
                line = source[: match.start()].count("\n") + 1
                yield path, line, tag, attributes


# --- the stylesheet carries the rule ---


@pytest.fixture(scope="module")
def stylesheet():
    if not STYLESHEET.exists():
        # `static/css/` is gitignored -- the stylesheet is built, not committed.
        # On a fresh clone that means "nobody has run the build yet", which is
        # not the same finding as "the rule is gone", so skip instead of fail.
        pytest.skip("static/css/app.css is built by ./scripts/tailwind.sh build")
    return STYLESHEET.read_text()


@pytest.mark.parametrize(
    "selector",
    ["button", "[role=button]", "summary", "select", "label:has(input[type=checkbox])"],
)
def test_the_pointer_rule_covers(selector, stylesheet):
    """Each selector appears in a declaration block that sets `cursor:pointer`.

    Matched against the minified output, which is what the browser is actually
    served -- asserting on `assets/input.css` would pass while a build failure
    left the page with the old stylesheet.
    """
    blocks = [
        block
        for block in re.findall(r"([^{}]*)\{cursor:pointer\}", stylesheet)
        if selector in block
    ]
    assert blocks, f"nothing gives {selector} a pointer cursor"


def test_a_disabled_control_does_not_promise_a_click(stylesheet):
    """The counterpart, and the reason the rule is not simply `*:hover`.

    Read from the built CSS the same way: a hand on a control that will not
    respond is a lie, so `:disabled` gets `not-allowed` instead.
    """
    blocks = re.findall(r"([^{}]*)\{cursor:not-allowed\}", stylesheet)
    assert any(":disabled" in block for block in blocks), (
        "no not-allowed rule for disabled controls"
    )


def test_no_template_hand_rolls_a_cursor():
    """`cursor-*` utilities in templates are allowed but should stay rare.

    The base rule is meant to be the single answer, so a utility appearing is a
    sign that either the rule missed a case or someone did not know it was
    there. None are needed today; this fails loudly if one creeps back without
    the rule above being reconsidered.
    """
    offenders = [
        f"{path.relative_to(ROOT)}"
        for path in TEMPLATES.rglob("*.html")
        if re.search(r'class="[^"]*\bcursor-(pointer|default)\b', path.read_text())
    ]
    assert not offenders, f"hand-rolled cursor utilities in {offenders}"


# --- no template outgrows the rule ---


def test_every_clickable_element_is_one_the_rule_reaches():
    """A clickable `<div>` or `<span>` gets no hand from anything above.

    This is the half that catches new markup rather than a new Tailwind: the
    stylesheet can be perfectly correct and still miss an element that was
    never a button in the first place.
    """
    uncovered = []
    for path, line, tag, attributes in clickable_elements():
        if tag in COVERED_TAGS or 'role="button"' in attributes:
            continue
        if any(path.name == name and marker in attributes for name, marker in BACKDROPS):
            continue
        uncovered.append(f"{path.relative_to(ROOT)}:{line} <{tag}>")

    assert not uncovered, (
        "clickable elements the cursor rule does not reach -- make them a "
        f'<button>, give them role="button", or add them to the rule: {uncovered}'
    )


def test_the_sweep_actually_finds_the_buttons():
    """Guards the guard.

    The test above passes trivially if the regex stops matching anything, so
    pin the count down. The floor sits below the 18 found today -- it is there
    to catch the sweep breaking, not to freeze the button count, and the plain
    `type="submit"` buttons are not among them: they carry no handler
    attribute, so this only ever sees the HTMX and Alpine ones.
    """
    tags = [tag for _, _, tag, _ in clickable_elements()]
    assert tags.count("button") >= 15, f"only found {tags.count('button')} clickable buttons"
